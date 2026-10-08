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
-- Einstellungen als Schlüssel/Wert (z. B. Pauschalwerte Verpflegung)
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Verpflegung laut Dienstplan Hauswirtschaft (Excel-Import), Anzahl Gäste je
-- Mahlzeit. Lunchpakete werden nicht gespeichert, sondern berechnet.
CREATE TABLE IF NOT EXISTS meals (
    day        TEXT PRIMARY KEY,             -- YYYY-MM-DD
    breakfast  INTEGER NOT NULL DEFAULT 0,
    lunch      INTEGER NOT NULL DEFAULT 0,
    dinner     INTEGER NOT NULL DEFAULT 0,
    departures INTEGER NOT NULL DEFAULT 0
);

-- Importierte Monate; nur diese Monate werden aus "meals" gerechnet, alle
-- anderen pauschal
CREATE TABLE IF NOT EXISTS meal_imports (
    month       TEXT PRIMARY KEY,            -- YYYY-MM
    filename    TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL
);

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

# Voreinstellungen (Verwaltung → Einstellungen)
DEFAULT_SETTINGS = {
    # Verpflegung: Übernachtungen mit Vollpension pro Jahr, gleichmäßig auf die
    # Öffnungstage verteilt
    "meals_days_per_year": "20000",
    "meals_closed_months": "12",          # kommagetrennt, 1 = Januar
    "meals_organic_share": "50",          # Bio-Anteil in %
    # kg CO2e pro Verpflegungstag (2.000 kcal), Scarborough u. a. 2014
    "meals_veg_kg_per_day": "3.81",       # vegetarisch
    "meals_mixed_kg_per_day": "5.63",     # mittlerer Fleischkonsum (50–99 g/Tag)
    # Anteil der Mahlzeiten am Verpflegungstag in % (Annahme, grob nach Kalorien).
    # Ein Lunchpaket zählt wie ein Mittagessen.
    "meals_weight_breakfast": "25",
    "meals_weight_lunch": "40",
    "meals_weight_dinner": "35",
    # Empfangsbildschirm: Seitenwechsel in Sekunden, 0 = aus
    "screen_rotation_seconds": "30",
    # Datenschutz: Werte des laufenden Tages erst ab so vielen Anreisen zeigen
    "privacy_min_trips_today": "3",
    # Hinweistext auf dem Tablet unter den Namen (leer = keiner), optional bis Datum
    "kiosk_notice": "",
    "kiosk_notice_until": "",              # YYYY-MM-DD, leer = unbegrenzt
    # PV-Anlage: IP-Adresse des Fronius-Wechselrichters, "demo" oder leer (aus)
    "pv_source": "",
    "pv_kwp": "",                          # Anlagenleistung (Module) in kWp, optional
    "pv_co2_g_per_kwh": "344",             # UBA Strommix 2025 (erste Schätzung)
    "pv_ev_kwh_per_100km": "18",           # Annahme Verbrauch E-Auto
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
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
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


def overview(conn, today, min_trips_today=0):
    """Alle Kennzahlen für das Anreise-Dashboard (heute und laufendes Jahr).

    Datenschutz: Solange heute weniger als `min_trips_today` Anreisen erfasst
    sind, fließen die heutigen Werte nirgends ein (auch nicht in Jahreswerte,
    Anteile, Verlauf und Rekorde). Sonst ließe sich aus der Veränderung der
    Jahreswerte auf die ersten Personen des Tages schließen."""
    from datetime import timedelta
    year_start = today.replace(month=1, day=1).isoformat()
    today_trips = totals(conn, today.isoformat(), today.isoformat())["trips"]
    today_hidden = today_trips < min_trips_today
    # Letzter Tag, der in die Auswertung eingeht
    day = (today - timedelta(days=1) if today_hidden else today).isoformat()

    def enrich(t):
        t["avg_g_per_km"] = t["co2_g"] / t["km"] if t["km"] else None
        t["saved_g"] = t["baseline_g"] - t["co2_g"]
        return t

    if today_hidden:
        today_t = enrich({"trips": 0, "km": 0, "co2_g": 0, "baseline_g": 0})
    else:
        today_t = enrich(totals(conn, day, day))
    today_t["hidden"] = today_hidden
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
        "date": today.isoformat(),
        "year": today.year,
        "min_trips_today": min_trips_today,
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


def get_settings(conn):
    return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                 " ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def closed_months(settings):
    months = set()
    for part in settings.get("meals_closed_months", "").replace(";", ",").split(","):
        try:
            m = int(part)
        except ValueError:
            continue
        if 1 <= m <= 12:
            months.add(m)
    return months


MEAL_FIELDS = ("breakfast", "packs", "lunch", "dinner")


def meal_weights(settings):
    """Anteil je Mahlzeit am Verpflegungstag (Lunchpaket wie Mittagessen)."""
    w = {}
    for field in ("breakfast", "lunch", "dinner"):
        try:
            w[field] = max(0.0, float(settings.get(f"meals_weight_{field}") or 0)) / 100
        except ValueError:
            w[field] = 0.0
    w["packs"] = w["lunch"]
    return w


def save_meal_plan(conn, plan, filename, now):
    """Ersetzt einen Monat vollständig durch den importierten Plan."""
    key = f"{plan['year']:04d}-{plan['month']:02d}"
    with conn:
        conn.execute("DELETE FROM meals WHERE day LIKE ?", (key + "-%",))
        conn.executemany(
            "INSERT INTO meals (day, breakfast, lunch, dinner, departures) VALUES (?, ?, ?, ?, ?)",
            [(d["day"], d["breakfast"], d["lunch"], d["dinner"], d["departures"])
             for d in plan["days"]])
        conn.execute("INSERT INTO meal_imports (month, filename, imported_at) VALUES (?, ?, ?)"
                     " ON CONFLICT(month) DO UPDATE SET filename = excluded.filename,"
                     " imported_at = excluded.imported_at", (key, filename, now))
    return key


def delete_meal_month(conn, key):
    with conn:
        conn.execute("DELETE FROM meals WHERE day LIKE ?", (key + "-%",))
        conn.execute("DELETE FROM meal_imports WHERE month = ?", (key,))


def meal_days(conn, key):
    return [dict(r) for r in conn.execute(
        "SELECT day, breakfast, lunch, dinner, departures FROM meals WHERE day LIKE ? ORDER BY day",
        (key + "-%",))]


def meal_imports(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM meal_imports ORDER BY month DESC")]


def nutrition(conn, settings, today):
    """Mitlaufender Zähler. Monate mit importiertem Essensplan werden tagesgenau
    gerechnet, alle anderen pauschal: Jahreswert gleichmäßig auf die
    Öffnungstage verteilt (je Gast Frühstück, Lunchpaket und Abendessen)."""
    from datetime import date, timedelta
    from .essensplan import lunch_packs
    closed = closed_months(settings)
    per_year = float(settings["meals_days_per_year"])
    veg = float(settings["meals_veg_kg_per_day"])
    mixed = float(settings["meals_mixed_kg_per_day"])
    weights = meal_weights(settings)
    year = today.year

    plan_months = {int(r["month"][5:]) for r in conn.execute(
        "SELECT month FROM meal_imports WHERE month LIKE ?", (f"{year:04d}-%",))}
    planned = {r["day"]: r for r in conn.execute(
        "SELECT * FROM meals WHERE day LIKE ?", (f"{year:04d}-%",))}

    day = date(year, 1, 1)
    open_total = open_to_date = 0
    while day.year == year:
        if day.month not in closed:
            open_total += 1
            if day <= today:
                open_to_date += 1
        day += timedelta(days=1)
    per_day = per_year / open_total if open_total else 0

    to_date = dict.fromkeys(MEAL_FIELDS, 0.0)
    whole_year = dict.fromkeys(MEAL_FIELDS, 0.0)
    today_meals = None
    estimated_to_date = False
    day = date(year, 1, 1)
    while day.year == year:
        if day.month in plan_months:
            r = planned.get(day.isoformat())
            b, l, d = (r["breakfast"], r["lunch"], r["dinner"]) if r else (0, 0, 0)
            m = {"breakfast": b, "packs": lunch_packs(b, l), "lunch": l, "dinner": d}
            estimated = False
        elif day.month not in closed:
            m = {"breakfast": per_day, "packs": per_day, "lunch": 0, "dinner": per_day}
            estimated = True
        else:
            m, estimated = None, False
        if m:
            for k in MEAL_FIELDS:
                whole_year[k] += m[k]
                if day <= today:
                    to_date[k] += m[k]
            if day <= today and estimated:
                estimated_to_date = True
        if day == today:
            today_meals = dict(m, estimated=estimated) if m and sum(m.values()) else None
        day += timedelta(days=1)

    def equiv(m):  # Verpflegungstage (gewichtete Mahlzeiten)
        return sum(m[k] * weights[k] for k in MEAL_FIELDS)

    days_to_date = equiv(to_date)
    year_days = equiv(whole_year)
    return {
        "year": year,
        "open_today": today_meals is not None,
        "today": today_meals,
        "plan_months": sorted(plan_months),
        "estimated_to_date": estimated_to_date,
        "open_days_total": open_total,
        "open_days_to_date": open_to_date,
        "days_per_year": per_year,
        "days_per_open_day": per_day,
        "meals_to_date": to_date,
        "meal_count_to_date": sum(to_date.values()),
        "days_to_date": days_to_date,
        "veg_kg": days_to_date * veg,
        "mixed_kg": days_to_date * mixed,
        "saved_kg": days_to_date * (mixed - veg),
        "year_meal_count": sum(whole_year.values()),
        "year_days": year_days,
        "year_saved_kg": year_days * (mixed - veg),
        "veg_kg_per_day": veg,
        "mixed_kg_per_day": mixed,
        "weights": {k: v * 100 for k, v in weights.items()},
        "organic_share": float(settings["meals_organic_share"]),
    }


NOTICE_MAX_CHARS = 400


def kiosk_notice(settings, today):
    """Aktueller Hinweistext für das Tablet oder None (leer bzw. abgelaufen)."""
    text = (settings.get("kiosk_notice") or "").strip()
    until = (settings.get("kiosk_notice_until") or "").strip()
    if not text or (until and until < today.isoformat()):
        return None
    return text
