import time
from datetime import date, datetime

import pytest

from anreise import create_app, db, pv

POWERFLOW = {  # Format laut Fronius Solar API v1 (GEN24/Verto: E_Day/E_Year = null)
    "Body": {"Data": {"Site": {
        "Mode": "meter", "P_PV": 12000.0, "P_Grid": -4500.0, "P_Load": -7500.0, "P_Akku": None,
        "E_Day": None, "E_Year": None, "E_Total": 1_000_000.0,
        "rel_Autonomy": 100.0, "rel_SelfConsumption": 62.5}}},
    "Head": {"Status": {"Code": 0}},
}


def meter(consumed, produced, new_names=False):
    keys = (("SMARTMETER_ENERGYACTIVE_CONSUMED_SUM_F64", "SMARTMETER_ENERGYACTIVE_PRODUCED_SUM_F64")
            if new_names else ("EnergyReal_WAC_Sum_Consumed", "EnergyReal_WAC_Sum_Produced"))
    return {"Body": {"Data": {"0": {keys[0]: consumed, keys[1]: produced}}}}


def test_parse_powerflow_and_meter():
    r = pv.parse_powerflow(POWERFLOW)
    assert r == {"pv_w": 12000.0, "grid_w": -4500.0, "load_w": 7500.0, "pv_total_wh": 1_000_000.0}
    night = {"Body": {"Data": {"Site": {"P_PV": None, "P_Grid": 800.0, "P_Load": None, "E_Total": 5.0}}}}
    assert pv.parse_powerflow(night) == {"pv_w": 0.0, "grid_w": 800.0, "load_w": 800.0, "pv_total_wh": 5.0}
    assert pv.parse_meter(meter(100.0, 200.0)) == {"import_total_wh": 100.0, "export_total_wh": 200.0}
    assert pv.parse_meter(meter(100.0, 200.0, new_names=True)) == {"import_total_wh": 100.0, "export_total_wh": 200.0}
    assert pv.parse_meter({"Body": {"Data": {}}}) == {"import_total_wh": None, "export_total_wh": None}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "pv.sqlite3"))
    db.init_db(c)
    pv.init_db(c)
    yield c
    c.close()


def ts(h, m=0, d=date(2026, 6, 15)):
    return int(datetime(d.year, d.month, d.day, h, m).timestamp())


def reading(pv_w, grid_w, pv_total=None, imp=None, exp=None):
    return {"pv_w": pv_w, "grid_w": grid_w, "load_w": pv_w + grid_w,
            "pv_total_wh": pv_total, "import_total_wh": imp, "export_total_wh": exp}


def test_record_uses_counters(conn):
    pv.record(conn, reading(10000, -4000, 1000, 50, 20), ts(12))
    pv.record(conn, reading(10000, -4000, 3000, 60, 1520), ts(12, 10))
    d = conn.execute("SELECT * FROM pv_daily").fetchone()
    assert (d["pv_wh"], d["import_wh"], d["export_wh"], d["peak_w"]) == (2000, 10, 1500, 10000)


def test_record_integrates_power_without_counters(conn):
    pv.record(conn, reading(6000, -2000), ts(12))
    pv.record(conn, reading(6000, -2000), ts(12, 10))   # 10 min
    pv.record(conn, reading(0, 1000), ts(13, 10))       # Lücke > 15 min → nicht integrieren
    d = conn.execute("SELECT * FROM pv_daily").fetchone()
    assert d["pv_wh"] == pytest.approx(1000) and d["export_wh"] == pytest.approx(2000 / 6)
    assert d["import_wh"] == 0


def test_record_ignores_counter_reset(conn):
    pv.record(conn, reading(1000, 0, 5000, 0, 0), ts(12))
    pv.record(conn, reading(1000, 0, 10, 0, 0), ts(12, 1))
    assert conn.execute("SELECT pv_wh FROM pv_daily").fetchone()[0] == pytest.approx(1000 / 60, rel=0.01)


def test_overview_quotes(conn):
    pv.record(conn, reading(0, 0, 0, 0, 0), ts(8))
    pv.record(conn, reading(8000, -3000, 10000, 1000, 6000), ts(8, 20))  # 10 kWh PV, 6 Einspeisung, 1 Bezug
    settings = {**db.DEFAULT_SETTINGS, "pv_source": "demo", "pv_kwp": "20"}
    o = pv.overview(conn, date(2026, 6, 15), settings, now=ts(8, 22))
    y = o["year_total"]
    assert y["pv_kwh"] == 10 and y["export_kwh"] == 6 and y["import_kwh"] == 1
    assert y["self_use_kwh"] == 4 and y["load_kwh"] == 5
    assert y["self_use_share"] == pytest.approx(0.4) and y["autarky"] == pytest.approx(0.8)
    assert y["co2_avoided_kg"] == pytest.approx(10 * 0.344)
    assert y["specific_yield"] == pytest.approx(0.5)
    assert o["live"]["pv_kw"] == 8 and o["live"]["grid_kw"] == -3
    assert o["monthly"][5] == 10 and o["monthly"][0] is None
    assert len(o["curve"]) == 2
    # Live-Werte veralten
    assert pv.overview(conn, date(2026, 6, 15), settings, now=ts(9))["live"] is None


def test_demo_source_is_plausible():
    src = pv.DemoSource(seed=1)
    noon, night = src.read(datetime(2026, 6, 15, 13)), src.read(datetime(2026, 6, 15, 2))
    assert 5000 < noon["pv_w"] <= 17000 and night["pv_w"] == 0
    assert noon["grid_w"] == pytest.approx(noon["load_w"] - noon["pv_w"])


@pytest.fixture
def app(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "a.sqlite3"), "SECRET_KEY": "x",
                       "ADMIN_PASSWORD": "pw", "TESTING": True, "TODAY": lambda: date(2026, 6, 15)})


def test_collector_not_started_in_tests(app):
    assert app.extensions["pv_collector"] is None


def test_pv_dashboard_not_configured(app):
    c = app.test_client()
    assert c.get("/dashboard/pv").status_code == 200
    d = c.get("/api/dashboard/pv").get_json()
    assert d["configured"] is False and d["live"] is None
    # Ohne Anlage nicht im Seitenwechsel
    assert "PV-Dashboard" not in c.get("/dashboard/verpflegung?empfang=1").get_data(as_text=True)


def test_pv_settings_and_rotation(app):
    c = app.test_client()
    c.post("/verwaltung/login", data={"password": "pw"})
    with c.session_transaction() as s:
        csrf = s["csrf"]
    c.post("/verwaltung/pv", data={"csrf": csrf, "pv_source": " demo ", "pv_kwp": "19,8",
                                   "pv_co2_g_per_kwh": "350", "pv_ev_kwh_per_100km": "17"})
    s = db.get_settings(db.connect(app.config["DATABASE"]))
    assert (s["pv_source"], s["pv_kwp"], s["pv_co2_g_per_kwh"], s["pv_ev_kwh_per_100km"]) == ("demo", "19.8", "350", "17")
    page = c.get("/dashboard/verpflegung?empfang=1").get_data(as_text=True)
    assert '"/dashboard/pv?empfang=1"' in page and "Weiter zum PV-Dashboard" in page
    assert '"/dashboard/anreise?empfang=1"' in c.get("/dashboard/pv?empfang=1").get_data(as_text=True)
    d = c.get("/api/dashboard/pv").get_json()
    assert d["configured"] and d["demo"] and d["co2_g_per_kwh"] == 350
    assert "PV-Anlage" in c.get("/verwaltung").get_data(as_text=True)
