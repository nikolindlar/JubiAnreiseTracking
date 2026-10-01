"""PV-Anlage: Abfrage des Fronius-Wechselrichters (Solar API v1), Speicherung, Auswertung.

Datenquelle ist die lokale Fronius Solar API (JSON, nur lesend). Sie muss im Menü
des Wechselrichters aktiviert sein (Kommunikation → Solar API).

- GetPowerFlowRealtimeData.fcgi: aktuelle Leistungen (P_PV, P_Grid, P_Load) und
  der Gesamtzähler der Erzeugung (E_Total). Tages-/Jahreswerte (E_Day, E_Year)
  liefern die Fronius-Hybridgeräte nicht, deshalb rechnen wir sie selbst.
- GetMeterRealtimeData.cgi?Scope=System: Zählerstände des Smart Meters für
  Netzbezug und Einspeisung. Je nach Firmware heißen die Felder
  EnergyReal_WAC_Sum_Consumed/_Produced oder SMARTMETER_ENERGYACTIVE_*_SUM_F64.

Liefert ein Gerät keinen Zählerstand, wird die Energie aus der Leistung und der
Zeit zwischen zwei Abfragen berechnet (weniger genau, aber robust).
"""
import json
import math
import random
import threading
import time
import urllib.request
from datetime import date, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS pv_daily (
    day        TEXT PRIMARY KEY,           -- YYYY-MM-DD
    pv_wh      REAL NOT NULL DEFAULT 0,    -- Erzeugung
    import_wh  REAL NOT NULL DEFAULT 0,    -- Netzbezug
    export_wh  REAL NOT NULL DEFAULT 0,    -- Einspeisung
    peak_w     REAL NOT NULL DEFAULT 0     -- höchste PV-Leistung
);
CREATE TABLE IF NOT EXISTS pv_samples (
    ts      INTEGER PRIMARY KEY,           -- Unix-Zeit
    pv_w    REAL,
    load_w  REAL,
    grid_w  REAL                           -- + Bezug, – Einspeisung
);
-- Letzte Abfrage, damit nach einem Neustart weitergerechnet werden kann
CREATE TABLE IF NOT EXISTS pv_state (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    ts               INTEGER,
    pv_w             REAL,
    grid_w           REAL,
    pv_total_wh      REAL,
    import_total_wh  REAL,
    export_total_wh  REAL
);
"""

SAMPLE_DAYS = 3            # Verlaufsdaten (Minutenwerte) so lange aufbewahren
MAX_INTEGRATE_GAP = 15 * 60  # Leistung nur über kurze Lücken integrieren (s)
STALE_AFTER = 5 * 60       # Live-Werte gelten danach als veraltet (s)

METER_KEYS = {
    "import": ("EnergyReal_WAC_Sum_Consumed", "SMARTMETER_ENERGYACTIVE_CONSUMED_SUM_F64"),
    "export": ("EnergyReal_WAC_Sum_Produced", "SMARTMETER_ENERGYACTIVE_PRODUCED_SUM_F64"),
}


def init_db(conn):
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------- Abfrage

def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def parse_powerflow(payload):
    site = payload["Body"]["Data"]["Site"]
    pv = _num(site.get("P_PV"))
    grid = _num(site.get("P_Grid"))
    load = _num(site.get("P_Load"))
    return {
        "pv_w": max(pv or 0.0, 0.0),                       # nachts null
        "grid_w": grid,
        # P_Load ist bei Verbrauch negativ; ohne Wert aus PV + Netz ableiten
        "load_w": abs(load) if load is not None else (
            (pv or 0.0) + grid if grid is not None else None),
        "pv_total_wh": _num(site.get("E_Total")),
    }


def parse_meter(payload):
    data = payload.get("Body", {}).get("Data") or {}
    meter = next(iter(data.values()), {}) if isinstance(data, dict) else {}
    out = {}
    for name, keys in METER_KEYS.items():
        out[f"{name}_total_wh"] = next((_num(meter[k]) for k in keys if _num(meter.get(k)) is not None), None)
    return out


class FroniusSource:
    def __init__(self, host, timeout=5):
        self.base = f"http://{host}/solar_api/v1"
        self.timeout = timeout

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def read(self):
        reading = parse_powerflow(self._get("/GetPowerFlowRealtimeData.fcgi"))
        try:
            reading.update(parse_meter(self._get("/GetMeterRealtimeData.cgi?Scope=System")))
        except Exception:  # Zählerstände sind optional
            reading.update(import_total_wh=None, export_total_wh=None)
        return reading


class DemoSource:
    """Simulierte Anlage (ca. 20 kWp) zum Testen ohne Wechselrichter."""

    def __init__(self, peak_w=17000, seed=None):
        self.peak_w = peak_w
        self.rng = random.Random(seed)

    def read(self, now=None):
        now = now or datetime.now()
        h = now.hour + now.minute / 60
        season = 0.55 + 0.45 * math.cos((now.timetuple().tm_yday - 172) / 365 * 2 * math.pi)
        sun = max(0.0, math.sin(math.pi * (h - 6.5) / 13)) if 6.5 < h < 19.5 else 0.0
        clouds = 0.75 + 0.25 * self.rng.random()
        pv = self.peak_w * season * sun * clouds
        load = 1500 + (5500 if 7 <= h < 20 else 0) * (0.8 + 0.4 * self.rng.random())
        return {"pv_w": pv, "load_w": load, "grid_w": load - pv,
                "pv_total_wh": None, "import_total_wh": None, "export_total_wh": None}


def make_source(setting):
    setting = (setting or "").strip()
    if not setting:
        return None
    if setting.lower() == "demo":
        return DemoSource()
    return FroniusSource(setting)


# ---------------------------------------------------------------- Speichern

def _delta(new, old):
    if new is None or old is None:
        return None
    d = new - old
    return d if d >= 0 else None  # Zähler zurückgesetzt → ignorieren


def record(conn, reading, ts=None):
    """Speichert eine Abfrage: Minutenwert, Tagessummen und Zustand."""
    ts = int(ts or time.time())
    day = date.fromtimestamp(ts).isoformat()
    prev = conn.execute("SELECT * FROM pv_state WHERE id = 1").fetchone()

    pv_wh = imp_wh = exp_wh = 0.0
    if prev is not None and ts > prev["ts"]:
        dt = ts - prev["ts"]
        integrate = dt <= MAX_INTEGRATE_GAP

        def energy(counter_new, counter_old, power_new, power_old):
            d = _delta(counter_new, counter_old)
            if d is not None:
                return d
            if integrate and power_new is not None and power_old is not None:
                return max(0.0, (power_new + power_old) / 2) * dt / 3600
            return 0.0

        grid, pgrid = reading.get("grid_w"), prev["grid_w"]
        pv_wh = energy(reading.get("pv_total_wh"), prev["pv_total_wh"], reading.get("pv_w"), prev["pv_w"])
        imp_wh = energy(reading.get("import_total_wh"), prev["import_total_wh"],
                        max(grid, 0) if grid is not None else None,
                        max(pgrid, 0) if pgrid is not None else None)
        exp_wh = energy(reading.get("export_total_wh"), prev["export_total_wh"],
                        max(-grid, 0) if grid is not None else None,
                        max(-pgrid, 0) if pgrid is not None else None)

    conn.execute(
        """INSERT INTO pv_daily (day, pv_wh, import_wh, export_wh, peak_w) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(day) DO UPDATE SET pv_wh = pv_wh + excluded.pv_wh,
             import_wh = import_wh + excluded.import_wh, export_wh = export_wh + excluded.export_wh,
             peak_w = MAX(peak_w, excluded.peak_w)""",
        (day, pv_wh, imp_wh, exp_wh, reading.get("pv_w") or 0))
    conn.execute("INSERT OR REPLACE INTO pv_samples (ts, pv_w, load_w, grid_w) VALUES (?, ?, ?, ?)",
                 (ts, reading.get("pv_w"), reading.get("load_w"), reading.get("grid_w")))
    conn.execute(
        """INSERT OR REPLACE INTO pv_state (id, ts, pv_w, grid_w, pv_total_wh, import_total_wh, export_total_wh)
           VALUES (1, ?, ?, ?, ?, ?, ?)""",
        (ts, reading.get("pv_w"), reading.get("grid_w"), reading.get("pv_total_wh"),
         reading.get("import_total_wh"), reading.get("export_total_wh")))
    conn.execute("DELETE FROM pv_samples WHERE ts < ?", (ts - SAMPLE_DAYS * 86400,))
    conn.commit()


class Collector(threading.Thread):
    """Fragt die Anlage regelmäßig ab (läuft im Serverprozess)."""

    def __init__(self, connect, get_settings, interval=60):
        super().__init__(daemon=True, name="pv-collector")
        self.connect, self.get_settings, self.interval = connect, get_settings, interval
        self.last_error = None
        self.last_ok = None

    def run(self):
        while True:
            try:
                conn = self.connect()
                try:
                    source = make_source(self.get_settings(conn).get("pv_source"))
                    if source is not None:
                        record(conn, source.read())
                        self.last_ok, self.last_error = time.time(), None
                finally:
                    conn.close()
            except Exception as exc:  # Netzwerk, Wechselrichter aus, …
                self.last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(self.interval)


# ---------------------------------------------------------------- Auswertung

def _sums(conn, day_from, day_to):
    r = conn.execute(
        """SELECT COALESCE(SUM(pv_wh),0) pv, COALESCE(SUM(import_wh),0) imp,
                  COALESCE(SUM(export_wh),0) exp, COALESCE(MAX(peak_w),0) peak, COUNT(*) days
           FROM pv_daily WHERE day BETWEEN ? AND ?""", (day_from, day_to)).fetchone()
    pv, imp, exp = r["pv"], r["imp"], r["exp"]
    self_use = max(pv - exp, 0)
    load = self_use + imp
    return {
        "pv_kwh": pv / 1000, "import_kwh": imp / 1000, "export_kwh": exp / 1000,
        "self_use_kwh": self_use / 1000, "load_kwh": load / 1000, "peak_kw": r["peak"] / 1000,
        "self_use_share": self_use / pv if pv else None,     # Eigenverbrauchsquote
        "autarky": self_use / load if load else None,        # Autarkiegrad
        "days": r["days"],
    }


def overview(conn, today, settings, now=None):
    now = int(now or time.time())
    co2_g_kwh = float(settings.get("pv_co2_g_per_kwh", "344"))
    kwp = float(settings.get("pv_kwp", "0") or 0)
    year_start = today.replace(month=1, day=1).isoformat()

    state = conn.execute("SELECT * FROM pv_state WHERE id = 1").fetchone()
    live = None
    if state is not None and now - state["ts"] <= STALE_AFTER:
        sample = conn.execute("SELECT * FROM pv_samples WHERE ts = ?", (state["ts"],)).fetchone()
        live = {"ts": state["ts"], "pv_kw": (sample["pv_w"] or 0) / 1000,
                "load_kw": None if sample["load_w"] is None else sample["load_w"] / 1000,
                "grid_kw": None if sample["grid_w"] is None else sample["grid_w"] / 1000}

    day_t = _sums(conn, today.isoformat(), today.isoformat())
    year_t = _sums(conn, year_start, today.isoformat())
    year_t["co2_avoided_kg"] = year_t["pv_kwh"] * co2_g_kwh / 1000
    day_t["co2_avoided_kg"] = day_t["pv_kwh"] * co2_g_kwh / 1000
    year_t["specific_yield"] = year_t["pv_kwh"] / kwp if kwp else None

    months = {int(r["m"]): r["pv"] / 1000 for r in conn.execute(
        """SELECT CAST(substr(day, 6, 2) AS INTEGER) m, SUM(pv_wh) pv FROM pv_daily
           WHERE day BETWEEN ? AND ? GROUP BY m""", (year_start, today.isoformat()))}
    prev_months = {int(r["m"]): r["pv"] / 1000 for r in conn.execute(
        """SELECT CAST(substr(day, 6, 2) AS INTEGER) m, SUM(pv_wh) pv FROM pv_daily
           WHERE day BETWEEN ? AND ? GROUP BY m""", (f"{today.year - 1}-01-01", f"{today.year - 1}-12-31"))}

    # Tagesverlauf in 15-Minuten-Mitteln
    start = int(datetime(today.year, today.month, today.day).timestamp())
    curve = [{"t": int(r["slot"]) * 900 + start, "pv_kw": r["pv"] / 1000,
              "load_kw": None if r["load"] is None else r["load"] / 1000}
             for r in conn.execute(
                 """SELECT (ts - ?) / 900 slot, AVG(COALESCE(pv_w, 0)) pv, AVG(load_w) load
                    FROM pv_samples WHERE ts >= ? AND ts < ? GROUP BY slot ORDER BY slot""",
                 (start, start, start + 86400))]

    return {
        "configured": bool((settings.get("pv_source") or "").strip()),
        "demo": (settings.get("pv_source") or "").strip().lower() == "demo",
        "date": today.isoformat(), "year": today.year,
        "live": live, "last_ts": state["ts"] if state else None,
        "today": day_t, "year_total": year_t,
        "monthly": [months.get(m) for m in range(1, 13)],
        "monthly_prev": [prev_months.get(m) for m in range(1, 13)] if prev_months else None,
        "curve": curve, "day_start": start,
        "co2_g_per_kwh": co2_g_kwh,
        "ev_kwh_per_100km": float(settings.get("pv_ev_kwh_per_100km", "18")),
    }
