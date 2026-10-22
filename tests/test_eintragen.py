import re

from test_app import admin_login, app, client, kiosk_login  # noqa: F401  (Fixtures)


def token(app):
    from anreise import db
    from anreise.app import get_db
    with app.app_context():
        return db.get_settings(get_db())["signup_token"]


def employee(app, name):
    from anreise.app import get_db
    with app.app_context():
        r = get_db().execute("SELECT * FROM employees WHERE name = ?", (name,)).fetchone()
        return dict(r) if r else None


def test_signup_page_and_entry(app, client):
    url = f"/eintragen/{token(app)}"
    page = client.get(url).get_data(as_text=True)
    assert "Welcher Name soll angezeigt werden?" in page
    assert "Wie weit fährst du jeden Tag? Hin- und Rückfahrt, in km" in page
    assert 'name="robots" content="noindex' in page

    r = client.post(url, data={"name": "  Kiki   B. ", "distance_km": "23,5"})
    assert r.status_code == 302
    assert "erscheint ab sofort" in client.get(r.headers["Location"]).get_data(as_text=True)
    e = employee(app, "Kiki B.")
    assert e["distance_km"] == 23.5 and e["active"] == 1 and e["self_added"]

    # Erscheint sofort auf dem Tablet, ohne Anmeldung keine Erfassung nötig
    kiosk_login(client)
    assert "Kiki B." in client.get("/erfassung").get_data(as_text=True)
    # Verwaltung zeigt die Markierung
    admin_login(client)
    assert "selbst eingetragen" in client.get("/verwaltung").get_data(as_text=True)


def test_signup_validation(app, client):
    url = f"/eintragen/{token(app)}"
    cases = [({"name": "", "distance_km": "10"}, "Bitte einen Namen"),
             ({"name": "x" * 31, "distance_km": "10"}, "höchstens 30 Zeichen"),
             ({"name": "Neu", "distance_km": "0"}, "zwischen 0 und 400"),
             ({"name": "Neu", "distance_km": "viel"}, "zwischen 0 und 400"),
             ({"name": "Neu", "distance_km": "-5"}, "zwischen 0 und 400"),
             ({"name": "Neu", "distance_km": "401"}, "zwischen 0 und 400"),
             ({"name": "niko", "distance_km": "10"}, "gibt es schon")]
    for data, message in cases:
        r = client.post(url, data=data)
        assert r.status_code == 200 and message in r.get_data(as_text=True), data
    assert employee(app, "Neu") is None
    # Eingaben bleiben nach einem Fehler im Formular stehen
    page = client.post(url, data={"name": "Neu", "distance_km": "viel"}).get_data(as_text=True)
    assert 'value="Neu"' in page and 'value="viel"' in page


def test_signup_wrong_or_renewed_token(app, client):
    old = token(app)
    assert client.get("/eintragen/falsch").status_code == 404
    assert client.post("/eintragen/falsch", data={"name": "X", "distance_km": "5"}).status_code == 404
    csrf = admin_login(client)
    page = client.get("/verwaltung").get_data(as_text=True)
    assert f"/eintragen/{old}" in page
    # Nur mit Anmeldung und CSRF-Token
    assert app.test_client().post("/verwaltung/eintragen-link").status_code == 302
    assert client.post("/verwaltung/eintragen-link", data={}).status_code == 400
    assert client.post("/verwaltung/eintragen-link", data={"csrf": csrf}).status_code == 302
    new = token(app)
    assert new != old and re.fullmatch(r"[\w-]{12}", new)
    assert client.get(f"/eintragen/{old}").status_code == 404
    assert client.get(f"/eintragen/{new}").status_code == 200
