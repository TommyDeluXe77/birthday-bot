# 🎂 Happy B-Day-Bot

Ein Discord-Bot zur automatischen Verwaltung und Anzeige von Geburtstagen.

## ✨ Features

- 🎂 Geburtstage speichern und verwalten
- 🎉 Automatische Geburtstags-Banner
- 👤 Discord-Avatar automatisch im Banner
- 📢 `@everyone` bei Geburtstagen
- 👥 Mehrere Geburtstagskinder → ein gemeinsamer Beitrag
- 🖼️ Banner werden nebeneinander mit Abstand angezeigt
- 🎁 Geburtstagsrolle automatisch vergeben
- 📅 Geburtstagskalender & Übersichten
- ⏳ Countdown zum nächsten Geburtstag
- 🧪 `/birthday test` zum Testen

🎁 Geburtstagsrolle

Mit /birthday role kann eine Rolle aktiviert werden.

Die Rolle wird am Geburtstag vergeben.

Die Rolle ist für 24 Stunden vorgesehen.

Der Bot prüft die Rolle regelmäßig.

Abgelaufene Rollen werden automatisch entfernt.

Wichtig: Die Bot-Rolle muss in Discord über der Geburtstagsrolle
stehen und der Bot benötigt Rollen verwalten.


🛠️ Befehle

Für Mitglieder

Befehl                       Funktion

/birthday set TT.MM        Geburtstag ohne Geburtsjahr speichern
/birthday set TT.MM.JJJJ   Geburtstag mit Geburtsjahr speichern
/birthday show             eigenen Geburtstag und Countdown anzeigen
/birthday remove           eigenen Geburtstag löschen
/birthday list             Geburtstagsliste des Servers
/birthday calendar         Kalender nach Monaten
/birthday upcoming         nächste 1--10 Geburtstage
/birthday next             nächsten Geburtstag mit Countdown
/birthday week             Geburtstage der nächsten 7 Tage
/birthday help             Hilfe anzeigen
/birthday game             zuletzt erkanntes Spiel anzeigen

Für Administratoren

Für diese Einstellungen benötigt der Benutzer je nach Befehl
entsprechende Discord-Berechtigungen.

Befehl                              Funktion

/birthday channel #kanal          Geburtstagskanal festlegen

/birthday dm True/False           automatische Geburtstags-DMs an/aus

/birthday role @Rolle             Geburtstagsrolle aktivieren

/birthday roleoff                 Geburtstagsrolle deaktivieren

/birthday ping True/False         User-Ping-Einstellung

/birthday gif URL                 GIF-URL für ältere/optionale
Embed-Funktionen setzen

/birthday banner URL              externe Banner-URL setzen bzw. mit
off deaktivieren

/birthday settings                aktuelle Bot-Einstellungen anzeigen

/birthday test                    Geburtstags-Banner testen

## GIF
Du kannst eine direkte GIF-URL über `/birthday gif` setzen.
Alternativ kannst du in `.env` `BIRTHDAY_GIF=` festlegen.

## Automatik
Die Prüfung läuft jede Minute. Der Geburtstagsgruß wird um 00:00 Uhr in Europe/Berlin verschickt.
Die Geburtstagsrolle wird nach 24 Stunden automatisch entfernt.

