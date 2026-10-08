# Anreise-CO₂-Tracking

Erfasst die Anreise der Mitarbeitenden über Tablets an den Eingängen und zeigt
den CO₂-Ausstoß pro Tag und Jahr im Empfangsbereich an.

| Seite | Adresse | Zweck |
|---|---|---|
| Erfassung | `http://<pi>:8080/erfassung` | Tablets an den Eingängen: Name → Verkehrsmittel (Passwort, einmalig pro Tablet) |
| Anreise-Dashboard | `http://<pi>:8080/dashboard/anreise` | Empfang: CO₂ tatsächlich vs. „alle mit dem Auto“, Ø g CO₂/km, Beteiligung, Verkehrsmittel-Anteile, Strecke mit Vergleichen, Flug-Vergleich, Rekorde |
| Verpflegungs-Dashboard | `http://<pi>:8080/dashboard/verpflegung` | CO₂ der vegetarischen Verpflegung vs. Mischkost (tagesgenau aus dem Essensplan, sonst Pauschale), Bio-Anteil |
| PV-Dashboard | `http://<pi>:8080/dashboard/pv` | Sonnenstrom: aktuelle Leistung, Tagesverlauf, Jahresertrag, Eigenverbrauch, Einspeisung, vermiedenes CO₂ |
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
- **Schwelle für Tageswerte:** Die Anreisen des laufenden Tages erscheinen im Anreise-Dashboard
  erst, wenn mindestens 3 erfasst sind (in der Verwaltung einstellbar). Bis dahin fließen sie
  nirgends ein, auch nicht in Jahreswerte, Anteile, Verlauf oder Rekorde, und sind auch über die
  Schnittstelle nicht abrufbar. Das Dashboard zeigt dann „Stand: bis gestern“.
- **Einschränkung:** Ab der Schwelle wird jede weitere Buchung sofort sichtbar. Wer das Dashboard
  direkt vor und nach einer Buchung vergleicht, kann weiterhin auf diese eine Anreise schließen.
  Bei wenigen Personen sind außerdem Rückschlüsse aus den Summen möglich, etwa wenn nur eine
  Person mit dem Motorrad kommt.
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

### Essensplan (tagesgenau)

Den Hauswirtschafts-Monatsplan (Excel, .xlsx) unverändert unter Verwaltung → „Essensplan“
hochladen. Eine Datei darf mehrere Monate als Blätter enthalten. Ablauf:

1. **Hochladen und prüfen:** Jedes sichtbare Blatt mit einer Tageszeile 1, 2, 3, … ist ein Monat.
   - Monat: Feld „Monat:“, sonst aus dem Blattnamen (z. B. „Hauswirtschaftsplan Okt“).
   - Jahr: Feld „Jahr:“, sonst Jahreszahl im Blattnamen, sonst aus den Wochentagen unter der
     Tageszeile (Vorjahr, laufendes Jahr oder Folgejahr; eindeutig). Liegt das Jahr nicht im
     laufenden Jahr, erscheint ein Hinweis.
   - Zeilen über die Beschriftung in Spalte A: Anreise, Abreise, Frühstück, Mittagessen, Lunch,
     Kaffee, Kuchen, Abendessen, Brotzeit. Pflicht: Frühstück, Mittagessen, Abendessen.
     Leere Zellen zählen als 0.
   - Ein Blatt wird abgelehnt, wenn Tage fehlen, die Wochentage nicht passen oder eine Zelle
     keine ganze Zahl ≥ 0 enthält. Die übrigen Blätter lassen sich trotzdem übernehmen.
   - Die ältere Vorlage „Dienstplan Hauswirtschaft“ ohne Zeile „Lunch“ wird weiter erkannt;
     dort gilt Lunchpakete = Frühstück − Mittagessen.
2. **Vorschau:** Summen je Monat und alle Tage. Hinweise (kein Fehler) z. B. bei mehr Abreisen
   als Frühstück. Noch nichts gespeichert.
3. **Übernehmen:** Die Monate werden vollständig ersetzt.
4. **Korrigieren:** In der Liste der importierten Monate lassen sich einzelne Werte ohne neuen
   Upload ändern. Ein späterer Upload desselben Monats überschreibt diese Korrekturen; die
   Vorschau warnt davor.

Regeln:
- Nur Gäste; Essen der Mitarbeitenden (Reste) wird nicht gezählt.
- Das Dashboard zählt nur Tage bis heute. Tage ohne Einträge im Planmonat zählen als 0.
- CO₂ pro Mahlzeit = kg pro Verpflegungstag × Anteil der Mahlzeit. Voreinstellung: Frühstück
  25 %, Mittagessen 40 %, Lunchpaket 40 %, Abendessen 35 %, Brotzeit 25 %, Kaffee 3 %,
  Kuchen 8 %. Die Anteile sind Annahmen (grob nach Kalorien), keine belastbare Quelle, und in
  der Verwaltung änderbar.
- Kaffee und Kuchen zählen im Vergleich „mit Fleisch“ gleich, bringen also keine Ersparnis.

### Pauschale (Monate ohne Essensplan)

Einstellbar unter Verwaltung → „Verpflegung, Empfangsbildschirm & Datenschutz“:
Übernachtungen mit Vollpension pro Jahr (Standard 20.000), geschlossene Monate (Standard
Dezember), Bio-Anteil (Standard 50 %) und kg CO₂e pro Verpflegungstag. Der Jahreswert wird
gleichmäßig auf die Öffnungstage verteilt (je Gast Frühstück, Lunchpaket, Abendessen). Im
Dashboard steht in der Fußnote, welche Monate nach Plan und welche geschätzt sind.

Vergleich: vegetarisch 3,81 kg vs. Mischkost mit mittlerem Fleischkonsum 5,63 kg CO₂e pro Tag
(Scarborough u. a. 2014, *Climatic Change*, je 2.000 kcal). Für Bio wird bewusst kein
CO₂-Vorteil angerechnet, da die Studienlage pro kg Lebensmittel uneinheitlich ist; angezeigt
wird nur der Anteil.

## Empfangsbildschirm

Den Bildschirm auf `http://<pi>:8080/?empfang` stellen. Dann wechseln Anreise- und
Verpflegungs-Dashboard automatisch (Standard alle 30 s, in der Verwaltung einstellbar, 0 = aus).
In der Kopfzeile zeigt ein Countdown, wann zum nächsten Dashboard gewechselt wird.
Ohne `?empfang` bleibt jede Seite stehen.

## PV-Dashboard

Datenquelle ist die lokale **Fronius Solar API** des Wechselrichters (Fronius Verto Plus, mit
Fronius Smart Meter IP). Sie ist nur lesend und muss im Menü des Wechselrichters aktiviert werden
(Kommunikation → Solar API). In der Verwaltung unter „PV-Anlage“ die IP-Adresse eintragen; mit
`demo` läuft eine simulierte Anlage zum Testen. Der Pi fragt jede Minute ab.

- Fronius-Hybridgeräte liefern keine fertigen Tages-/Jahreswerte, nur Gesamtzähler. Der Pi
  speichert deshalb Tagessummen (Erzeugung, Netzbezug, Einspeisung) selbst. Ohne Zählerstand
  wird die Energie aus der Leistung zwischen zwei Abfragen berechnet.
- Eigenverbrauch = Erzeugung − Einspeisung; Hausverbrauch = Eigenverbrauch + Netzbezug.
  Eigenverbrauchsquote = Eigenverbrauch / Erzeugung; Autarkiegrad = Eigenverbrauch / Hausverbrauch.
- Vermiedenes CO₂ = Erzeugung × 344 g/kWh (UBA, Strommix 2025, erste Schätzung; einstellbar).
- Das PV-Dashboard wird in den Seitenwechsel am Empfang aufgenommen, sobald eine Quelle eingetragen ist.
- Fällt der Wechselrichter aus (z. B. nachts im Standby oder ohne Netz), zeigt „Jetzt“ „Keine
  aktuellen Daten“; Ausfallzeiten fehlen in den Summen, sofern keine Zählerstände verfügbar sind.

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

## Hinweis auf dem Tablet

In der Verwaltung ganz oben („Hinweis auf dem Tablet“) lässt sich ein kurzer Text (max. 400 Zeichen)
eingeben, der auf der Erfassungsseite unter den Namen erscheint, z. B. eine Ankündigung oder ein
Hinweis auf eine Baustelle. Optional mit Enddatum, danach wird er automatisch ausgeblendet. Die
Tablets laden die Seite alle 15 Minuten neu und übernehmen Änderungen spätestens dann.

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
