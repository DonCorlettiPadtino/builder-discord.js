import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import asyncio
import re
from collections import defaultdict
import os

# --- Configuración de Intents ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
intents.messages = True

# --- Inicialización del bot ---
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Configuración del Anti-Raid ---
join_records = {}
MAX_JOINS = 5
TIME_WINDOW = 10
LOG_CHANNEL_ID = 123456789012345678  # Reemplaza con tu canal

async def log_event(guild, message):
    channel = guild.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(f"{message}")

@bot.event
async def on_ready():
    print(f"Bot listo como: {bot.user}")

@bot.event
async def on_member_join(member):
    guild_id = member.guild.id
    now = datetime.utcnow()
    if guild_id not in join_records:
        join_records[guild_id] = []
    join_records[guild_id].append(now)
    join_records[guild_id] = [t for t in join_records[guild_id] if now - t < timedelta(seconds=TIME_WINDOW)]
    if len(join_records[guild_id]) >= MAX_JOINS:
        try:
            await member.ban(reason="Raid detectado")
            await log_event(member.guild, f"Miembro baneado por raid: {member.name}")
        except Exception as e:
            print(f"Error al banear: {e}")

@bot.event
async def on_member_remove(member):
    await log_event(member.guild, f"{member.name} salió o fue expulsado.")

@bot.command()
@commands.has_permissions(administrator=True)
async def check_joins(ctx):
    count = len(join_records.get(ctx.guild.id, []))
    await ctx.send(f"Uniones recientes: {count} en los últimos {TIME_WINDOW} segundos")

# --- Seguridad por Audit Logs ---
WHITELIST = [989700706283978803]
MAX_ACTIONS = 3
TIME_LIMIT = 10

action_records = {
    "ban": {},
    "channel_delete": {},
    "role_delete": {},
    "channel_create": {},
    "role_create": {},
}

async def check_audit(guild, action_type, limit_reason):
    try:
        async for entry in guild.audit_logs(limit=1, action=action_type):
            user = entry.user
            if user.bot or user.id in WHITELIST:
                return
            now = datetime.utcnow()
            if user.id not in action_records[limit_reason]:
                action_records[limit_reason][user.id] = []
            action_records[limit_reason][user.id] = [
                t for t in action_records[limit_reason][user.id] if now - t < timedelta(seconds=TIME_LIMIT)
            ]
            action_records[limit_reason][user.id].append(now)
            if len(action_records[limit_reason][user.id]) > MAX_ACTIONS:
                await punish_user(guild, user, f"Exceso de {limit_reason}")
    except Exception as e:
        print(f"[ERROR AUDIT] {e}")

async def punish_user(guild, user, reason):
    try:
        await guild.ban(user, reason=reason)
        await log_event(guild, f"{user.name} baneado por: {reason}")
    except Exception as e:
        await log_event(guild, f"Error al sancionar a {user.name}: {e}")

@bot.event
async def on_guild_channel_delete(channel):
    await check_audit(channel.guild, discord.AuditLogAction.channel_delete, "channel_delete")

@bot.event
async def on_guild_channel_create(channel):
    await check_audit(channel.guild, discord.AuditLogAction.channel_create, "channel_create")

@bot.event
async def on_guild_role_delete(role):
    await check_audit(role.guild, discord.AuditLogAction.role_delete, "role_delete")

@bot.event
async def on_guild_role_create(role):
    await check_audit(role.guild, discord.AuditLogAction.role_create, "role_create")

@bot.event
async def on_member_ban(guild, user):
    await check_audit(guild, discord.AuditLogAction.ban, "ban")

@bot.command()
@commands.has_permissions(administrator=True)
async def whitelist(ctx, member: discord.Member):
    if member.id not in WHITELIST:
        WHITELIST.append(member.id)
        await ctx.send(f"{member.name} añadido a la whitelist.")

@bot.command()
@commands.has_permissions(administrator=True)
async def show_whitelist(ctx):
    users = [f"<@{uid}>" for uid in WHITELIST]
    await ctx.send("Whitelist:\n" + "\n".join(users))

# --- Antispam y antiflood ---
PROHIBITED_WORDS = ["@everyone", "@here", "discord.gg/", "malas", "palabras"]
MAX_MENTIONS = 5
SPAM_LIMIT = 5
user_messages = defaultdict(list)

def is_spam(user_id, content):
    now = datetime.utcnow()
    user_messages[user_id] = [
        (msg, t) for msg, t in user_messages[user_id] if now - t < timedelta(seconds=10)
    ]
    user_messages[user_id].append((content, now))
    repeated = sum(1 for msg, _ in user_messages[user_id] if msg == content)
    return repeated >= SPAM_LIMIT

def contains_prohibited(content):
    content = content.lower()
    return any(word in content for word in PROHIBITED_WORDS)

def contains_invite(content):
    return re.search(r"(discord\\.gg/|discord\\.com/invite/)", content)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if contains_invite(message.content) or contains_prohibited(message.content):
        try:
            await message.delete()
            await log_event(message.guild, f"Mensaje eliminado por contenido prohibido de {message.author.name}")
        except:
            pass
    if len(message.mentions) >= MAX_MENTIONS:
        try:
            await message.delete()
            await punish_user(message.guild, message.author, "Menciones masivas")
        except:
            pass
    if is_spam(message.author.id, message.content):
        try:
            await message.delete()
            await punish_user(message.guild, message.author, "Spam detectado")
        except:
            pass
    await bot.process_commands(message)

# --- Protección del nombre e icono del servidor ---
server_name_cache = {}
server_icon_cache = {}

@bot.event
async def on_guild_update(before, after):
    if before.id not in server_name_cache:
        server_name_cache[before.id] = before.name
        server_icon_cache[before.id] = before.icon
    if before.name != after.name:
        await after.edit(name=server_name_cache[before.id])
        await log_event(after, "Nombre del servidor restaurado.")
    if before.icon != after.icon:
        await after.edit(icon=server_icon_cache[before.id])
        await log_event(after, "Icono del servidor restaurado.")

@bot.command()
@commands.has_permissions(administrator=True)
async def add_badword(ctx, *, word):
    if word not in PROHIBITED_WORDS:
        PROHIBITED_WORDS.append(word)
        await ctx.send(f"Palabra añadida: `{word}`")

@bot.command()
@commands.has_permissions(administrator=True)
async def show_badwords(ctx):
    await ctx.send("Palabras prohibidas:\n" + ", ".join(PROHIBITED_WORDS))

import discord
from discord.ext import commands
import asyncio

MODS = []  # IDs de usuarios con permisos especiales

@bot.command()
async def mod(ctx, member: discord.Member):
    if ctx.author.guild_permissions.administrator:
        if member.id not in MODS:
            MODS.append(member.id)
            await ctx.send(f"✅ {member.name} ahora es moderador.")
        else:
            await ctx.send("⚠️ Este usuario ya es moderador.")
    else:
        await ctx.send("❌ No tienes permisos para usar este comando.")

@bot.command()
async def show_mods(ctx):
    if not MODS:
        await ctx.send("❌ No hay moderadores registrados.")
        return
    users = [f"<@{uid}>" for uid in MODS]
    await ctx.send("🛡️ Moderadores:\n" + "\n".join(users))

@bot.command()
async def mute(ctx, member: discord.Member, duration):
    if ctx.author.id not in MODS:
        return await ctx.send("❌ No tienes permiso para usar este comando.")

    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if not muted_role:
        muted_role = await ctx.guild.create_role(name="Muted")
        for channel in ctx.guild.channels:
            await channel.set_permissions(muted_role, send_messages=False, speak=False)

    time_units = {"s": 1, "m": 60, "h": 3600}
    unit = duration[-1]
    if unit not in time_units:
        return await ctx.send("❌ Usa un formato válido como `10m`, `1h`, etc.")

    try:
        seconds = int(duration[:-1]) * time_units[unit]
    except ValueError:
        return await ctx.send("❌ Duración inválida.")

    await member.add_roles(muted_role)
    await ctx.send(f"🔇 {member.name} fue muteado por {duration}.")
    await asyncio.sleep(seconds)
    await member.remove_roles(muted_role)
    await ctx.send(f"🔊 {member.name} fue desmuteado automáticamente.")

@bot.command()
async def unmute(ctx, member: discord.Member):
    if ctx.author.id not in MODS:
        return await ctx.send("❌ No tienes permiso para usar este comando.")
    muted_role = discord.utils.get(ctx.guild.roles, name="Muted")
    if muted_role and muted_role in member.roles:
        await member.remove_roles(muted_role)
        await ctx.send(f"🔊 {member.name} fue desmuteado.")
    else:
        await ctx.send("❌ Este usuario no está muteado o el rol no existe.")

import json

with open("config.json") as f:
    config = json.load(f)

token = config["TOKEN"]