# Anreise-CO₂-Tracking

Erfasst die Anreise der Mitarbeitenden über Tablets an den Eingängen und zeigt
den CO₂-Ausstoß pro Tag und Jahr im Empfangsbereich an.

| Seite | Adresse | Zweck |
|---|---|---|
| Erfassung | `http://<pi>:8080/erfassung` | Tablets an den Eingängen: Name → Verkehrsmittel |
| Übersicht | `http://<pi>:8080/` | Empfang: Balken „tatsächlich“ vs. „alle mit dem Auto“, heute und laufendes Jahr |
| Verwaltung | `http://<pi>:8080/verwaltung` | Mitarbeitende, Strecken, Verkehrsmittel, Emissionsfaktoren, CSV-Export (Passwort) |

## Datenschutz – was gespeichert wird

- **Gespeichert:** Stammdaten (Name, Gesamtstrecke hin + zurück) und je Tag und
  Verkehrsmittel nur **Summen**: Anzahl Anreisen, km, CO₂ und Vergleichswert.
- **Nicht gespeichert:** wer an welchem Tag womit gekommen ist. Der angetippte
  Name wird nur benutzt, um die Strecke nachzuschlagen, und dann verworfen.
  Es gibt keine Einzelbuchungen, auch nicht vorübergehend. Der Server schreibt
  keine Zugriffsprotokolle.
- **Einschränkung:** Bei wenigen Personen lassen sich aus den Summen Rückschlüsse ziehen,
  zum Beispiel wenn jemand die Übersicht direkt nach einer Buchung beobachtet oder
  nur eine Person mit dem Motorrad kommt.
- Die Erfassung ist ohne Anmeldung erreichbar: Jeder im WLAN kann Buchungen absenden.
  Für ein internes Netz ist das vertretbar, es erlaubt aber Fehl- und Spaßbuchungen.

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
| ÖPNV | 59 | **eigene Mittelung** aus UBA Linienbus Nahverkehr 90, Eisenbahn Nahverkehr 44, Straßen-/Stadt-/U-Bahn 42 |
| Auto (Verbrenner) | 164 | UBA Pkw (Ø 1,4 Personen/Pkw) |
| E-Auto | 70 | UBA Elektro-Pkw |
| Fahrgemeinschaft Auto | 115 | **abgeleitet:** 164 × 1,4 ÷ 2 Personen |
| Fahrgemeinschaft E-Auto | 49 | **abgeleitet:** 70 × 1,4 ÷ 2 Personen |
| Motorrad/Roller | 100 | **Platzhalter, kein verifizierter UBA-Wert** |

Hinweis: Der UBA-Pkw-Wert unterstellt 1,4 Personen pro Auto. Für allein fahrende
Pendler ist der tatsächliche Wert höher (rund 164 × 1,4 ≈ 230 g/Pkm).

## Installation auf dem Raspberry Pi

Voraussetzung: Raspberry Pi OS (Bookworm oder neuer), im selben WLAN/LAN wie die Tablets.

```bash
git clone <repo-url> anreise && cd anreise
sudo ./deploy/install.sh        # fragt einmalig nach dem Verwaltungspasswort
```

Das Skript
- installiert die Anwendung nach `/opt/anreise` und die Datenbank nach `/var/lib/anreise/`,
- legt die Konfiguration in `/etc/anreise.env` ab (Passwort, Schlüssel, Port 8080),
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

Die Erfassungsseite kehrt nach jeder Buchung bzw. nach 20 s Inaktivität zur
Namensauswahl zurück und lädt sich alle 15 Minuten neu, damit Änderungen aus der
Verwaltung ankommen.

## Erste Schritte nach der Installation

1. `http://<pi-ip>:8080/verwaltung` öffnen und anmelden.
2. Bei Niko, Angela und Marlene die **Gesamtstrecke (hin + zurück)** eintragen.
   Personen ohne Strecke erscheinen nicht auf dem Tablet.
3. Die Faktoren prüfen, insbesondere Motorrad/Roller.

## Entwicklung

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt pytest
ANREISE_ADMIN_PASSWORD=test python run.py    # http://localhost:8080
pytest
```
