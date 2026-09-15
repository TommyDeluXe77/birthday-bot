import os
import sys

# Windows: nur eine laufende Bot-Instanz erlauben.
if os.name == "nt":
    import msvcrt
    _lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "birthday_bot.lock")
    _lock_file = open(_lock_path, "a+")
    try:
        msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("❌ Birthday Bot läuft bereits in einem anderen Fenster/Prozess.")
        sys.exit(1)
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from io import BytesIO
from PIL import Image, ImageDraw

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Berlin")
DB_PATH = os.getenv("DB_PATH", "birthdays.db")
BIRTHDAY_GIF = os.getenv("BIRTHDAY_GIF", "")
BIRTHDAY_BANNER = os.getenv("BIRTHDAY_BANNER", "")
LOCAL_BIRTHDAY_BANNER = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "birthday_banner.jpg"
)

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env-Datei.")

TZ = ZoneInfo(TIMEZONE)
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.message_content = True
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

# Verhindert doppelte 7-Tage-Vorschauen pro Geburtstag/Jahr.
db.execute("""CREATE TABLE IF NOT EXISTS birthday_previews(
 guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
 year INTEGER NOT NULL,
 PRIMARY KEY(guild_id,user_id,year))""")
db.commit()

# Zuletzt gespieltes Spiel je Mitglied speichern.
db.execute("""
CREATE TABLE IF NOT EXISTS last_games (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    game_name TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, user_id)
)
""")
db.commit()

# Verhindert doppelte /birthday-test Nachrichten (auch bei zwei Bot-Prozessen).
db.execute("""
CREATE TABLE IF NOT EXISTS test_locks (
    guild_id INTEGER NOT NULL,
    minute_key TEXT NOT NULL,
    PRIMARY KEY (guild_id, minute_key)
)
""")
db.commit()

# Eigener Marker für erfolgreich gesendete Geburtstagsbanner.
db.execute("""
CREATE TABLE IF NOT EXISTS birthday_banner_announcements (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    year INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id, year)
)
""")
db.commit()

# Migration: Logo des zuletzt gespielten Spiels speichern.
try:
    db.execute("ALTER TABLE last_games ADD COLUMN game_logo TEXT DEFAULT ''")
    db.commit()
except sqlite3.OperationalError:
    pass

# Migration für ältere Datenbanken: großes Birthday-Banner hinzufügen.
try:
    db.execute("ALTER TABLE settings ADD COLUMN banner_url TEXT DEFAULT ''")
    db.commit()
except sqlite3.OperationalError:
    pass

def get_bday(g,u):
    return db.execute("SELECT day,month,year FROM birthdays WHERE guild_id=? AND user_id=?",(g,u)).fetchone()

def save_bday(g,u,d,m,y):
    db.execute("""INSERT INTO birthdays VALUES(?,?,?,?,?)
    ON CONFLICT(guild_id,user_id) DO UPDATE SET day=excluded.day,month=excluded.month,year=excluded.year""",(g,u,d,m,y)); db.commit()

def del_bday(g,u):
    db.execute("DELETE FROM birthdays WHERE guild_id=? AND user_id=?",(g,u)); db.commit()

def settings(g):
    row=db.execute("""SELECT channel_id,role_id,dm_enabled,role_enabled,ping_enabled,gif_url,banner_url
                      FROM settings WHERE guild_id=?""",(g,)).fetchone()
    return row or (None,None,0,0,1,"","")

def save_settings(g,**kw):
    old=settings(g)
    vals={
      "channel_id":old[0],"role_id":old[1],"dm_enabled":old[2],
      "role_enabled":old[3],"ping_enabled":old[4],"gif_url":old[5],
      "banner_url":old[6]
    }
    vals.update(kw)
    db.execute("""INSERT INTO settings
      (guild_id,channel_id,role_id,dm_enabled,role_enabled,ping_enabled,gif_url,banner_url)
      VALUES(?,?,?,?,?,?,?,?)
      ON CONFLICT(guild_id) DO UPDATE SET
      channel_id=excluded.channel_id,role_id=excluded.role_id,
      dm_enabled=excluded.dm_enabled,role_enabled=excluded.role_enabled,
      ping_enabled=excluded.ping_enabled,gif_url=excluded.gif_url,
      banner_url=excluded.banner_url""",
      (g,vals["channel_id"],vals["role_id"],vals["dm_enabled"],vals["role_enabled"],
       vals["ping_enabled"],vals["gif_url"],vals["banner_url"]))
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

def age_on_birthday(birth_year, birthday_year):
    return birthday_year - birth_year if birth_year else None

async def build_birthday_banner(member):
    """Erstellt das feste Birthday-Banner mit dem Discord-Avatar exakt mittig."""
    if not os.path.isfile(LOCAL_BIRTHDAY_BANNER):
        return None

    with Image.open(LOCAL_BIRTHDAY_BANNER) as base_img:
        base = base_img.convert("RGB")

    avatar_bytes = await member.display_avatar.replace(size=1024).read()
    with Image.open(BytesIO(avatar_bytes)) as av_img:
        avatar = av_img.convert("RGB")

    # Das Banner ist 1254x1254. Der freie Mittelkreis liegt exakt um diese Mitte.
    cx, cy = base.width // 2, 752
    diameter = 570
    x0 = cx - diameter // 2
    y0 = cy - diameter // 2

    avatar = avatar.resize((diameter, diameter), Image.Resampling.LANCZOS)
    mask = Image.new("L", (diameter, diameter), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, diameter - 1, diameter - 1), fill=255)

    # Avatar kreisförmig einsetzen.
    base.paste(avatar, (x0, y0), mask)

    out = BytesIO()
    quality = 90
    while quality >= 55:
        out.seek(0)
        out.truncate(0)
        base.save(out, format="JPEG", quality=quality, optimize=True, progressive=True)
        if out.tell() <= 500 * 1024:
            out.seek(0)
            return out
        quality -= 5
    out.seek(0)
    return out

def upcoming_rows(guild_id, now):
    rows=db.execute("SELECT user_id,day,month,year FROM birthdays WHERE guild_id=?",(guild_id,)).fetchall()
    return sorted(rows, key=lambda x: next_birthday(x[1],x[2],now))

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
                    try: await member.remove_roles(role,reason="Geburtstagsrolle nach 24 Stunden abgelaufen")
                    except discord.HTTPException: pass
            db.execute("DELETE FROM role_expiry WHERE guild_id=? AND user_id=? AND role_id=?",(g,u,r))
    db.commit()

async def get_member_safe(guild, user_id):
    """Mit Cache zuerst, danach API-Fallback."""
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.HTTPException, discord.Forbidden):
        return None


async def apply_birthday_role(guild, member, role_id, now):
    """Geburtstagsrolle sicher vergeben und Ablauf speichern."""
    if not role_id or not member:
        return False

    role = guild.get_role(role_id)
    if not role:
        print(f"❌ Geburtstagsrolle {role_id} wurde auf Server {guild.id} nicht gefunden.")
        return False

    # Discord verlangt, dass die Bot-Rolle über der zu vergebenden Rolle steht.
    me = guild.me
    if not me:
        try:
            me = await guild.fetch_member(bot.user.id)
        except (discord.HTTPException, discord.NotFound, discord.Forbidden):
            me = None

    if me and role >= me.top_role:
        print(
            f"❌ Geburtstagsrolle '{role.name}' kann {member} nicht gegeben werden: "
            f"Bot-Rolle '{me.top_role.name}' steht nicht über der Geburtstagsrolle."
        )
        return False

    if not me or not me.guild_permissions.manage_roles:
        print(
            f"❌ Birthday Bot hat keine 'Rollen verwalten'-Berechtigung auf "
            f"Server {guild.id}."
        )
        return False

    try:
        if role not in member.roles:
            await member.add_roles(role, reason="Geburtstag")
            print(f"🎁 Geburtstagsrolle '{role.name}' an {member} vergeben.")

        expires = now + timedelta(hours=24)
        db.execute(
            """INSERT OR REPLACE INTO role_expiry
               (guild_id,user_id,role_id,expires_at)
               VALUES(?,?,?,?)""",
            (guild.id, member.id, role.id, expires.isoformat())
        )
        db.commit()
        return True

    except discord.Forbidden as ex:
        print(f"❌ Keine Berechtigung für Geburtstagsrolle '{role.name}': {ex}")
    except discord.HTTPException as ex:
        print(f"❌ Discord-Fehler beim Vergeben der Geburtstagsrolle: {ex}")
    except Exception as ex:
        print(f"❌ Unerwarteter Rollenfehler: {type(ex).__name__}: {ex}")

    return False


async def build_combined_birthday_banners(banners):
    """Kombiniert vollständige Birthday-Banner mit transparentem Zwischenraum.

    Der Abstand bleibt bewusst erhalten. Er ist transparent, damit Discord an
    dieser Stelle exakt den jeweiligen Kanal-/Client-Hintergrund durchscheint.
    Dadurch entsteht kein schwarzer oder dunkelblauer Balken zwischen den Bannern.
    """
    if not banners:
        return None

    images=[]
    for banner in banners:
        banner.seek(0)
        with Image.open(banner) as img:
            images.append(img.convert("RGB").copy())

    # Der gewünschte sichtbare Abstand zwischen den Bannern.
    gap = 160
    width=sum(img.width for img in images) + gap * (len(images) - 1)
    height=max(img.height for img in images)

    # WICHTIG: RGBA + Alpha 0 im Zwischenraum. Discord zeigt dort seinen
    # tatsächlichen Hintergrund, statt eine künstliche dunkle Farbe.
    canvas=Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x=0
    for index, img in enumerate(images):
        canvas.paste(img.convert("RGBA"), (x, 0))
        x += img.width
        if index < len(images) - 1:
            x += gap

    out=BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    if out.tell() <= 8 * 1024 * 1024:
        out.seek(0)
        return out

    # Fallback: falls PNG wider/komplexer als das Discord-Limit wird,
    # bleibt die Transparenz erhalten und die Bilder werden moderat skaliert.
    scale = ((8 * 1024 * 1024) / out.tell()) ** 0.5 * 0.98
    new_w=max(1, int(canvas.width * scale))
    new_h=max(1, int(canvas.height * scale))
    resized=canvas.resize((new_w, new_h), Image.Resampling.LANCZOS)
    out=BytesIO()
    resized.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out


async def announce(guild_id,user_id,day,month,year,now):
    """Verarbeitet alle heutigen Geburtstage eines Servers als EINEN Kanal-Post."""
    ch_id,role_id,dm_enabled,role_enabled,ping_enabled,gif_url,banner_url = settings(guild_id)
    guild=bot.get_guild(guild_id)
    if not guild:
        return

    rows=db.execute(
        "SELECT user_id,day,month,year FROM birthdays WHERE guild_id=?",
        (guild_id,)
    ).fetchall()
    today=[]
    for u,d,m,y in rows:
        match=((d==now.day and m==now.month) or
               (d==29 and m==2 and now.month==2 and now.day==28 and now.year%4!=0))
        if match:
            already=db.execute(
                "SELECT 1 FROM birthday_banner_announcements WHERE guild_id=? AND user_id=? AND year=?",
                (guild_id,u,now.year)
            ).fetchone()
            if not already:
                today.append((u,d,m,y))

    if not today:
        return

    members=[]
    banner_files=[]
    for u,d,m,y in today:
        member=await get_member_safe(guild,u)
        members.append((u,d,m,y,member))

        if role_enabled and role_id and member:
            await apply_birthday_role(guild, member, role_id, now)

        if member and os.path.isfile(LOCAL_BIRTHDAY_BANNER):
            try:
                bf=await build_birthday_banner(member)
                if bf:
                    banner_files.append((u,bf))
            except Exception as ex:
                print(f"❌ Fehler beim Erstellen des Geburtstagsbanners für {member}: {type(ex).__name__}: {ex}")

    ch=guild.get_channel(ch_id) if ch_id else None
    channel_sent=False
    if ch and banner_files:
        try:
            combined=await build_combined_birthday_banners([bf for _,bf in banner_files])
            if combined:
                combined.seek(0)
                await ch.send(
                    content="@everyone",
                    file=discord.File(combined, filename="birthday_banners.png"),
                    allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=True)
                )
                channel_sent=True

                # Erst nach erfolgreichem EINZELNEN Sammelpost alle heutigen Personen markieren.
                for u,_,_,_ in today:
                    db.execute(
                        "INSERT OR IGNORE INTO birthday_banner_announcements VALUES(?,?,?)",
                        (guild_id,u,now.year)
                    )
                db.commit()
        except discord.Forbidden as ex:
            print(f"❌ Keine Berechtigung im Geburtstagskanal: {ex}")
        except discord.HTTPException as ex:
            print(f"❌ Discord-Fehler beim Senden des Geburtstags-Sammelbanners: {ex}")
        except OSError as ex:
            print(f"❌ Datei-/Bannerfehler: {ex}")

    # Persönliche DMs bleiben individuell.
    if dm_enabled:
        for u,d,m,y,member in members:
            if not member:
                continue
            try:
                own=next((bf for uid,bf in banner_files if uid==u), None)
                if own:
                    own.seek(0)
                    await member.send(file=discord.File(own, filename="birthday_banner.jpg"))
                else:
                    age=now.year-y if y else None
                    e=discord.Embed(
                        title="🎂✨ HAPPY BIRTHDAY! ✨🎂",
                        description=(f"**Alles Gute, {member.mention}!** 🥳\n\n"
                                    + (f"🎈 Heute feierst du deinen **{age}. Geburtstag**!\n" if age else "")
                                    + "Wir wünschen dir einen wunderschönen Geburtstag! ❤️🎁"),
                        colour=discord.Colour.from_rgb(212,175,55), timestamp=now)
                    e.set_thumbnail(url=member.display_avatar.url)
                    await member.send(embed=e)
            except discord.Forbidden:
                print(f"⚠️ {member} hat DMs deaktiviert.")
            except discord.HTTPException as ex:
                print(f"⚠️ Geburtstags-DM an {member} konnte nicht gesendet werden: {ex}")

    print(f"🎂 Geburtstags-Sammelpost verarbeitet: {len(today)} Person(en) | Banner={'OK' if channel_sent else 'FEHLT'}")


async def ensure_today_birthday_roles(now):
    """Prüft regelmäßig, ob heutige Geburtstagskinder ihre Rolle haben."""
    rows = db.execute(
        "SELECT guild_id,user_id,day,month,year FROM birthdays"
    ).fetchall()

    for guild_id,user_id,day,month,year in rows:
        match = (
            (day == now.day and month == now.month)
            or (
                day == 29 and month == 2
                and now.month == 2 and now.day == 28
                and now.year % 4 != 0
            )
        )
        if not match:
            continue

        ch_id,role_id,dm_enabled,role_enabled,ping_enabled,gif_url,banner_url = settings(guild_id)
        if not role_enabled or not role_id:
            continue

        guild = bot.get_guild(guild_id)
        if not guild:
            continue

        member = await get_member_safe(guild, user_id)
        if member:
            await apply_birthday_role(guild, member, role_id, now)


async def send_week_before_previews(now):
    rows=db.execute("SELECT guild_id,user_id,day,month,year FROM birthdays").fetchall()
    for g,u,d,m,y in rows:
        target=next_birthday(d,m,now)
        days=(target.date()-now.date()).days
        if days != 7:
            continue
        if db.execute("SELECT 1 FROM birthday_previews WHERE guild_id=? AND user_id=? AND year=?",(g,u,target.year)).fetchone():
            continue
        guild=bot.get_guild(g)
        if not guild:
            continue
        ch_id,*_=settings(g)
        ch=guild.get_channel(ch_id) if ch_id else None
        if not ch:
            continue
        member=guild.get_member(u)
        name=member.mention if member else f"<@{u}>"
        age=age_on_birthday(y,target.year)
        age_text=f" und wird **{age} Jahre alt**" if age is not None else ""
        e=discord.Embed(title="🎉 Geburtstag steht bevor!",description=f"🎂 {name} hat in **7 Tagen** Geburtstag{age_text}!\n\n📅 **{d:02d}.{m:02d}.**\n🥳 Schon jetzt alles Gute!",colour=discord.Colour.from_rgb(212,175,55))
        if member: e.set_thumbnail(url=member.display_avatar.url)
        try:
            await ch.send(embed=e)
            db.execute("INSERT OR IGNORE INTO birthday_previews VALUES(?,?,?)",(g,u,target.year))
        except discord.HTTPException:
            pass
    db.commit()

@tasks.loop(minutes=1)
async def scheduler():
    now=datetime.now(TZ)

    await remove_expired_roles()

    # Heutige Geburtstage JEDE MINUTE prüfen. Damit wird der Banner auch
    # gesendet, wenn der Bot nach Mitternacht gestartet wurde.
    rows=db.execute(
        "SELECT guild_id,user_id,day,month,year FROM birthdays"
    ).fetchall()

    for g,u,d,m,y in rows:
        match=(
            (d==now.day and m==now.month)
            or (
                d==29 and m==2
                and now.month==2 and now.day==28
                and now.year%4!=0
            )
        )
        if match:
            await announce(g,u,d,m,y,now)

    # Vorschauen nur einmal um Mitternacht.
    if now.hour == 0 and now.minute == 0:
        await send_week_before_previews(now)


@scheduler.before_loop
async def before_scheduler(): await bot.wait_until_ready()

GUILD_ID = 926921790326992906
GUILD = discord.Object(id=GUILD_ID)

@bot.event
async def on_presence_update(before, after):
    if not after.guild:
        return

    game = None
    for activity in after.activities:
        if isinstance(activity, discord.Game) or getattr(activity, "type", None) == discord.ActivityType.playing:
            game = activity
            break

    if not game or not getattr(game, "name", None):
        return

    game_logo = ""
    assets = getattr(game, "assets", None)
    if assets:
        try:
            game_logo = str(assets.large_image_url)
        except Exception:
            game_logo = ""

    db.execute(
        """INSERT INTO last_games (guild_id,user_id,game_name,updated_at,game_logo)
           VALUES (?,?,?,?,?)
           ON CONFLICT(guild_id,user_id) DO UPDATE SET
           game_name=excluded.game_name,
           updated_at=excluded.updated_at,
           game_logo=excluded.game_logo""",
        (after.guild.id, after.id, game.name, datetime.now(TZ).isoformat(), game_logo)
    )
    db.commit()

def get_last_game(guild_id, user_id):
    row = db.execute(
        "SELECT game_name, game_logo FROM last_games WHERE guild_id=? AND user_id=?",
        (guild_id, user_id)
    ).fetchone()
    return row if row else (None, None)

async def capture_current_games():
    for guild in bot.guilds:
        for member in guild.members:
            game = None
            for activity in member.activities:
                if isinstance(activity, discord.Game) or getattr(activity, "type", None) == discord.ActivityType.playing:
                    game = activity
                    break
            if not game or not getattr(game, "name", None):
                continue

            game_logo = ""
            assets = getattr(game, "assets", None)
            if assets:
                try:
                    game_logo = str(assets.large_image_url)
                except Exception:
                    game_logo = ""

            db.execute(
                """INSERT INTO last_games (guild_id,user_id,game_name,updated_at,game_logo)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(guild_id,user_id) DO UPDATE SET
                   game_name=excluded.game_name,
                   updated_at=excluded.updated_at,
                   game_logo=excluded.game_logo""",
                (guild.id, member.id, game.name, datetime.now(TZ).isoformat(), game_logo)
            )
    db.commit()

@bot.event
async def on_ready():
    if not scheduler.is_running():
        scheduler.start()
    try:
        bot.tree.clear_commands(guild=GUILD)
        bot.tree.copy_global_to(guild=GUILD)
        synced = await bot.tree.sync(guild=GUILD)
        await capture_current_games()
        print(f"🎂 Birthday Bot online als {bot.user} | {len(synced)} Commands auf Server {GUILD_ID}")
    except Exception as e:
        print("Sync:", e)


birthday=app_commands.Group(name="birthday",description="🎂 Geburtstage verwalten")

@birthday.command(name="set",description="Geburtstag speichern")
@app_commands.describe(datum="TT.MM oder TT.MM.JJJJ")
async def set_bday(i:discord.Interaction,datum:str):
    try: d,m,y=parse_date(datum)
    except:
        return await i.response.send_message("❌ Ungültig. Beispiel: `15.03` oder `15.03.1995`",ephemeral=True)

    now=datetime.now(TZ)
    save_bday(i.guild_id,i.user.id,d,m,y)

    # Wird ein anderes Datum eingetragen, wird eine eventuell noch laufende
    # Geburtstagsrolle vom alten Test/Tag entfernt.
    if not (d == now.day and m == now.month):
        ch_id,role_id,dm_enabled,role_enabled,ping_enabled,gif_url,banner_url = settings(i.guild_id)
        if role_id:
            role=guild_role = i.guild.get_role(role_id)
            member=i.guild.get_member(i.user.id)
            if role and member and role in member.roles:
                try:
                    await member.remove_roles(role, reason="Geburtstag geändert")
                except discord.HTTPException:
                    pass
            db.execute("DELETE FROM role_expiry WHERE guild_id=? AND user_id=? AND role_id=?",
                       (i.guild_id,i.user.id,role_id))
            db.commit()

        return await i.response.send_message(
            f"🎂 Geburtstag **{d:02d}.{m:02d}" + (f".{y}" if y else "") + "** gespeichert.",
            ephemeral=True
        )

    # Wenn der Geburtstag HEUTE ist, wird er sofort verarbeitet – ohne bis
    # Mitternacht warten zu müssen. announce() verhindert weiterhin doppelte
    # Geburtstagsnachrichten für dasselbe Jahr.
    await announce(i.guild_id,i.user.id,d,m,y,now)

    ch_id,role_id,dm_enabled,role_enabled,ping_enabled,gif_url,banner_url = settings(i.guild_id)
    if role_enabled and role_id:
        role=i.guild.get_role(role_id)
        if role and i.user.id:
            await i.response.send_message(
                f"🎂 Geburtstag **{d:02d}.{m:02d}" + (f".{y}" if y else "") + "** gespeichert.\n"
                f"🎁 Deine Geburtstagsrolle {role.mention} wurde **sofort für 24 Stunden** vergeben.",
                ephemeral=True
            )
        else:
            await i.response.send_message(
                f"🎂 Geburtstag **{d:02d}.{m:02d}" + (f".{y}" if y else "") + "** gespeichert.\n"
                "⚠️ Die Geburtstagsrolle ist aktiviert, konnte aber nicht gefunden werden.",
                ephemeral=True
            )
    else:
        await i.response.send_message(
            f"🎂 Geburtstag **{d:02d}.{m:02d}" + (f".{y}" if y else "") + "** gespeichert.\n"
            "ℹ️ Heute ist dein Geburtstag – die automatische Verarbeitung wurde sofort gestartet.\n"
            "🎁 Falls du die Rolle möchtest, einmal `/birthday role` mit **🎂 (Geburtstagskind)** aktivieren.",
            ephemeral=True
        )

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

@birthday.command(name="list",description="Saubere Geburtstagsliste mit Zeilen und Spalten")
async def list_b(i):
    rows=db.execute(
        "SELECT user_id,day,month,year FROM birthdays WHERE guild_id=? ORDER BY month,day,year",
        (i.guild_id,)
    ).fetchall()
    if not rows:
        return await i.response.send_message(
            "📭 **Noch keine Geburtstage gespeichert.**\n\n"
            "Nutze `/birthday set TT.MM` oder `/birthday set TT.MM.JJJJ`, um deinen Geburtstag einzutragen."
        )

    months={
        1:"JANUAR",2:"FEBRUAR",3:"MÄRZ",4:"APRIL",5:"MAI",6:"JUNI",
        7:"JULI",8:"AUGUST",9:"SEPTEMBER",10:"OKTOBER",11:"NOVEMBER",12:"DEZEMBER"
    }
    icons={
        1:"❄️",2:"💝",3:"🌷",4:"🌸",5:"🌼",6:"☀️",
        7:"🏖️",8:"🌻",9:"🍂",10:"🎃",11:"🍁",12:"🎄"
    }
    now=datetime.now(TZ)
    grouped={m:[] for m in range(1,13)}

    for u,d,m,y in rows:
        mem=i.guild.get_member(u)
        name=mem.display_name if mem else f"User {u}"
        name=name.replace("\n"," ").replace("\r"," ").strip()
        if len(name)>22:
            name=name[:19]+"..."

        age=None
        if y:
            age=now.year-y
            birthday_this_year=datetime(now.year,m,d,tzinfo=TZ)
            if birthday_this_year > now:
                age-=1

        grouped[m].append((d,name,age))

    this_month=sum(1 for _,_,m,_ in rows if m==now.month)

    # Eine einzige, feste Tabelle: dadurch bleiben Zeilen und Spalten
    # unabhängig von der Discord-Fensterbreite sauber ausgerichtet.
    table=[]
    table.append("DATUM      NAME                    ALTER")
    table.append("────────────────────────────────────────")

    for m in range(1,13):
        entries=grouped[m]
        if not entries:
            continue

        marker="⭐ " if m==now.month else "   "
        table.append(f"{marker}{icons[m]} {months[m]}")
        table.append("────────────────────────────────────────")

        for d,name,age in entries:
            age_text=f"{age:>5}" if age is not None else "    —"
            table.append(f"{d:02d}.{m:02d}.    {name:<22} {age_text}")

        table.append("")

    # Discord-Description ist auf 4096 Zeichen begrenzt. Bei sehr großen
    # Servern wird die Tabelle automatisch auf mehrere saubere Seiten geteilt.
    chunks=[]
    current=[]
    current_len=0
    for line in table:
        add_len=len(line)+1
        if current and current_len+add_len>3600:
            chunks.append(current)
            current=[]
            current_len=0
        current.append(line)
        current_len+=add_len
    if current:
        chunks.append(current)

    embeds=[]
    for idx,chunk in enumerate(chunks,1):
        title="🎂 Geburtstagsliste"
        if len(chunks)>1:
            title += f"  •  Seite {idx}/{len(chunks)}"

        e=discord.Embed(
            title=title,
            description=(
                f"👥 **{len(rows)}** eingetragen   •   📅 **{this_month}** diesen Monat\n\n"
                "```text\n" + "\n".join(chunk) + "\n```"
            ),
            colour=discord.Colour.from_rgb(212,175,55)
        )
        e.set_footer(text="Birthday-DeluXe-Bot • /birthday set zum Eintragen")
        embeds.append(e)

    await i.response.send_message(embeds=embeds[:10])


@birthday.command(name="calendar",description="Geburtstagskalender mit Monatsüberschriften anzeigen")
async def calendar(i):
    try:
        # Direkt als öffentliche Interaction-Antwort senden.
        # Kein defer() -> dadurch bleibt kein „Bot denkt nach …“ stehen.
        rows=db.execute("SELECT user_id,day,month,year FROM birthdays WHERE guild_id=? ORDER BY month,day",(i.guild_id,)).fetchall()
        if not rows:
            return await i.response.send_message("📭 Noch keine Geburtstage gespeichert.")
        months={1:"Januar",2:"Februar",3:"März",4:"April",5:"Mai",6:"Juni",7:"Juli",8:"August",9:"September",10:"Oktober",11:"November",12:"Dezember"}
        now=datetime.now(TZ)
        grouped={}
        for u,d,m,y in rows:
            mem=i.guild.get_member(u); name=mem.display_name if mem else f"<@{u}>"
            days=countdown(next_birthday(d,m,now),now)[0]
            grouped.setdefault(m,[]).append(f"🎂 **{d:02d}.{m:02d}** — {name} • ⏳ {days} Tage")
        e=discord.Embed(title="📅 Birthday-DeluXe-Bot • Geburtstagskalender",description="Alle eingetragenen Geburtstage dieses Servers.",colour=discord.Colour.from_rgb(212,175,55))
        for m in sorted(grouped):
            e.add_field(name=f"🗓️ {months[m]}",value="\n".join(grouped[m][:10]),inline=False)
        e.set_footer(text=f"{len(rows)} Geburtstage gespeichert • Birthday-DeluXe-Bot")
        await i.response.send_message(embed=e)
    except Exception as ex:
        print(f"[CALENDAR ERROR] {type(ex).__name__}: {ex}")
        if not i.response.is_done():
            await i.response.send_message("❌ Der Geburtstagskalender konnte gerade nicht angezeigt werden.",ephemeral=True)


@birthday.command(name="upcoming",description="Die nächsten Geburtstage anzeigen")
@app_commands.describe(anzahl="Anzahl der Geburtstage (1-10)")
async def upcoming(i,anzahl:int=5):
    if anzahl<1 or anzahl>10:
        return await i.response.send_message("❌ Die Anzahl muss zwischen 1 und 10 liegen.",ephemeral=True)
    rows=db.execute("SELECT user_id,day,month,year FROM birthdays WHERE guild_id=?",(i.guild_id,)).fetchall()
    if not rows:
        return await i.response.send_message("📭 Noch keine Geburtstage gespeichert.",ephemeral=True)
    now=datetime.now(TZ)
    selected=sorted(rows,key=lambda x:next_birthday(x[1],x[2],now))[:anzahl]
    lines=[]
    for n,(u,d,m,y) in enumerate(selected,1):
        mem=i.guild.get_member(u); name=mem.display_name if mem else f"<@{u}>"
        days=countdown(next_birthday(d,m,now),now)[0]
        lines.append(f"**{n}.** 🎂 **{d:02d}.{m:02d}** — {name} • ⏳ {days} Tage")
    e=discord.Embed(title="🎉 Die nächsten Geburtstage",description="\n".join(lines),colour=discord.Colour.from_rgb(212,175,55))
    e.set_footer(text="Birthday-DeluXe-Bot • Stay Legendary")
    await i.response.send_message(embed=e)

@birthday.command(name="next",description="Nächsten Geburtstag mit Countdown anzeigen")
async def next_b(i:discord.Interaction):
    try:
        # Sofort bestätigen, damit das Herunterladen der Avatare nicht zu einem
        # "Unknown interaction" / 10062 führt.
        await i.response.defer()

        now = datetime.now(TZ)
        rows = upcoming_rows(i.guild_id, now)

        if not rows:
            return await i.followup.send(
                "📭 Auf diesem Server sind noch keine Geburtstage gespeichert."
            )

        # Alle Personen mit exakt demselben nächsten Geburtstag ermitteln.
        first_u, first_d, first_m, first_y = rows[0]
        first_target = next_birthday(first_d, first_m, now)
        same_day = [
            row for row in rows
            if next_birthday(row[1], row[2], now) == first_target
        ]

        # Discord erlaubt bis zu 10 Embeds in EINER Nachricht.
        # Deshalb werden alle Karten gesammelt und anschließend mit EINEM
        # followup.send() gepostet. Es entstehen keine einzelnen Posts mehr.
        same_day = same_day[:10]

        days, hours, minutes = countdown(first_target, now)
        if days == 0 and hours == 0 and minutes == 0:
            countdown_text = "🎉 **Heute!**"
        elif days == 0:
            countdown_text = f"⏳ **{hours} Std. und {minutes} Min.**"
        else:
            countdown_text = f"⏳ **{days} Tage, {hours} Std. und {minutes} Min.**"

        total = len(same_day)
        embeds = []
        files = []

        for index, (u, d, m, y) in enumerate(same_day, start=1):
            mem = i.guild.get_member(u)
            name = mem.mention if mem else f"<@{u}>"
            age = age_on_birthday(y, first_target.year)

            desc = f"🎂 {name}\n📅 **{d:02d}.{m:02d}**"
            if age is not None:
                desc += f"\n🎈 wird **{age} Jahre alt**"
            desc += f"\n\n{countdown_text}"

            title = "🎉 Nächster Geburtstag"
            if total > 1:
                title += f" ({index}/{total})"

            # Farbwelt des neuen Birthday-Banners: kräftiges Blau als Basis,
            # Gold und Rot als sichtbare Akzente.
            e = discord.Embed(
                title=f"💙 {title}",
                description=(
                    "🟨 **BIRTHDAY DELUXE**\n"
                    "────────────────────────\n"
                    f"{desc}\n\n"
                    "🔴 **Alles Gute schon jetzt!**"
                ),
                colour=discord.Colour.from_rgb(20, 115, 235)
            )
            e.set_footer(text="🎂 Birthday-DeluXe-Bot  •  💙 Blau  🟨 Gold  🔴 Rot")

            if mem:
                try:
                    avatar_bytes = await mem.display_avatar.replace(
                        size=256, format="png"
                    ).read()
                    filename = f"birthday_avatar_{index}.png"
                    files.append(discord.File(BytesIO(avatar_bytes), filename=filename))
                    e.set_thumbnail(url=f"attachment://{filename}")
                except (discord.HTTPException, OSError) as avatar_error:
                    print(
                        f"⚠️ Avatar konnte für {mem} nicht geladen werden: "
                        f"{type(avatar_error).__name__}: {avatar_error}"
                    )

            embeds.append(e)

        # GENAU EIN öffentlicher Discord-Post mit allen Birthday-Next-Karten.
        await i.followup.send(embeds=embeds, files=files)

    except Exception as exc:
        print(f"❌ Fehler bei /birthday next: {type(exc).__name__}: {exc}")
        if i.response.is_done():
            try:
                await i.followup.send(
                    "❌ /birthday next konnte gerade nicht ausgeführt werden.",
                    ephemeral=True
                )
            except discord.HTTPException:
                pass
        else:
            try:
                await i.response.send_message(
                    "❌ /birthday next konnte gerade nicht ausgeführt werden.",
                    ephemeral=True
                )
            except discord.HTTPException:
                pass


@birthday.command(name="week",description="Geburtstage der nächsten 7 Tage anzeigen")
async def week(i):
    now=datetime.now(TZ)
    rows=upcoming_rows(i.guild_id, now)
    items=[]
    for u,d,m,y in rows:
        target=next_birthday(d,m,now)
        days=(target.date()-now.date()).days
        if 0 <= days <= 7:
            mem=i.guild.get_member(u); name=mem.display_name if mem else f"<@{u}>"
            age=age_on_birthday(y,target.year)
            age_text=f" • 🎈 {age} Jahre" if age is not None else ""
            items.append(f"🎂 **{d:02d}.{m:02d}** — {name}{age_text} • ⏳ {days} Tage")
    if not items:
        return await i.response.send_message("📭 In den nächsten 7 Tagen hat niemand Geburtstag.",ephemeral=True)
    e=discord.Embed(title="📅 Geburtstage dieser Woche",description="\n".join(items),colour=discord.Colour.from_rgb(212,175,55))
    e.set_footer(text="Birthday Bot • Nächste 7 Tage")
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


@birthday.command(name="help",description="Alle Birthday-Bot Befehle anzeigen")
async def birthday_help(i):
    e=discord.Embed(
        title="🎂 Birthday-DeluXe-Bot • Hilfe",
        description="Alle wichtigen Funktionen auf einen Blick.",
        colour=discord.Colour.from_rgb(212,175,55))
    e.add_field(
        name="👤 Für Mitglieder",
        value="`/birthday set` — Geburtstag eintragen\n"
              "`/birthday show` — eigenen Geburtstag anzeigen\n"
              "`/birthday remove` — Geburtstag löschen\n"
              "`/birthday list` — Geburtstagsliste\n"
              "`/birthday calendar` — Kalender nach Monaten\n"
              "`/birthday next` — nächsten Geburtstag\n"
              "`/birthday upcoming` — nächste Geburtstage\n`/birthday week` — Geburtstage der nächsten 7 Tage\n`/birthday help` — diese Hilfe",
        inline=False)
    e.add_field(
        name="🛡️ Für Admins",
        value="`/birthday channel` — Geburtstagskanal\n"
              "`/birthday dm` — automatische DMs an/aus\n"
              "`/birthday role` — Geburtstagsrolle\n"
              "`/birthday roleoff` — Rolle deaktivieren\n"
              "`/birthday ping` — Mention an/aus\n"
              "`/birthday gif` — GIF setzen\n"
              "`/birthday banner` — großes Banner setzen\n"
              "`/birthday settings` — Einstellungen\n"
              "`/birthday test` — Nachricht testen",
        inline=False)
    e.set_footer(text="Birthday-DeluXe-Bot • Stay Legendary 🎁")
    await i.response.send_message(embed=e,ephemeral=True)

@birthday.command(name="banner",description="Großes Birthday-Banner setzen")
@app_commands.describe(url="Direkte Bild-URL; mit 'off' ausschalten")
@app_commands.checks.has_permissions(manage_guild=True)
async def banner(i, url:str):
    if url.lower() == "off":
        save_settings(i.guild_id, banner_url="")
        return await i.response.send_message("✅ Großes Birthday-Banner deaktiviert.", ephemeral=True)
    if not (url.startswith("https://") or url.startswith("http://")):
        return await i.response.send_message("❌ Bitte eine gültige Bild-URL angeben.", ephemeral=True)
    save_settings(i.guild_id, banner_url=url)
    await i.response.send_message("✅ Großes Birthday-Banner gespeichert.", ephemeral=True)

@birthday.command(name="settings",description="Übersichtliche Birthday-Bot Einstellungen")
@app_commands.checks.has_permissions(manage_guild=True)
async def settings_cmd(i):
    ch_id, role_id, dm_enabled, role_enabled, ping_enabled, gif_url, banner_url = settings(i.guild_id)
    ch = i.guild.get_channel(ch_id) if ch_id else None
    role = i.guild.get_role(role_id) if role_id else None

    e = discord.Embed(
        title="⚙️ Birthday-DeluXe-Bot • Einstellungen",
        description="Aktuelle Konfiguration des Geburtstagsbots.",
        colour=discord.Colour.from_rgb(212,175,55)
    )
    e.add_field(name="🎁 Geburtstagskanal", value=ch.mention if ch else "❌ Nicht gesetzt", inline=False)
    e.add_field(name="🎂 Geburtstagsrolle", value=role.mention if role and role_enabled else "🔴 AUS", inline=True)
    e.add_field(name="💌 Automatische DM", value="🟢 AN" if dm_enabled else "🔴 AUS", inline=True)
    e.add_field(name="🔔 Geburtstags-Ping", value="🟢 AN" if ping_enabled else "🔴 AUS", inline=True)
    e.add_field(name="🖼️ Großes Banner", value="🟢 AN" if banner_url else "🔴 AUS", inline=True)
    e.add_field(name="🎉 GIF", value="🟢 AN" if gif_url else "🔴 AUS", inline=True)
    e.add_field(name="⏰ Automatik", value="🟢 Täglich um 00:00", inline=True)
    e.set_footer(text="Birthday-DeluXe-Bot • Stay Legendary 👑")
    await i.response.send_message(embed=e, ephemeral=True)

@birthday.command(name="game",description="Zeigt das zuletzt erkannte Spiel")
@app_commands.describe(user="Mitglied, dessen Spiel angezeigt werden soll")
async def game(i, user: discord.Member = None):
    target = user or i.user
    game_name, game_logo = get_last_game(i.guild_id, target.id)
    if not game_name:
        return await i.response.send_message(
            f"🎮 Für {target.mention} wurde noch kein Spiel erkannt. "
            "Starte ein Spiel bzw. wechsle einmal das Spiel, während der Bot online ist.",
            ephemeral=True
        )

    e = discord.Embed(
        title="🎮 Zuletzt gespielt",
        description=f"{target.mention} hat zuletzt **{game_name}** gespielt."
    )
    if game_logo:
        e.set_thumbnail(url=game_logo)
    await i.response.send_message(embed=e)

@birthday.command(name="test",description="Birthday-Banner mit deinem Avatar testen")
@app_commands.checks.has_permissions(manage_guild=True)
async def test(i):
    ch_id=settings(i.guild_id)[0]
    ch=i.guild.get_channel(ch_id) if ch_id else None
    if not ch:
        return await i.response.send_message(
            "❌ Erst `/birthday channel` einrichten.", ephemeral=True
        )

    # Sofort bestätigen, damit das Laden mehrerer Discord-Avatare nicht
    # zu einem "Unknown interaction" / 10062 führt.
    await i.response.defer(ephemeral=True)

    try:
        if not os.path.isfile(LOCAL_BIRTHDAY_BANNER):
            return await i.followup.send(
                "❌ `birthday_banner.jpg` wurde neben `bot.py` nicht gefunden.",
                ephemeral=True
            )

        # Für den Test dieselbe Sammel-Logik wie beim echten Geburtstag:
        # Hat der Testende einen Geburtstag gespeichert, werden alle
        # registrierten Personen mit demselben Tag/Monat gemeinsam dargestellt.
        own_bday = get_bday(i.guild_id, i.user.id)
        selected = []
        if own_bday:
            test_day, test_month, _ = own_bday
            rows = db.execute(
                "SELECT user_id,day,month,year FROM birthdays WHERE guild_id=? AND day=? AND month=? ORDER BY user_id",
                (i.guild_id, test_day, test_month)
            ).fetchall()
            selected = rows

        # Wenn für den Testenden kein Geburtstag gespeichert ist, bleibt der
        # bisherige Einzelbanner-Test erhalten.
        if not selected:
            selected = [(i.user.id, None, None, None)]
        elif not any(u == i.user.id for u,_,_,_ in selected):
            selected.insert(0, (i.user.id, None, None, None))

        banner_files = []
        for u, _, _, _ in selected:
            member = i.guild.get_member(u)
            if member is None and u == i.user.id:
                member = i.user
            if member is None:
                member = await get_member_safe(i.guild, u)
            if not member:
                continue
            try:
                bf = await build_birthday_banner(member)
                if bf:
                    banner_files.append(bf)
            except Exception as ex:
                print(
                    f"⚠️ Test-Banner konnte für User {u} nicht erstellt werden: "
                    f"{type(ex).__name__}: {ex}"
                )

        if not banner_files:
            return await i.followup.send(
                "❌ Es konnte kein Birthday-Banner erstellt werden.",
                ephemeral=True
            )

        # GENAU EIN öffentlicher Test-Post: bei mehreren Personen werden die
        # vollständigen Banner nebeneinander in EIN Bild kombiniert.
        combined = await build_combined_birthday_banners(banner_files)
        if not combined:
            return await i.followup.send(
                "❌ Das gemeinsame Birthday-Banner konnte nicht erstellt werden.",
                ephemeral=True
            )

        combined.seek(0)
        count = len(banner_files)
        content = "@everyone"
        if count > 1:
            content += f"\n🎉 Heute haben gleich **{count} Personen** Geburtstag! 🎁"

        await ch.send(
            content=content,
            file=discord.File(combined, filename="birthday_banners.png"),
            allowed_mentions=discord.AllowedMentions(
                users=False, roles=False, everyone=True
            )
        )

        if count > 1:
            await i.followup.send(
                f"✅ Test gesendet: **{count} Birthday-Banner** nebeneinander in einem Post.",
                ephemeral=True
            )
        else:
            await i.followup.send(
                "✅ Birthday-Banner mit deinem Avatar gesendet.",
                ephemeral=True
            )
    except Exception as ex:
        print(f"[TEST BANNER ERROR] {type(ex).__name__}: {ex}")
        await i.followup.send(
            "❌ Das Birthday-Banner konnte nicht gesendet werden. Siehe Konsole.",
            ephemeral=True
        )

bot.tree.add_command(birthday)

@bot.tree.error
async def on_tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    try:
        if isinstance(error, app_commands.CommandInvokeError):
            original = error.original
            print(f"❌ Fehler bei /{interaction.command.qualified_name if interaction.command else 'unknown'}: {original}")
        else:
            print(f"❌ Slash-Command-Fehler: {error}")

        message = "❌ Beim Ausführen des Befehls ist ein Fehler aufgetreten. Bitte versuche es erneut."
        if isinstance(error, app_commands.MissingPermissions):
            message = "❌ Du hast nicht die nötigen Berechtigungen für diesen Befehl."

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception as handler_error:
        print(f"❌ Fehler im Fehlerhandler: {handler_error}")

bot.run(TOKEN)
