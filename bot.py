import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")
DB_PATH = os.getenv("DB_PATH", "birthdays.db")
BIRTHDAY_GIF = os.getenv("BIRTHDAY_GIF", "")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env-Datei.")

TZ = ZoneInfo(TIMEZONE)
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

db = sqlite3.connect(DB_PATH)
db.execute("""CREATE TABLE IF NOT EXISTS birthdays(
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 day INTEGER NOT NULL, month INTEGER NOT NULL, year INTEGER,
 PRIMARY KEY(guild_id,user_id))""")
db.execute("""CREATE TABLE IF NOT EXISTS settings(
 guild_id INTEGER PRIMARY KEY,
 channel_id INTEGER,
 role_id INTEGER,
 dm_enabled INTEGER DEFAULT 0,
 role_enabled INTEGER DEFAULT 0,
 ping_enabled INTEGER DEFAULT 1,
 gif_url TEXT DEFAULT ''
)""")
db.execute("""CREATE TABLE IF NOT EXISTS announcements(
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 year INTEGER NOT NULL,
 PRIMARY KEY(guild_id,user_id,year))""")
db.execute("""CREATE TABLE IF NOT EXISTS role_expiry(
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 role_id INTEGER NOT NULL, expires_at TEXT NOT NULL,
 PRIMARY KEY(guild_id,user_id,role_id))""")
db.commit()

def get_bday(g,u):
    return db.execute("SELECT day,month,year FROM birthdays WHERE guild_id=? AND user_id=?",(g,u)).fetchone()

def save_bday(g,u,d,m,y):
    db.execute("""INSERT INTO birthdays VALUES(?,?,?,?,?)
    ON CONFLICT(guild_id,user_id) DO UPDATE SET day=excluded.day,month=excluded.month,year=excluded.year""",(g,u,d,m,y)); db.commit()

def del_bday(g,u):
    db.execute("DELETE FROM birthdays WHERE guild_id=? AND user_id=?",(g,u)); db.commit()

def settings(g):
    row=db.execute("""SELECT channel_id,role_id,dm_enabled,role_enabled,ping_enabled,gif_url
                      FROM settings WHERE guild_id=?""",(g,)).fetchone()
    return row or (None,None,0,0,1,"")

def save_settings(g,**kw):
    old=settings(g)
    vals={
      "channel_id":old[0],"role_id":old[1],"dm_enabled":old[2],
      "role_enabled":old[3],"ping_enabled":old[4],"gif_url":old[5]
    }
    vals.update(kw)
    db.execute("""INSERT INTO settings VALUES(?,?,?,?,?,?,?)
      ON CONFLICT(guild_id) DO UPDATE SET
      channel_id=?,role_id=?,dm_enabled=?,role_enabled=?,ping_enabled=?,gif_url=?""",
      (g,vals["channel_id"],vals["role_id"],vals["dm_enabled"],vals["role_enabled"],vals["ping_enabled"],vals["gif_url"],
       vals["channel_id"],vals["role_id"],vals["dm_enabled"],vals["role_enabled"],vals["ping_enabled"],vals["gif_url"]))
    db.commit()

def parse_date(s):
    p=s.strip().split(".")
    if len(p) not in (2,3): raise ValueError
    d,m=int(p[0]),int(p[1]); y=int(p[2]) if len(p)==3 else None
    datetime(2000,m,d)
    if y is not None and not 1900 <= y <= datetime.now(TZ).year: raise ValueError
    return d,m,y

def next_birthday(day,month,now):
    for year in (now.year, now.year+1):
        try:
            target=datetime(year,month,day,tzinfo=TZ)
        except ValueError:
            target=datetime(year,2,28,tzinfo=TZ)
        if target.date() >= now.date():
            return target
    raise RuntimeError

def countdown(target,now):
    delta=target-now
    days=delta.days
    hours=delta.seconds//3600
    minutes=(delta.seconds%3600)//60
    return days,hours,minutes

async def remove_expired_roles():
    now=datetime.now(TZ)
    rows=db.execute("SELECT guild_id,user_id,role_id,expires_at FROM role_expiry").fetchall()
    for g,u,r,expires in rows:
        try: exp=datetime.fromisoformat(expires)
        except ValueError:
            db.execute("DELETE FROM role_expiry WHERE guild_id=? AND user_id=? AND role_id=?",(g,u,r)); continue
        if exp <= now:
            guild=bot.get_guild(g)
            if guild:
                member=guild.get_member(u); role=guild.get_role(r)
                if member and role:
                    try: await member.remove_roles(role,reason="Geburtstagsrolle abgelaufen")
                    except discord.HTTPException: pass
            db.execute("DELETE FROM role_expiry WHERE guild_id=? AND user_id=? AND role_id=?",(g,u,r))
    db.commit()

async def announce(guild_id,user_id,day,month,year,now):
    ch_id,role_id,dm_enabled,role_enabled,ping_enabled,gif_url=settings(guild_id)
    guild=bot.get_guild(guild_id)
    if not guild: return
    if db.execute("SELECT 1 FROM announcements WHERE guild_id=? AND user_id=? AND year=?",(guild_id,user_id,now.year)).fetchone(): return

    member=guild.get_member(user_id)
    mention=member.mention if member else f"<@{user_id}>"
    a=now.year-year if year else None

    embed=discord.Embed(
        title="🎉🎂 HAPPY BIRTHDAY! 🎂🎉",
        description=f"## Alles Gute, {mention}! 🥳\n\n"
                    + (f"🎈 Heute wirst du **{a} Jahre** alt! 🎈\n\n" if a else "")
                    + "Wir wünschen dir einen wunderschönen Tag! ❤️🎁",
        timestamp=now
    )
    embed.set_footer(text="Birthday Bot • Automatischer Geburtstagsgruß")
    if member and member.display_avatar:
        embed.set_thumbnail(url=member.display_avatar.url)

    guild_gif = gif_url or BIRTHDAY_GIF
    if guild_gif:
        embed.set_image(url=guild_gif)

    content = mention if ping_enabled else None

    if ch_id:
        ch=guild.get_channel(ch_id)
        if ch:
            try: await ch.send(content=content,embed=embed)
            except discord.HTTPException: pass

    if dm_enabled and member:
        try:
            await member.send(embed=embed)
        except discord.HTTPException:
            pass

    if role_enabled and role_id and member:
        role=guild.get_role(role_id)
        if role:
            try:
                await member.add_roles(role,reason="Geburtstag")
                expires=now+timedelta(hours=24)
                db.execute("""INSERT OR REPLACE INTO role_expiry
                    VALUES(?,?,?,?)""",(guild_id,user_id,role_id,expires.isoformat()))
            except discord.HTTPException: pass

    db.execute("INSERT OR IGNORE INTO announcements VALUES(?,?,?)",(guild_id,user_id,now.year))
    db.commit()

@tasks.loop(minutes=1)
async def scheduler():
    now=datetime.now(TZ)
    await remove_expired_roles()
    if now.hour != 0 or now.minute != 0: return
    rows=db.execute("SELECT guild_id,user_id,day,month,year FROM birthdays").fetchall()
    for g,u,d,m,y in rows:
        match=(d==now.day and m==now.month) or (d==29 and m==2 and now.month==2 and now.day==28 and now.year%4!=0)
        if match: await announce(g,u,d,m,y,now)

@scheduler.before_loop
async def before_scheduler(): await bot.wait_until_ready()

GUILD_ID = 926921790326992906
GUILD = discord.Object(id=GUILD_ID)

@bot.event
async def on_ready():
    if not scheduler.is_running(): scheduler.start()
    try:
        # Alte Guild-Commands entfernen und die aktuelle Command-Struktur
        # für den Testserver neu synchronisieren.
        bot.tree.clear_commands(guild=GUILD)
        bot.tree.copy_global_to(guild=GUILD)
        synced=await bot.tree.sync(guild=GUILD)
        print(f"🎂 Birthday Bot online als {bot.user} | {len(synced)} Commands auf Server {GUILD_ID}")
    except Exception as e: print("Sync:",e)

birthday=app_commands.Group(name="birthday",description="🎂 Geburtstage verwalten")

@birthday.command(name="set",description="Geburtstag speichern")
@app_commands.describe(datum="TT.MM oder TT.MM.JJJJ")
async def set_bday(i:discord.Interaction,datum:str):
    try: d,m,y=parse_date(datum)
    except:
        return await i.response.send_message("❌ Ungültig. Beispiel: `15.03` oder `15.03.1995`",ephemeral=True)
    save_bday(i.guild_id,i.user.id,d,m,y)
    await i.response.send_message(f"🎂 Geburtstag **{d:02d}.{m:02d}** gespeichert.",ephemeral=True)

@birthday.command(name="show",description="Eigenen Geburtstag und Countdown anzeigen")
async def show(i):
    r=get_bday(i.guild_id,i.user.id)
    if not r: return await i.response.send_message("❌ Kein Geburtstag gespeichert.",ephemeral=True)
    d,m,y=r; now=datetime.now(TZ); target=next_birthday(d,m,now); days,hours,minutes=countdown(target,now)
    s=f"{d:02d}.{m:02d}"+(f".{y}" if y else "")
    await i.response.send_message(f"🎂 **{s}**\n⏳ Nächster Geburtstag in **{days} Tagen, {hours} Std. und {minutes} Min.**",ephemeral=True)

@birthday.command(name="remove",description="Eigenen Geburtstag löschen")
async def remove(i):
    del_bday(i.guild_id,i.user.id)
    await i.response.send_message("🗑️ Geburtstag gelöscht.",ephemeral=True)

@birthday.command(name="list",description="Geburtstagskalender des Servers")
async def list_b(i):
    rows=db.execute("SELECT user_id,day,month,year FROM birthdays WHERE guild_id=? ORDER BY month,day",(i.guild_id,)).fetchall()
    if not rows: return await i.response.send_message("📭 Noch keine Geburtstage gespeichert.")
    now=datetime.now(TZ); lines=[]
    for u,d,m,y in rows:
        mem=i.guild.get_member(u); name=mem.display_name if mem else f"<@{u}>"
        days=countdown(next_birthday(d,m,now),now)[0]
        lines.append(f"🎂 **{d:02d}.{m:02d}** — {name}  •  ⏳ {days} Tage")
    e=discord.Embed(title="📅 Geburtstagskalender",description="\n".join(lines[:50]))
    e.set_footer(text=f"{len(rows)} Geburtstage gespeichert")
    await i.response.send_message(embed=e)

@birthday.command(name="next",description="Nächsten Geburtstag anzeigen")
async def next_b(i):
    rows=db.execute("SELECT user_id,day,month,year FROM birthdays WHERE guild_id=?",(i.guild_id,)).fetchall()
    if not rows: return await i.response.send_message("📭 Keine Geburtstage vorhanden.")
    now=datetime.now(TZ); item=min(rows,key=lambda x:next_birthday(x[1],x[2],now))
    u,d,m,y=item; mem=i.guild.get_member(u); name=mem.display_name if mem else f"<@{u}>"
    target=next_birthday(d,m,now); days,hours,minutes=countdown(target,now)
    e=discord.Embed(title="🎉 Nächster Geburtstag",description=f"**{name}**\n🎂 {d:02d}.{m:02d}\n⏳ **{days} Tage, {hours} Std. und {minutes} Min.**")
    if mem: e.set_thumbnail(url=mem.display_avatar.url)
    await i.response.send_message(embed=e)

@birthday.command(name="channel",description="Geburtstagskanal festlegen")
@app_commands.describe(kanal="Textkanal")
@app_commands.checks.has_permissions(manage_guild=True)
async def channel(i,kanal:discord.TextChannel):
    save_settings(i.guild_id,channel_id=kanal.id)
    await i.response.send_message(f"✅ Geburtstagskanal: {kanal.mention}")

@birthday.command(name="dm",description="Automatische Geburtstags-DMs an/aus")
@app_commands.describe(aktiv="True = an, False = aus")
@app_commands.checks.has_permissions(manage_guild=True)
async def dm(i,aktiv:bool):
    save_settings(i.guild_id,dm_enabled=int(aktiv))
    await i.response.send_message(f"✅ Geburtstags-DMs: **{'AN' if aktiv else 'AUS'}**")

@birthday.command(name="role",description="Geburtstagsrolle aktivieren (24 Stunden)")
@app_commands.describe(rolle="Rolle, die 24 Stunden vergeben wird")
@app_commands.checks.has_permissions(manage_roles=True)
async def role(i,rolle:discord.Role):
    save_settings(i.guild_id,role_id=rolle.id,role_enabled=1)
    await i.response.send_message(f"🎁 Geburtstagsrolle aktiviert: {rolle.mention}\n⏰ Sie wird nach 24 Stunden automatisch entfernt.")

@birthday.command(name="roleoff",description="Geburtstagsrolle deaktivieren")
@app_commands.checks.has_permissions(manage_roles=True)
async def roleoff(i):
    save_settings(i.guild_id,role_enabled=0)
    await i.response.send_message("✅ Geburtstagsrolle deaktiviert.")

@birthday.command(name="ping",description="User-Mention bei Geburtstagsgrüßen an/aus")
@app_commands.describe(aktiv="True = User wird gepingt")
@app_commands.checks.has_permissions(manage_guild=True)
async def ping(i,aktiv:bool):
    save_settings(i.guild_id,ping_enabled=int(aktiv))
    await i.response.send_message(f"✅ Geburtstags-Ping: **{'AN' if aktiv else 'AUS'}**")

@birthday.command(name="gif",description="GIF für Geburtstags-Embed setzen")
@app_commands.describe(url="Direkte GIF-URL; leer ist nicht möglich, nutze 'off' zum Ausschalten")
@app_commands.checks.has_permissions(manage_guild=True)
async def gif(i,url:str):
    if url.lower()=="off":
        save_settings(i.guild_id,gif_url="")
        return await i.response.send_message("✅ GIF deaktiviert.")
    if not (url.startswith("https://") or url.startswith("http://")):
        return await i.response.send_message("❌ Bitte eine gültige GIF-URL angeben.",ephemeral=True)
    save_settings(i.guild_id,gif_url=url)
    await i.response.send_message("✅ Birthday-GIF gespeichert.")

@birthday.command(name="test",description="Kompletten Geburtstagsgruß testen")
@app_commands.checks.has_permissions(manage_guild=True)
async def test(i):
    ch_id=settings(i.guild_id)[0]
    ch=i.guild.get_channel(ch_id) if ch_id else None
    if not ch: return await i.response.send_message("❌ Erst `/birthday channel` einrichten.",ephemeral=True)
    now=datetime.now(TZ)
    e=discord.Embed(title="🎉🎂 HAPPY BIRTHDAY! 🎂🎉",
                    description=f"## Alles Gute, {i.user.mention}! 🥳\n\n🎈 Das ist eine Testnachricht! 🎈",
                    timestamp=now)
    e.set_thumbnail(url=i.user.display_avatar.url)
    e.set_footer(text="Birthday Bot • Test")
    gif_url=settings(i.guild_id)[5] or BIRTHDAY_GIF
    if gif_url: e.set_image(url=gif_url)
    await ch.send(content=i.user.mention,embed=e)
    await i.response.send_message("✅ Komplette Testnachricht gesendet.",ephemeral=True)

@birthday.command(name="settings",description="Aktuelle Birthday-Bot Einstellungen")
@app_commands.checks.has_permissions(manage_guild=True)
async def show_settings(i):
    ch,role,dmv,rev,pingv,gifv=settings(i.guild_id)
    e=discord.Embed(title="⚙️ Birthday Bot Einstellungen")
    e.add_field(name="🎉 Kanal",value=f"<#{ch}>" if ch else "Nicht gesetzt",inline=True)
    e.add_field(name="🎁 Rolle",value=f"<@&{role}>" if role and rev else "Aus",inline=True)
    e.add_field(name="💌 DMs",value="AN" if dmv else "AUS",inline=True)
    e.add_field(name="📣 Ping",value="AN" if pingv else "AUS",inline=True)
    e.add_field(name="🖼️ GIF",value="AN" if gifv or BIRTHDAY_GIF else "AUS",inline=True)
    await i.response.send_message(embed=e,ephemeral=True)

bot.tree.add_command(birthday)
bot.run(TOKEN)
