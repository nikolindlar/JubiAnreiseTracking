from datetime import date

import pytest

from anreise import create_app

PW = "geheim"
KIOSK_PW = "tablet"


@pytest.fixture
def app(tmp_path):
    state = {"today": date(2026, 10, 1)}
    app = create_app({
        "DATABASE": str(tmp_path / "test.sqlite3"),
        "SECRET_KEY": "test",
        "ADMIN_PASSWORD": PW,
        "KIOSK_PASSWORD": KIOSK_PW,
        "TODAY": lambda: state["today"],
        "TESTING": True,
    })
    app.state = state
    # Bestehende Tests prüfen Einzelbuchungen; die Datenschutz-Schwelle wird
    # in eigenen Tests geprüft
    set_privacy(app, 0)
    return app


def set_privacy(app, n):
    from anreise import db
    from anreise.app import get_db
    with app.app_context():
        conn = get_db()
        db.set_setting(conn, "privacy_min_trips_today", str(n))
        conn.commit()


@pytest.fixture
def client(app):
    return app.test_client()


def admin_login(client):
    client.post("/verwaltung/login", data={"password": PW})
    with client.session_transaction() as s:
        return s["csrf"]


def kiosk_login(client):
    return client.post("/erfassung/login", data={"password": KIOSK_PW})


def set_distance(client, csrf, emp_id, name, km):
    return client.post(f"/verwaltung/mitarbeitende/{emp_id}",
                       data={"csrf": csrf, "name": name, "distance_km": km,
                             "active": "on", "action": "save"})


def mode_id(app, label):
    from anreise.app import get_db
    with app.app_context():
        return get_db().execute("SELECT id FROM modes WHERE label = ?", (label,)).fetchone()[0]


def test_seed_employees_with_roundtrip_distance(app, client):
    from anreise.app import get_db
    with app.app_context():
        rows = get_db().execute("SELECT name, distance_km FROM employees").fetchall()
    assert {r["name"]: r["distance_km"] for r in rows} == {"Niko": 18, "Angela": 74, "Marlene": 34}
    kiosk_login(client)
    page = client.get("/erfassung").get_data(as_text=True)
    assert "Niko" in page and "Bus + Bahn" in page and "ÖPNV" not in page


def test_employee_without_distance_hidden(app, client):
    csrf = admin_login(client)
    set_distance(client, csrf, 1, "Niko", "0")
    page = client.get("/erfassung").get_data(as_text=True)
    assert "Niko" not in page and "Angela" in page


def test_arrival_aggregates_without_storing_name(app, client):
    csrf = admin_login(client)
    set_distance(client, csrf, 1, "Niko", "10")
    set_distance(client, csrf, 2, "Angela", "20")

    bike = mode_id(app, "Fahrrad/E-Bike")
    car = mode_id(app, "Auto (Verbrenner)")
    assert client.post("/api/anreise", json={"employee_id": 1, "mode_id": bike}).status_code == 200
    assert client.post("/api/anreise", json={"employee_id": 2, "mode_id": car}).status_code == 200

    d = client.get("/api/dashboard/anreise").get_json()
    assert d["today"]["trips"] == 2
    assert d["today"]["km"] == 30
    assert d["today"]["co2_g"] == pytest.approx(20 * 230)
    assert d["today"]["baseline_g"] == pytest.approx(30 * 230)

    # Datenbank enthält keine Einzelbuchungen mit Personenbezug
    from anreise.app import get_db
    with app.app_context():
        conn = get_db()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"employees", "modes", "daily_totals", "settings",
                          "pv_daily", "pv_samples", "pv_state"}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_totals)")}
        assert "employee_id" not in cols


def test_year_and_day_separation(app, client):
    csrf = admin_login(client)
    set_distance(client, csrf, 1, "Niko", "10")
    bike = mode_id(app, "Bus")
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": bike})
    app.state["today"] = date(2026, 10, 2)
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": bike})

    d = client.get("/api/dashboard/anreise").get_json()
    assert d["today"]["trips"] == 1
    assert d["year_total"]["trips"] == 2

    app.state["today"] = date(2027, 1, 1)
    d = client.get("/api/dashboard/anreise").get_json()
    assert d["year_total"]["trips"] == 0


def test_factor_change_does_not_rewrite_history(app, client):
    csrf = admin_login(client)
    set_distance(client, csrf, 1, "Niko", "10")
    bus = mode_id(app, "Bus")
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": bus})
    client.post(f"/verwaltung/verkehrsmittel/{bus}",
                data={"csrf": csrf, "label": "Bus", "factor_g": "100", "active": "on"})
    d = client.get("/api/dashboard/anreise").get_json()
    assert d["today"]["co2_g"] == pytest.approx(10 * 90)


def test_invalid_arrival_rejected(app, client):
    kiosk_login(client)
    assert client.post("/api/anreise", json={"employee_id": 999, "mode_id": 1}).status_code == 400
    assert client.post("/api/anreise", json={}).status_code == 400


def test_admin_requires_login_and_csrf(client):
    assert client.get("/verwaltung").status_code == 302
    admin_login(client)
    assert client.get("/verwaltung").status_code == 200
    r = client.post("/verwaltung/mitarbeitende", data={"name": "X", "csrf": "falsch"})
    assert r.status_code == 400


def test_wrong_password(client):
    r = client.post("/verwaltung/login", data={"password": "nein"})
    assert "Falsches Passwort" in r.get_data(as_text=True)
    assert client.get("/verwaltung").status_code == 302


def test_csv_export(app, client):
    csrf = admin_login(client)
    set_distance(client, csrf, 1, "Niko", "10")
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": mode_id(app, "E-Auto")})
    body = client.get("/verwaltung/export.csv").get_data(as_text=True)
    assert "2026-10-01;E-Auto;1;10,0;0,98;2,30" in body
    assert "Niko" not in body


def test_kiosk_requires_login(app, client):
    r = client.get("/erfassung")
    assert r.status_code == 302 and r.headers["Location"].endswith("/erfassung/login")
    r = client.post("/api/anreise", json={"employee_id": 1, "mode_id": mode_id(app, "Bus")})
    assert r.status_code == 401
    assert client.get("/api/dashboard/anreise").get_json()["today"]["trips"] == 0
    # Übersicht bleibt öffentlich
    assert client.get("/dashboard/anreise").status_code == 200


def test_kiosk_login_is_persistent_and_limited(app, client):
    r = client.post("/erfassung/login", data={"password": "falsch"})
    assert "Falsches Passwort" in r.get_data(as_text=True)
    r = kiosk_login(client)
    assert r.status_code == 302
    assert "Expires=" in r.headers["Set-Cookie"]  # überlebt Browser-Neustart
    assert client.get("/erfassung").status_code == 200
    r = client.post("/api/anreise", json={"employee_id": 1, "mode_id": mode_id(app, "Bus")})
    assert r.status_code == 200
    # Erfassungspasswort öffnet nicht die Verwaltung
    assert client.get("/verwaltung").status_code == 302
    assert client.post("/verwaltung/login", data={"password": KIOSK_PW}).status_code == 200
    assert client.get("/verwaltung").status_code == 302


def test_admin_logout_keeps_kiosk_login(client):
    kiosk_login(client)
    csrf = admin_login(client)
    assert client.get("/verwaltung").status_code == 200
    client.post("/verwaltung/logout", data={"csrf": csrf})
    assert client.get("/verwaltung").status_code == 302
    assert client.get("/erfassung").status_code == 200


def test_admin_session_expires(app, client):
    admin_login(client)
    with client.session_transaction() as s:
        s["admin_until"] = 1  # längst abgelaufen
    assert client.get("/verwaltung").status_code == 302
    assert client.get("/erfassung").status_code == 302


def test_missing_passwords_block_login(tmp_path):
    app = create_app({"DATABASE": str(tmp_path / "x.sqlite3"),
                      "ADMIN_PASSWORD": "", "KIOSK_PASSWORD": ""})
    c = app.test_client()
    assert "Kein Erfassungspasswort" in c.post("/erfassung/login", data={"password": ""}).get_data(as_text=True)
    assert "Kein Verwaltungspasswort" in c.post("/verwaltung/login", data={"password": ""}).get_data(as_text=True)
    assert c.get("/erfassung").status_code == 302


def test_secret_key_persisted_next_to_db(tmp_path):
    cfg = {"DATABASE": str(tmp_path / "x.sqlite3"), "SECRET_KEY": ""}
    k1 = create_app(cfg).config["SECRET_KEY"]
    k2 = create_app(cfg).config["SECRET_KEY"]
    assert k1 and k1 == k2
    assert (tmp_path / "secret_key").read_text() == k1


def test_undo_reverts_arrival(app, client):
    kiosk_login(client)
    bus = mode_id(app, "Bus")
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": bus})
    r = client.post("/api/anreise", json={"employee_id": 2, "mode_id": bus}).get_json()
    assert r["undo_seconds"] == 20 and r["undo_token"]
    assert client.post("/api/anreise/storno", json={"undo_token": r["undo_token"]}).get_json()["ok"]
    t = client.get("/api/dashboard/anreise").get_json()["today"]
    assert t["trips"] == 1 and t["km"] == 18
    assert t["co2_g"] == pytest.approx(18 * 90)
    # Marke nur einmal verwendbar
    assert client.post("/api/anreise/storno", json={"undo_token": r["undo_token"]}).status_code == 410


def test_undo_last_arrival_removes_row(app, client):
    kiosk_login(client)
    r = client.post("/api/anreise", json={"employee_id": 1, "mode_id": mode_id(app, "Bus")}).get_json()
    client.post("/api/anreise/storno", json={"undo_token": r["undo_token"]})
    from anreise.app import get_db
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM daily_totals").fetchone()[0] == 0


def test_undo_expires(app, client):
    app.config["UNDO_SECONDS"] = 0
    app.config["UNDO_GRACE_SECONDS"] = -1
    kiosk_login(client)
    r = client.post("/api/anreise", json={"employee_id": 1, "mode_id": mode_id(app, "Bus")}).get_json()
    assert client.post("/api/anreise/storno", json={"undo_token": r["undo_token"]}).status_code == 410
    assert client.get("/api/dashboard/anreise").get_json()["today"]["trips"] == 1


def test_undo_requires_login_and_valid_token(app, client):
    kiosk_login(client)
    r = client.post("/api/anreise", json={"employee_id": 1, "mode_id": mode_id(app, "Bus")}).get_json()
    other = app.test_client()
    assert other.post("/api/anreise/storno", json={"undo_token": r["undo_token"]}).status_code == 401
    assert client.post("/api/anreise/storno", json={"undo_token": "falsch"}).status_code == 410
    assert client.post("/api/anreise/storno", json={}).status_code == 410
    assert client.get("/api/dashboard/anreise").get_json()["today"]["trips"] == 1


def test_comparisons():
    from anreise.vergleiche import vergleich, STRECKEN, FLUEGE
    assert vergleich(0, STRECKEN) is None
    c = vergleich(57, STRECKEN)
    assert c["label"] == "München–Jubi" and c["factor"] == pytest.approx(0.5)
    c = vergleich(112236, STRECKEN)
    assert c["label"] == "um die Erde" and c["factor"] == pytest.approx(2.8, abs=0.01)
    c = vergleich(1216 * 210 * 2, FLUEGE)
    assert c["label"] == "München–Mallorca" and c["factor"] == pytest.approx(2)


def test_overview_stats(app, client):
    from datetime import timedelta
    kiosk_login(client)
    ids = {l: mode_id(app, l) for l in ["Fahrrad/E-Bike", "Bus", "Auto (Verbrenner)"]}
    # 4 weitere Mitarbeitende, damit 5 Anreisen pro Tag möglich sind
    csrf = admin_login(client)
    for i in range(4):
        client.post("/verwaltung/mitarbeitende", data={"csrf": csrf, "name": f"P{i}", "distance_km": "10"})

    def day(d, green):  # 5 Anreisen, davon `green` klimafreundlich
        app.state["today"] = d
        for emp in range(1, 6):
            mode = ids["Fahrrad/E-Bike"] if emp <= green else ids["Auto (Verbrenner)"]
            client.post("/api/anreise", json={"employee_id": emp, "mode_id": mode})

    start = date(2026, 3, 2)
    day(start, 3)                        # 60 %
    day(start + timedelta(days=1), 4)    # 80 % -> bester Tag
    day(start + timedelta(days=2), 1)    # 20 % -> Serie bricht
    day(start + timedelta(days=7), 3)
    day(start + timedelta(days=8), 3)
    day(start + timedelta(days=9), 5)    # 100 % -> neuer bester Tag, Serie 3
    app.state["today"] = start + timedelta(days=10)
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": ids["Bus"]})  # nur 1 Anreise: zählt nicht

    d = client.get("/api/dashboard/anreise").get_json()
    r = d["records"]
    assert r["best_day"] == {"day": "2026-03-11", "share": 1.0}
    assert r["longest_streak"] == 3
    assert r["days_counted"] == 6
    assert d["staff"] == 7
    assert d["today"]["trips"] == 1
    y = d["year_total"]
    assert y["trips"] == 31
    assert y["green_share"] == pytest.approx(20 / 31)
    # Rad-km: Strecken 18, 74, 34, 10, 10; je Tag die ersten `green` Personen
    assert d["human_km"] == pytest.approx(126 + 136 + 18 + 126 + 126 + 146)
    assert y["avg_g_per_km"] == pytest.approx(y["co2_g"] / y["km"])
    assert d["compare"]["distance"]["label"]
    modes = {m["label"]: m for m in d["by_mode"]}
    assert modes["Bus"]["trips"] == 1 and modes["Bus"]["is_green"] == 1 and modes["Bus"]["is_human"] == 0


def test_migration_adds_mode_flags(tmp_path):
    import sqlite3
    path = tmp_path / "alt.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE modes (id INTEGER PRIMARY KEY, label TEXT NOT NULL, icon TEXT NOT NULL DEFAULT '',
            factor_g REAL NOT NULL, source TEXT NOT NULL DEFAULT '', sort INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1, is_baseline INTEGER NOT NULL DEFAULT 0);
        INSERT INTO modes (label, factor_g) VALUES ('Fahrrad/E-Bike', 0), ('Bus', 90), ('E-Auto', 98);
    """)
    conn.close()
    app = create_app({"DATABASE": str(path), "SECRET_KEY": "x"})
    from anreise.app import get_db
    with app.app_context():
        rows = {r["label"]: (r["is_green"], r["is_human"])
                for r in get_db().execute("SELECT label, is_green, is_human FROM modes")}
    assert rows == {"Fahrrad/E-Bike": (1, 1), "Bus": (1, 0), "E-Auto": (0, 0)}


def test_monthly_trend_and_previous_year(app, client):
    kiosk_login(client)
    bus, car = mode_id(app, "Bus"), mode_id(app, "Auto (Verbrenner)")
    app.state["today"] = date(2025, 6, 10)
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": car})   # 2025: 230 g/km
    app.state["today"] = date(2026, 2, 3)
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": car})
    app.state["today"] = date(2026, 4, 1)
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": bus})
    d = client.get("/api/dashboard/anreise").get_json()
    avg = [m["avg_g_per_km"] for m in d["monthly"]]
    assert len(avg) == 12
    assert avg[1] == pytest.approx(230) and avg[3] == pytest.approx(90)
    assert avg[0] is None and avg[2] is None and avg[11] is None
    assert d["prev_year"] == {"year": 2025, "avg_g_per_km": pytest.approx(230), "trips": 1}


def test_previous_year_empty(app, client):
    d = client.get("/api/dashboard/anreise").get_json()
    assert d["prev_year"]["avg_g_per_km"] is None


def test_nutrition_counter(app, client):
    d = client.get("/api/dashboard/verpflegung").get_json()  # heute: 1.10.2026
    assert d["open_days_total"] == 334            # Jan–Nov 2026
    assert d["open_days_to_date"] == 274          # 1.1.–1.10.
    assert d["days_to_date"] == pytest.approx(20000 * 274 / 334)
    assert d["veg_kg"] == pytest.approx(d["days_to_date"] * 3.81)
    assert d["saved_kg"] == pytest.approx(d["days_to_date"] * (5.63 - 3.81))
    assert d["year_saved_kg"] == pytest.approx(20000 * 1.82)
    assert d["organic_share"] == 50 and d["open_today"] is True
    assert d["compare"]["label"].startswith("München–")


def test_nutrition_closed_month(app, client):
    app.state["today"] = date(2026, 12, 15)
    d = client.get("/api/dashboard/verpflegung").get_json()
    assert d["open_today"] is False
    assert d["days_to_date"] == pytest.approx(20000)


def test_settings_update(app, client):
    csrf = admin_login(client)
    r = client.post("/verwaltung/einstellungen", data={
        "csrf": csrf, "meals_days_per_year": "18000", "meals_organic_share": "60,5",
        "meals_veg_kg_per_day": "3,5", "meals_mixed_kg_per_day": "5,5",
        "closed_1": "on", "closed_12": "on", "screen_rotation_seconds": "45"})
    assert r.status_code == 302
    app.state["today"] = date(2026, 1, 20)
    d = client.get("/api/dashboard/verpflegung").get_json()
    assert d["open_today"] is False and d["days_to_date"] == 0
    assert d["open_days_total"] == 365 - 31 - 31
    assert d["organic_share"] == 60.5 and d["days_per_year"] == 18000
    page = client.get("/verwaltung").get_data(as_text=True)
    assert 'name="closed_1" checked' in page and 'name="closed_2" checked' not in page
    # Einstellungen nur mit Anmeldung
    other = app.test_client()
    assert other.post("/verwaltung/einstellungen", data={"meals_days_per_year": "1"}).status_code == 302


def test_screen_rotation_only_with_empfang(client):
    page = client.get("/dashboard/anreise").get_data(as_text=True)
    assert "rotate-timer" not in page
    page = client.get("/dashboard/anreise?empfang").get_data(as_text=True)
    assert '"/dashboard/verpflegung?empfang=1"' in page
    assert "Weiter zum Verpflegungs-Dashboard in" in page and "var total = 30" in page
    page = client.get("/dashboard/verpflegung?empfang=1").get_data(as_text=True)
    assert '"/dashboard/anreise?empfang=1"' in page


def test_dashboard_urls_and_redirects(client):
    assert "<title>Anreise-Dashboard</title>" in client.get("/dashboard/anreise").get_data(as_text=True)
    assert "<title>Verpflegungs-Dashboard</title>" in client.get("/dashboard/verpflegung").get_data(as_text=True)
    # Alte Adressen und Startseite leiten weiter, ?empfang bleibt erhalten
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard/anreise")
    r = client.get("/?empfang")
    assert r.headers["Location"].endswith("/dashboard/anreise?empfang=")
    r = client.get("/ernaehrung?empfang=1")
    assert r.headers["Location"].endswith("/dashboard/verpflegung?empfang=1")


def test_privacy_threshold_hides_today_everywhere(app, client):
    set_privacy(app, 3)
    kiosk_login(client)
    bike, car = mode_id(app, "Fahrrad/E-Bike"), mode_id(app, "Auto (Verbrenner)")
    app.state["today"] = date(2026, 9, 30)
    client.post("/api/anreise", json={"employee_id": 2, "mode_id": car})   # Vortag
    app.state["today"] = date(2026, 10, 1)
    before = client.get("/api/dashboard/anreise").get_json()

    for emp in (1, 3):  # zwei Anreisen heute: noch unter der Schwelle
        client.post("/api/anreise", json={"employee_id": emp, "mode_id": bike})
        d = client.get("/api/dashboard/anreise").get_json()
        assert d["today"]["hidden"] is True and d["today"]["trips"] == 0
        # Nichts darf sich gegenüber vorher verändern
        for key in ("year_total", "by_mode", "human_km", "monthly", "records", "compare"):
            assert d[key] == before[key], key

    client.post("/api/anreise", json={"employee_id": 2, "mode_id": car})  # dritte Anreise
    d = client.get("/api/dashboard/anreise").get_json()
    assert d["today"]["hidden"] is False and d["today"]["trips"] == 3
    assert d["year_total"]["trips"] == 4
    assert d["human_km"] == pytest.approx(18 + 34)


def test_privacy_threshold_reapplies_after_undo(app, client):
    set_privacy(app, 3)
    kiosk_login(client)
    bus = mode_id(app, "Bus")
    tokens = [client.post("/api/anreise", json={"employee_id": e, "mode_id": bus}).get_json()["undo_token"]
              for e in (1, 2, 3)]
    assert client.get("/api/dashboard/anreise").get_json()["today"]["hidden"] is False
    client.post("/api/anreise/storno", json={"undo_token": tokens[-1]})
    d = client.get("/api/dashboard/anreise").get_json()
    assert d["today"]["hidden"] is True and d["year_total"]["trips"] == 0


def test_privacy_default_and_setting(app, client):
    from anreise import db
    assert db.DEFAULT_SETTINGS["privacy_min_trips_today"] == "3"
    csrf = admin_login(client)
    client.post("/verwaltung/einstellungen", data={
        "csrf": csrf, "meals_days_per_year": "20000", "meals_organic_share": "50",
        "meals_veg_kg_per_day": "3.81", "meals_mixed_kg_per_day": "5.63", "closed_12": "on",
        "screen_rotation_seconds": "30", "privacy_min_trips_today": "5"})
    assert client.get("/api/dashboard/anreise").get_json()["min_trips_today"] == 5
