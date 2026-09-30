"""SQLite-Datenhaltung.

Datenschutz-Prinzip: Es werden KEINE Einzelbuchungen gespeichert. Eine Buchung
erhöht direkt die Tagessumme (Tag + Verkehrsmittel). Name bzw. Mitarbeiter-ID
werden nur benutzt, um die hinterlegte Strecke nachzuschlagen, und nicht
gespeichert.
"""
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS employees (
    id          INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    distance_km REAL    NOT NULL DEFAULT 0,  -- Gesamtstrecke Hin + Rück
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS modes (
    id         INTEGER PRIMARY KEY,
    label      TEXT    NOT NULL,
    icon       TEXT    NOT NULL DEFAULT '',
    factor_g   REAL    NOT NULL,             -- g CO2e pro Personenkilometer
    source     TEXT    NOT NULL DEFAULT '',
    sort       INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    is_baseline INTEGER NOT NULL DEFAULT 0   -- Vergleichswert "alle mit dem Auto"
);

-- Aggregierte Tageswerte. CO2 wird beim Buchen mit dem dann gültigen Faktor
-- berechnet und festgeschrieben, damit spätere Faktoränderungen die
-- Vergangenheit nicht verändern.
CREATE TABLE IF NOT EXISTS daily_totals (
    day        TEXT    NOT NULL,             -- YYYY-MM-DD
    mode_id    INTEGER NOT NULL REFERENCES modes(id),
    trips      INTEGER NOT NULL DEFAULT 0,
    km         REAL    NOT NULL DEFAULT 0,
    co2_g      REAL    NOT NULL DEFAULT 0,
    baseline_g REAL    NOT NULL DEFAULT 0,
    PRIMARY KEY (day, mode_id)
);
"""

# Emissionsfaktoren in g CO2-Äquivalente pro Personenkilometer.
# Quelle, soweit nicht anders vermerkt: Umweltbundesamt, TREMOD 6.71B (10/2025),
# Bezugsjahr 2024, inkl. Vorkette (Bereitstellung der Energieträger).
DEFAULT_MODES = [
    # label, icon, factor_g, source, is_baseline
    ("zu Fuß", "🚶", 0, "Keine direkten Emissionen", 0),
    ("Fahrrad/E-Bike", "🚲", 0, "Keine direkten Emissionen (E-Bike-Strom vernachlässigt)", 0),
    ("Bus", "🚌", 90, "UBA Linienbus Nahverkehr", 0),
    ("Bus + Bahn", "🚌🚆", 67,
     "Annahme je halbe Strecke: UBA Linienbus Nahverkehr 90 und Eisenbahn Nahverkehr 44", 0),
    ("Auto (Verbrenner)", "🚗", 164, "UBA Pkw (Ø 1,4 Personen/Pkw)", 1),
    ("E-Auto", "🔌", 70, "UBA Elektro-Pkw", 0),
    ("Fahrgemeinschaft Auto", "🚗👥", 115,
     "Abgeleitet: UBA Pkw 164 × 1,4 Pers. ÷ 2 Pers.", 0),
    ("Fahrgemeinschaft E-Auto", "🔌👥", 49,
     "Abgeleitet: UBA Elektro-Pkw 70 × 1,4 Pers. ÷ 2 Pers.", 0),
    ("Motorrad/Roller", "🏍️", 100,
     "PLATZHALTER – kein UBA-Wert verifiziert, bitte prüfen", 0),
]

# Name, Gesamtstrecke hin + zurück in km (einfache Strecke × 2)
DEFAULT_EMPLOYEES = [("Niko", 18), ("Angela", 74), ("Marlene", 34)]


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM modes").fetchone()[0] == 0:
        for sort, (label, icon, factor, source, baseline) in enumerate(DEFAULT_MODES):
            conn.execute(
                "INSERT INTO modes (label, icon, factor_g, source, sort, is_baseline)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (label, icon, factor, source, sort, baseline),
            )
    if conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
        for name, km in DEFAULT_EMPLOYEES:
            conn.execute("INSERT INTO employees (name, distance_km) VALUES (?, ?)", (name, km))
    conn.commit()


def baseline_factor(conn):
    row = conn.execute(
        "SELECT factor_g FROM modes WHERE is_baseline = 1 LIMIT 1"
    ).fetchone()
    return row["factor_g"] if row else 0


def record_arrival(conn, day, employee_id, mode_id):
    """Addiert eine Anreise zur Tagessumme. Gibt False zurück, wenn ungültig."""
    emp = conn.execute(
        "SELECT distance_km FROM employees WHERE id = ? AND active = 1 AND distance_km > 0",
        (employee_id,),
    ).fetchone()
    mode = conn.execute(
        "SELECT factor_g FROM modes WHERE id = ? AND active = 1", (mode_id,)
    ).fetchone()
    if emp is None or mode is None:
        return False
    km = emp["distance_km"]
    co2 = km * mode["factor_g"]
    base = km * baseline_factor(conn)
    conn.execute(
        """INSERT INTO daily_totals (day, mode_id, trips, km, co2_g, baseline_g)
           VALUES (?, ?, 1, ?, ?, ?)
           ON CONFLICT(day, mode_id) DO UPDATE SET
             trips = trips + 1,
             km = km + excluded.km,
             co2_g = co2_g + excluded.co2_g,
             baseline_g = baseline_g + excluded.baseline_g""",
        (day, mode_id, km, co2, base),
    )
    conn.commit()
    return True


def totals(conn, day_from, day_to):
    """Summen über einen Zeitraum (inklusive beider Grenzen)."""
    row = conn.execute(
        """SELECT COALESCE(SUM(trips), 0) AS trips, COALESCE(SUM(km), 0) AS km,
                  COALESCE(SUM(co2_g), 0) AS co2_g, COALESCE(SUM(baseline_g), 0) AS baseline_g
           FROM daily_totals WHERE day BETWEEN ? AND ?""",
        (day_from, day_to),
    ).fetchone()
    return dict(row)


def daily_rows(conn):
    return conn.execute(
        """SELECT d.day, m.label AS mode, d.trips, d.km, d.co2_g, d.baseline_g
           FROM daily_totals d JOIN modes m ON m.id = d.mode_id
           ORDER BY d.day, m.sort"""
    ).fetchall()
