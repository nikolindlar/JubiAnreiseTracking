# Anreise-CO₂-Tracking

Erfasst die Anreise der Mitarbeitenden über Tablets an den Eingängen und zeigt
den CO₂-Ausstoß pro Tag und Jahr im Empfangsbereich an.

| Seite | Adresse | Zweck |
|---|---|---|
| Erfassung | `http://<pi>:8080/erfassung` | Tablets an den Eingängen: Name → Verkehrsmittel (Passwort, einmalig pro Tablet) |
| Anreise-Dashboard | `http://<pi>:8080/dashboard/anreise` | Empfang: CO₂ tatsächlich vs. „alle mit dem Auto“, Ø g CO₂/km, Beteiligung, Verkehrsmittel-Anteile, Strecke mit Vergleichen, Flug-Vergleich, Rekorde |
| Verpflegungs-Dashboard | `http://<pi>:8080/dashboard/verpflegung` | CO₂ der vegetarischen Verpflegung vs. Mischkost (Pauschalwerte, mitlaufender Zähler), Bio-Anteil |
| (Startseite) | `http://<pi>:8080/` | leitet zum Anreise-Dashboard weiter |
| Verwaltung | `http://<pi>:8080/verwaltung` | Mitarbeitende, Strecken, Verkehrsmittel, Emissionsfaktoren, CSV-Export (Passwort) |

## Datenschutz – was gespeichert wird

- **Gespeichert:** Stammdaten (Name, Gesamtstrecke hin + zurück) und je Tag und
  Verkehrsmittel nur **Summen**: Anzahl Anreisen, km, CO₂ und Vergleichswert.
- **Nicht gespeichert:** wer an welchem Tag womit gekommen ist. Der angetippte
  Name wird nur benutzt, um die Strecke nachzuschlagen, und dann verworfen.
  Es gibt keine Einzelbuchungen in der Datenbank. Der Server schreibt keine
  Zugriffsprotokolle.
- **Storno:** Damit eine Buchung 20 Sekunden lang rückgängig gemacht werden kann, hält der
  Server nur im Arbeitsspeicher eine zufällige Storno-Marke mit Tag, Verkehrsmittel, km und
  CO₂-Werten, ohne Name. Die Marke verfällt nach 30 Sekunden (20 s plus Puffer für die
  Übertragung) und geht bei einem Neustart verloren.
- **Einschränkung:** Bei wenigen Personen lassen sich aus den Summen Rückschlüsse ziehen,
  zum Beispiel wenn jemand das Anreise-Dashboard direkt nach einer Buchung beobachtet oder
  nur eine Person mit dem Motorrad kommt.
- Die Erfassung ist durch ein eigenes Passwort geschützt. Jedes Tablet meldet sich einmal an
  und bleibt dann über ein Cookie bis zu 400 Tage angemeldet, auch über Neustarts hinweg.
  Die Verwaltungsanmeldung läuft nach 12 Stunden ab. Wer in der Verwaltung angemeldet ist,
  kann auch die Erfassung öffnen, umgekehrt nicht.

## Berechnung

`CO₂ = Strecke (km, hin+zurück) × Faktor des Verkehrsmittels (g CO₂e/Pkm)`
`Vergleich = Strecke × Faktor des als „Vergleich“ markierten Verkehrsmittels (Standard: Auto (Verbrenner))`

Der CO₂-Wert wird beim Buchen mit dem dann gültigen Faktor festgeschrieben.
Spätere Faktoränderungen wirken nur auf neue Anreisen.

### Voreingestellte Faktoren (in der Verwaltung änderbar)

Quelle, soweit nicht anders angegeben: Umweltbundesamt, TREMOD 6.71B (10/2025), Bezugsjahr 2024,
g CO₂-Äquivalente pro Personenkilometer inkl. Energievorkette.

| Verkehrsmittel | g/Pkm | Herkunft |
|---|---|---|
| zu Fuß | 0 | keine direkten Emissionen |
| Fahrrad/E-Bike | 0 | keine direkten Emissionen, E-Bike-Strom vernachlässigt |
| Bus | 90 | UBA Linienbus Nahverkehr |
| Bus + Bahn | 67 | **Annahme:** je halbe Strecke Bus (90) und Eisenbahn Nahverkehr (44) |
| Auto (Verbrenner) | 230 | **abgeleitet, Alleinfahrt:** UBA Pkw 164 g/Pkm × 1,4 Personen/Pkw |
| E-Auto | 98 | **abgeleitet, Alleinfahrt:** UBA Elektro-Pkw 70 g/Pkm × 1,4 Personen/Pkw |
| Fahrgemeinschaft Auto | 115 | **abgeleitet:** 230 ÷ 2 Personen |
| Fahrgemeinschaft E-Auto | 49 | **abgeleitet:** 98 ÷ 2 Personen |
| Motorrad/Roller | 140 | **abgeleitet, kein UBA-Wert:** UK DESNZ 2024 Motorrad-Durchschnitt 114 g/km (nur direkte Emissionen) + ca. 25 % Vorkette (eigene Schätzung), 1 Person. Nur Roller < 125 cm³: ca. 105 (DESNZ klein 83 g/km + 25 %) |

Hinweis: Der UBA-Pkw-Wert (164 g/Pkm) unterstellt 1,4 Personen pro Auto. Da Pendler
meist allein fahren, wird er auf eine Person umgerechnet (164 × 1,4 ≈ 230 g/Pkm).
Das E-Auto wird genauso umgerechnet (70 × 1,4 ≈ 98 g/Pkm). Der Verbrenner-Wert gilt auch für den Vergleichsbalken „alle mit dem Auto“.

## Anreise-Dashboard

- **Heute / Jahr:** eingespartes CO₂, Balken tatsächlich vs. alle mit dem Auto, Ø g CO₂ pro km,
  heute die Beteiligung („erfasst X von Y“), im Jahr der Anteil klimafreundlicher Anreisen.
- **Verkehrsmittel:** Anteil an allen Anreisen des Jahres. Bewusst nur fürs Jahr, nicht pro Tag,
  damit sich einzelne Personen nicht erkennen lassen.
- **Gefahrene Strecke / Mit Muskelkraft:** Jahres-km mit anschaulichem Vergleich (Luftlinie).
  Die Vergleichslisten stehen in `anreise/vergleiche.py`.
- **Eingespartes CO₂ als Flüge** ab München, einfache Strecke, pro Person. Näherung:
  Entfernung × 290 g/km (UBA Inlandsflug 2024) bzw. × 210 g/km (abgeleitet aus dem
  UBA-Beispiel Frankfurt–New York).
- **Ø g CO₂/km je Monat** als Verlauf, mit dem Vorjahresdurchschnitt als gestrichelter Linie
  (sobald Daten aus dem Vorjahr vorliegen).
- **Rekorde:** bester Tag nach Anteil klimafreundlich und längste Serie von Tagen mit
  mindestens 50 % klimafreundlich. Gezählt werden nur Tage mit mindestens 5 Anreisen;
  Tage ohne genug Daten (Wochenende) unterbrechen die Serie nicht.
- Welche Verkehrsmittel als „klimafreundlich“ bzw. „Muskelkraft“ zählen, ist in der Verwaltung
  einstellbar (Standard: zu Fuß, Rad, Bus, Bus + Bahn bzw. zu Fuß, Rad).

## Verpflegungs-Dashboard

Pauschalwerte, einstellbar unter Verwaltung → „Verpflegung & Empfangsbildschirm“:
Übernachtungen mit Vollpension pro Jahr (Standard 20.000), geschlossene Monate (Standard
Dezember), Bio-Anteil (Standard 50 %) und kg CO₂e pro Verpflegungstag. Der Jahreswert wird
gleichmäßig auf die Öffnungstage verteilt und läuft als Zähler mit. Vergleich: vegetarisch
3,81 kg vs. Mischkost mit mittlerem Fleischkonsum 5,63 kg CO₂e pro Tag (Scarborough u. a. 2014,
*Climatic Change*, je 2.000 kcal). Für Bio wird bewusst kein CO₂-Vorteil angerechnet, da die
Studienlage pro kg Lebensmittel uneinheitlich ist; angezeigt wird nur der Anteil.

## Empfangsbildschirm

Den Bildschirm auf `http://<pi>:8080/?empfang` stellen. Dann wechseln Anreise- und
Verpflegungs-Dashboard automatisch (Standard alle 30 s, in der Verwaltung einstellbar, 0 = aus).
In der Kopfzeile zeigt ein Countdown, wann zum nächsten Dashboard gewechselt wird.
Ohne `?empfang` bleibt jede Seite stehen.

## Installation auf dem Raspberry Pi

Voraussetzung: Raspberry Pi OS (Bookworm oder neuer), im selben WLAN/LAN wie die Tablets.

```bash
git clone <repo-url> anreise && cd anreise
sudo ./deploy/install.sh        # fragt einmalig nach Erfassungs- und Verwaltungspasswort
```

Das Skript
- installiert die Anwendung nach `/opt/anreise` und die Datenbank nach `/var/lib/anreise/`,
- legt die Konfiguration in `/etc/anreise.env` ab (Passwörter, Schlüssel, Port 8080),
- richtet den systemd-Dienst `anreise` ein, der beim Booten automatisch startet,
- legt eine tägliche Datenbanksicherung unter `/var/lib/anreise/backup/` an (14 Tage aufbewahrt).

Updates: `git pull && sudo ./deploy/install.sh`

Empfehlung: Dem Pi im Router eine **feste IP-Adresse** geben, sonst finden die
Tablets ihn nach einem Neustart eventuell nicht mehr. Die Uhrzeit des Pi muss
stimmen (NTP ist bei Raspberry Pi OS Standard), da das Datum den Tag der Buchung bestimmt.

Dienst prüfen: `systemctl status anreise`, Passwort ändern: `/etc/anreise.env` bearbeiten und
`sudo systemctl restart anreise`.

## Tablets einrichten (Android)

Empfohlen ist eine Kiosk-Browser-App, z. B. **Fully Kiosk Browser**:
Start-URL `http://<pi-ip>:8080/erfassung`, Kiosk-Modus an, Bildschirm dauerhaft an,
Start beim Booten. Ohne Kiosk-App: Chrome öffnen, Seite aufrufen, „Zum Startbildschirm
hinzufügen“ und Android-Bildschirmfixierung („App anpinnen“) nutzen.

Beim ersten Aufruf fragt die Seite nach dem Erfassungspasswort. Danach bleibt das Tablet
angemeldet, solange die Browserdaten (Cookies) nicht gelöscht werden.

Ablauf am Tablet: Name antippen → Verkehrsmittel antippen (mit „← Zurück“ geht es zur
Namensauswahl, falls die falsche Person gewählt wurde) → Bestätigung mit Knopf
„Rückgängig? 20 s“ und „Fertig“. Der Countdown läuft ab, bei 0 s erscheint wieder die
Namensauswahl. „Rückgängig“ storniert die Buchung, „Fertig“ springt sofort zur
Namensauswahl (für die nächste Person). Auf der Verkehrsmittel-Seite geht es nach
20 s ohne Eingabe ebenfalls zurück zur Namensauswahl. Die Seite und lädt sich alle 15 Minuten neu, damit Änderungen aus der
Verwaltung ankommen.

## Erste Schritte nach der Installation

1. `http://<pi-ip>:8080/verwaltung` öffnen und anmelden.
2. Strecken prüfen. Vorbelegt sind Niko 18 km, Angela 74 km, Marlene 34 km
   (jeweils hin + zurück). Diese Startwerte werden nur beim allerersten Start mit leerer
   Datenbank übernommen; spätere Änderungen erfolgen in der Verwaltung.
   Personen ohne Strecke erscheinen nicht auf dem Tablet.
3. Die Faktoren prüfen.

## Lokal testen unter Windows (ohne Pi)

1. Python von python.org installieren (Haken bei „Add python.exe to PATH“).
2. Repository als ZIP herunterladen und entpacken, z. B. nach `C:\anreise`.
3. `.env.example` kopieren, die Kopie `.env` nennen und beide Passwörter eintragen.
4. In PowerShell im Ordner:
   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   .venv\Scripts\python run.py
   ```
5. Aufruf unter `http://localhost:8080/`, vom Tablet aus `http://<IP des PCs>:8080/erfassung`
   (IP mit `ipconfig`; Windows-Firewall für private Netzwerke erlauben).

Testdaten zurücksetzen: Server beenden, `anreise.sqlite3` löschen.

## Entwicklung

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
ANREISE_KIOSK_PASSWORD=test ANREISE_ADMIN_PASSWORD=test python run.py    # http://localhost:8080
pytest
```
