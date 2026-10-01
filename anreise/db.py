"""SQLite-Datenhaltung.

Datenschutz-Prinzip: Es werden KEINE Einzelbuchungen gespeichert. Eine Buchung
erhöht direkt die Tagessumme (Tag + Verkehrsmittel). Name bzw. Mitarbeiter-ID
werden nur benutzt, um die hinterlegte Strecke nachzuschlagen, und nicht
gespeichert.
"""
import sqlite3

# Ab so vielen Anreisen an einem Tag zählt der Tag für Rekorde
RECORD_MIN_TRIPS = 5

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
    is_baseline INTEGER NOT NULL DEFAULT 0,  -- Vergleichswert "alle mit dem Auto"
    is_green    INTEGER NOT NULL DEFAULT 0,  -- zählt als klimafreundlich
    is_human    INTEGER NOT NULL DEFAULT 0   -- mit Muskelkraft (zu Fuß, Rad)
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
    ("Auto (Verbrenner)", "🚗", 230,
     "Alleinfahrt, abgeleitet: UBA Pkw 164 g/Pkm × 1,4 Pers./Pkw", 1),
    ("E-Auto", "🔌", 98,
     "Alleinfahrt, abgeleitet: UBA Elektro-Pkw 70 g/Pkm × 1,4 Pers./Pkw", 0),
    ("Fahrgemeinschaft Auto", "🚗👥", 115,
     "Abgeleitet: Alleinfahrt 230 ÷ 2 Pers.", 0),
    ("Fahrgemeinschaft E-Auto", "🔌👥", 49,
     "Abgeleitet: Alleinfahrt 98 ÷ 2 Pers.", 0),
    ("Motorrad/Roller", "🏍️", 140,
     "Abgeleitet: UK DESNZ 2024 Motorrad Ø 114 g/km direkt + ca. 25 % Vorkette"
     " (Schätzung), 1 Person", 0),
]

# Voreinstellung der Merkmale je Verkehrsmittel: (klimafreundlich, Muskelkraft)
DEFAULT_FLAGS = {
    "zu Fuß": (1, 1),
    "Fahrrad/E-Bike": (1, 1),
    "Bus": (1, 0),
    "Bus + Bahn": (1, 0),
}

# Name, Gesamtstrecke hin + zurück in km (einfache Strecke × 2)
DEFAULT_EMPLOYEES = [("Niko", 18), ("Angela", 74), ("Marlene", 34)]


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    # Ältere Datenbanken um neue Spalten ergänzen
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(modes)")}
    new_cols = [c for c in ("is_green", "is_human") if c not in cols]
    for col in new_cols:
        conn.execute(f"ALTER TABLE modes ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    if new_cols:
        apply_default_flags(conn)
    if conn.execute("SELECT COUNT(*) FROM modes").fetchone()[0] == 0:
        for sort, (label, icon, factor, source, baseline) in enumerate(DEFAULT_MODES):
            conn.execute(
                "INSERT INTO modes (label, icon, factor_g, source, sort, is_baseline)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (label, icon, factor, source, sort, baseline),
            )
        apply_default_flags(conn)
    if conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] == 0:
        for name, km in DEFAULT_EMPLOYEES:
            conn.execute("INSERT INTO employees (name, distance_km) VALUES (?, ?)", (name, km))
    conn.commit()


def apply_default_flags(conn):
    for label, (green, human) in DEFAULT_FLAGS.items():
        conn.execute("UPDATE modes SET is_green = ?, is_human = ? WHERE label = ?",
                     (green, human, label))


def baseline_factor(conn):
    row = conn.execute(
        "SELECT factor_g FROM modes WHERE is_baseline = 1 LIMIT 1"
    ).fetchone()
    return row["factor_g"] if row else 0


def record_arrival(conn, day, employee_id, mode_id):
    """Addiert eine Anreise zur Tagessumme. Gibt die addierten Werte zurück
    (für ein Storno) oder None, wenn die Eingabe ungültig ist."""
    emp = conn.execute(
        "SELECT distance_km FROM employees WHERE id = ? AND active = 1 AND distance_km > 0",
        (employee_id,),
    ).fetchone()
    mode = conn.execute(
        "SELECT factor_g FROM modes WHERE id = ? AND active = 1", (mode_id,)
    ).fetchone()
    if emp is None or mode is None:
        return None
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
    return {"day": day, "mode_id": mode_id, "km": km, "co2_g": co2, "baseline_g": base}


def revert_arrival(conn, entry):
    """Zieht eine zuvor mit record_arrival addierte Anreise wieder ab."""
    cur = conn.execute(
        """UPDATE daily_totals SET trips = trips - 1, km = km - ?,
                  co2_g = co2_g - ?, baseline_g = baseline_g - ?
           WHERE day = ? AND mode_id = ? AND trips > 0""",
        (entry["km"], entry["co2_g"], entry["baseline_g"], entry["day"], entry["mode_id"]),
    )
    conn.execute("DELETE FROM daily_totals WHERE trips <= 0")
    conn.commit()
    return cur.rowcount == 1


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


def overview(conn, today):
    """Alle Kennzahlen für die Übersicht (heute und laufendes Jahr)."""
    day = today.isoformat()
    year_start = today.replace(month=1, day=1).isoformat()

    def enrich(t):
        t["avg_g_per_km"] = t["co2_g"] / t["km"] if t["km"] else None
        t["saved_g"] = t["baseline_g"] - t["co2_g"]
        return t

    today_t = enrich(totals(conn, day, day))
    year_t = enrich(totals(conn, year_start, day))

    by_mode = [dict(r) for r in conn.execute(
        """SELECT m.label, m.icon, m.is_green, m.is_human,
                  COALESCE(SUM(d.trips), 0) AS trips, COALESCE(SUM(d.km), 0) AS km
           FROM modes m LEFT JOIN daily_totals d
             ON d.mode_id = m.id AND d.day BETWEEN ? AND ?
           GROUP BY m.id
           HAVING m.active = 1 OR trips > 0
           ORDER BY m.sort""", (year_start, day))]
    year_trips = sum(m["trips"] for m in by_mode)
    green_trips = sum(m["trips"] for m in by_mode if m["is_green"])
    year_t["green_share"] = green_trips / year_trips if year_trips else None
    human_km = sum(m["km"] for m in by_mode if m["is_human"])

    staff = conn.execute(
        "SELECT COUNT(*) FROM employees WHERE active = 1 AND distance_km > 0").fetchone()[0]

    prev = totals(conn, f"{today.year - 1}-01-01", f"{today.year - 1}-12-31")
    prev_avg = prev["co2_g"] / prev["km"] if prev["km"] else None

    return {
        "date": day,
        "year": today.year,
        "monthly": monthly(conn, today.year, day),
        "prev_year": {"year": today.year - 1, "avg_g_per_km": prev_avg, "trips": prev["trips"]},
        "today": today_t,
        "year_total": year_t,
        "staff": staff,
        "by_mode": by_mode,
        "human_km": human_km,
        "records": records(conn, year_start, day),
        "baseline_factor_g": baseline_factor(conn),
    }


def records(conn, day_from, day_to, min_trips=RECORD_MIN_TRIPS, threshold=0.5):
    """Bester Tag (Anteil klimafreundlich) und längste Serie aufeinanderfolgender
    erfasster Tage mit mindestens `threshold` klimafreundlich. Gezählt werden nur
    Tage mit mindestens `min_trips` Anreisen; Tage ohne genug Daten (z. B.
    Wochenenden) unterbrechen die Serie nicht."""
    days = conn.execute(
        """SELECT d.day, SUM(d.trips) AS trips,
                  SUM(CASE WHEN m.is_green = 1 THEN d.trips ELSE 0 END) AS green
           FROM daily_totals d JOIN modes m ON m.id = d.mode_id
           WHERE d.day BETWEEN ? AND ?
           GROUP BY d.day HAVING SUM(d.trips) >= ?
           ORDER BY d.day""", (day_from, day_to, min_trips)).fetchall()
    best = None
    streak = longest = 0
    for r in days:
        share = r["green"] / r["trips"]
        if best is None or share > best["share"]:
            best = {"day": r["day"], "share": share}
        streak = streak + 1 if share >= threshold else 0
        longest = max(longest, streak)
    return {"best_day": best, "longest_streak": longest,
            "min_trips": min_trips, "days_counted": len(days)}


def monthly(conn, year, day_to):
    """Ø g CO2/km je Monat des Jahres bis einschließlich day_to (None ohne Daten)."""
    rows = {r["m"]: r for r in conn.execute(
        """SELECT CAST(substr(day, 6, 2) AS INTEGER) AS m, SUM(km) AS km, SUM(co2_g) AS co2_g
           FROM daily_totals WHERE day BETWEEN ? AND ? GROUP BY m""",
        (f"{year}-01-01", day_to))}
    return [{"month": m,
             "avg_g_per_km": rows[m]["co2_g"] / rows[m]["km"] if m in rows and rows[m]["km"] else None}
            for m in range(1, 13)]
