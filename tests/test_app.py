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
    return app


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

    d = client.get("/api/uebersicht").get_json()
    assert d["today"]["trips"] == 2
    assert d["today"]["km"] == 30
    assert d["today"]["co2_g"] == pytest.approx(20 * 230)
    assert d["today"]["baseline_g"] == pytest.approx(30 * 230)

    # Datenbank enthält keine Einzelbuchungen mit Personenbezug
    from anreise.app import get_db
    with app.app_context():
        conn = get_db()
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"employees", "modes", "daily_totals"}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(daily_totals)")}
        assert "employee_id" not in cols


def test_year_and_day_separation(app, client):
    csrf = admin_login(client)
    set_distance(client, csrf, 1, "Niko", "10")
    bike = mode_id(app, "Bus")
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": bike})
    app.state["today"] = date(2026, 10, 2)
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": bike})

    d = client.get("/api/uebersicht").get_json()
    assert d["today"]["trips"] == 1
    assert d["year_total"]["trips"] == 2

    app.state["today"] = date(2027, 1, 1)
    d = client.get("/api/uebersicht").get_json()
    assert d["year_total"]["trips"] == 0


def test_factor_change_does_not_rewrite_history(app, client):
    csrf = admin_login(client)
    set_distance(client, csrf, 1, "Niko", "10")
    bus = mode_id(app, "Bus")
    client.post("/api/anreise", json={"employee_id": 1, "mode_id": bus})
    client.post(f"/verwaltung/verkehrsmittel/{bus}",
                data={"csrf": csrf, "label": "Bus", "factor_g": "100", "active": "on"})
    d = client.get("/api/uebersicht").get_json()
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
    assert client.get("/api/uebersicht").get_json()["today"]["trips"] == 0
    # Übersicht bleibt öffentlich
    assert client.get("/").status_code == 200


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
