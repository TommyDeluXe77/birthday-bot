# 🎂 Birthday Bot ULTRA

Die umfangreiche Version für Discord.

## Features
- 🎂 Geburtstage speichern
- 📅 kompletter Geburtstagskalender
- ⏳ Countdown zum nächsten Geburtstag
- 🎉 professioneller Embed mit Avatar
- 📣 User-Ping ein/aus
- 💌 automatische DM ein/aus
- 🎁 Geburtstagsrolle
- ⏰ Geburtstagsrolle wird nach 24 Stunden automatisch entfernt
- 🖼️ eigenes GIF im Embed
- ⚙️ Server-Einstellungsübersicht
- 💾 SQLite
- 🇩🇪 Europe/Berlin
- 🐍 Python + discord.py

## Installation
1. Python 3.10+ installieren.
2. `py -m pip install -r requirements.txt`
3. `.env.example` in `.env` kopieren.
4. Bot-Token eintragen.
5. `start.bat` starten.

## Discord-Bot Rechte
Scopes:
- bot
- applications.commands

Permissions:
- View Channels
- Send Messages
- Embed Links
- Manage Roles (für Geburtstagsrolle)

**Wichtig:** Die Bot-Rolle muss über der Geburtstagsrolle liegen.

## Befehle

### Mitglieder
- `/birthday set 15.03`
- `/birthday set 15.03.1995`
- `/birthday show`
- `/birthday remove`
- `/birthday list`
- `/birthday next`

### Admin
- `/birthday channel #kanal`
- `/birthday dm true/false`
- `/birthday role @Rolle`
- `/birthday roleoff`
- `/birthday ping true/false`
- `/birthday gif https://...`
- `/birthday gif off`
- `/birthday test`
- `/birthday settings`

## GIF
Du kannst eine direkte GIF-URL über `/birthday gif` setzen.
Alternativ kannst du in `.env` `BIRTHDAY_GIF=` festlegen.

## Automatik
Die Prüfung läuft jede Minute. Der Geburtstagsgruß wird um 00:00 Uhr in Europe/Berlin verschickt.
Die Geburtstagsrolle wird nach 24 Stunden automatisch entfernt.

## Sicherheit
Den Bot-Token niemals veröffentlichen oder in Discord posten.
