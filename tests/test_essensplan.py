import calendar
import html
import io
import json
import re
from datetime import date

import openpyxl
import pytest

from anreise import essensplan
from test_app import admin_login, app, client  # noqa: F401  (Fixtures)

WD = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
TODAY = date(2026, 10, 8)
NEW_ROWS = ["Anreise", "Abreise", "Frühstück", "Mittagessen", "Lunch", "Kaffee", "Kuchen",
            "Abendessen", "Brotzeit"]


def save(wb):
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def fill_sheet(ws, year, month, values, weekday_shift=0, n_days=None, labels=NEW_ROWS, first_row=1):
    """Nachbau des Hauswirtschafts-Monatsplans: Tage in Zeile 1, Wochentage in
    Zeile 2, ab Zeile 3 die Beschriftungen in Spalte A."""
    n_days = n_days or calendar.monthrange(year, month)[1]
    for d in range(1, n_days + 1):
        ws.cell(first_row, d + 1, d)
        ws.cell(first_row + 1, d + 1, WD[(date(year, month, d).weekday() + weekday_shift) % 7])
    for i, label in enumerate(labels):
        ws.cell(first_row + 2 + i, 1, label)
    for (label, d), v in values.items():
        ws.cell(first_row + 2 + labels.index(label), d + 1, v)


def make_new(sheets):
    """sheets: Liste (Blattname, Jahr, Monat, Werte, kwargs)"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, year, month, values, kwargs in sheets:
        fill_sheet(wb.create_sheet(title), year, month, values, **kwargs)
    return save(wb)


def make_old(values=None, weekday_shift=0):
    """Ältere Vorlage „Dienstplan Hauswirtschaft“ mit Monat/Jahr-Feldern, ohne Lunch."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"] = "Dienstplan Hauswirtschaft"
    ws["G1"] = "Monat:"
    ws["J1"] = "November"
    ws["W1"] = "Jahr:"
    ws["Y1"] = 2026
    labels = ["Abreise", "Frühstück", "Mittagesssen", "Abendessen"]
    fill_sheet(ws, 2026, 11, {}, weekday_shift=weekday_shift, labels=[], first_row=3)
    for i, label in enumerate(labels):
        ws.cell(6 + i, 1, label)
    for (label, d), v in (values or {}).items():
        ws.cell(6 + labels.index(label), d + 1, v)
    return save(wb)


OKT = {("Anreise", 1): 16, ("Frühstück", 1): 42, ("Lunch", 1): 42, ("Abendessen", 1): 58,
       ("Abreise", 2): 33, ("Frühstück", 2): 64, ("Lunch", 2): 64, ("Kaffee", 2): 6,
       ("Kuchen", 2): 6, ("Abendessen", 2): 45,
       ("Frühstück", 8): 81, ("Mittagessen", 8): 10, ("Lunch", 8): 81, ("Brotzeit", 8): 20,
       ("Abendessen", 8): 81}
SEP = {("Frühstück", 30): 43, ("Lunch", 30): 70, ("Abendessen", 30): 42}


def new_file(extra=()):
    return make_new([("Hauswirtschaftsplan Okt", 2026, 10, OKT, {}),
                     ("Hauswirtschaftsplan Sep", 2026, 9, SEP, {}), *extra])


def test_parse_new_template_multiple_sheets():
    res = essensplan.parse(new_file(), TODAY)
    assert [r["plan"]["key"] for r in res] == ["2026-10", "2026-09"]
    okt = res[0]["plan"]
    assert okt["label"] == "Oktober 2026" and len(okt["days"]) == 31 and okt["warnings"] == []
    assert okt["days"][1] == {"day": "2026-10-02", "arrivals": 0, "departures": 33, "breakfast": 64,
                              "lunch": 0, "packs": 64, "coffee": 6, "cake": 6, "dinner": 45, "snack": 0}
    assert okt["days"][7]["snack"] == 20 and okt["days"][7]["packs"] == 81
    s = essensplan.summary(okt["days"])
    assert s["meals"] == 42 + 42 + 58 + 64 + 64 + 6 + 6 + 45 + 81 + 10 + 81 + 20 + 81
    # Lunch explizit, nicht Frühstück − Mittagessen
    assert res[1]["plan"]["days"][29]["packs"] == 70


def test_year_from_weekdays_and_title():
    # Ohne Feld „Jahr:“ wird das Jahr aus den Wochentagen bestimmt
    res = essensplan.parse(make_new([("Plan Okt", 2027, 10, {}, {})]), TODAY)
    assert res[0]["plan"]["key"] == "2027-10"
    assert "nicht für das laufende Jahr 2026" in res[0]["plan"]["warnings"][0]
    assert res[0]["plan"]["notes"] == ["Jahr 2027 aus den Wochentagen bestimmt."]
    # Jahreszahl im Blattnamen hat Vorrang und wird gegen die Wochentage geprüft
    res = essensplan.parse(make_new([("Plan Okt 2025", 2026, 10, {}, {})]), TODAY)
    assert "Wochentag passt nicht" in res[0]["error"]


def test_old_template_still_supported():
    res = essensplan.parse(make_old({("Frühstück", 6): 43, ("Mittagesssen", 6): 13,
                                     ("Abendessen", 6): 43}), TODAY)
    plan = res[0]["plan"]
    assert plan["key"] == "2026-11"
    assert plan["days"][5]["packs"] == 30 and plan["days"][5]["coffee"] == 0
    assert plan["notes"] and "Frühstück − Mittagessen" in plan["notes"][0]
    res = essensplan.parse(make_old(weekday_shift=1), TODAY)
    assert "Wochentag passt nicht" in res[0]["error"]


@pytest.mark.parametrize("title, kwargs, message", [
    ("Plan Okt", {"weekday_shift": 3}, "Jahr nicht erkannt"),
    ("Plan", {}, "Monat nicht erkannt"),
    ("Plan Okt", {"labels": ["Frühstück", "Abendessen"]}, "Mittagessen“ nicht gefunden"),
    ("Plan Okt", {"n_days": 30}, "fehlen die Tage 31"),
])
def test_sheet_errors(title, kwargs, message):
    res = essensplan.parse(make_new([(title, 2026, 10, {}, kwargs)]), TODAY)
    assert message in res[0]["error"]


def test_invalid_cell_and_duplicate_month():
    res = essensplan.parse(make_new([("Okt", 2026, 10, {("Kaffee", 3): "viele"}, {})]), TODAY)
    assert "Kaffee am 3. (Zelle D8)" in res[0]["error"]
    res = essensplan.parse(make_new([("Okt", 2026, 10, {}, {}), ("Oktober", 2026, 10, {}, {})]), TODAY)
    assert all("mehreren Blättern" in r["error"] for r in res)


def test_not_excel_and_no_plan():
    with pytest.raises(essensplan.PlanError, match="keine lesbare Excel"):
        essensplan.parse(io.BytesIO(b"kein excel"), TODAY)
    wb = openpyxl.Workbook()
    wb.active["A1"] = "Notizen"
    with pytest.raises(essensplan.PlanError, match="Kein Blatt"):
        essensplan.parse(save(wb), TODAY)


def upload(client, csrf, buf, name="Hauswirtschaftsmonatsplan.xlsx"):
    return client.post("/verwaltung/essensplan", data={"csrf": csrf, "plan": (buf, name)},
                       content_type="multipart/form-data")


def confirm_from_preview(client, csrf, page):
    data = html.unescape(re.search(r'name="data" value="([^"]*)"', page).group(1))
    return client.post("/verwaltung/essensplan/uebernehmen",
                       data={"csrf": csrf, "data": data, "filename": "plan.xlsx"})


def nutrition(client):
    return client.get("/api/dashboard/verpflegung").get_json()


def test_upload_preview_confirm_and_dashboard(app, client):
    app.state["today"] = date(2026, 10, 2)
    csrf = admin_login(client)
    page = upload(client, csrf, new_file()).get_data(as_text=True)
    assert "September 2026 und Oktober 2026 übernehmen" in page
    assert "bereits importiert" not in page
    assert nutrition(client)["plan_months"] == []  # Vorschau speichert nichts

    assert confirm_from_preview(client, csrf, page).status_code == 302
    d = nutrition(client)
    assert d["plan_months"] == [9, 10]
    assert d["today"] == {"breakfast": 64, "lunch": 0, "packs": 64, "coffee": 6, "cake": 6,
                          "dinner": 45, "snack": 0, "estimated": False}
    # Jan–Aug pauschal, September und Oktober nach Plan
    per_day = 20000 / 334
    pauschal = per_day * 243
    assert d["meals_to_date"]["breakfast"] == pytest.approx(pauschal + 43 + 42 + 64)
    assert d["meals_to_date"]["coffee"] == 6
    main = (pauschal * 1.0 + (43 + 42 + 64) * .25 + (70 + 42 + 64) * .40 + (42 + 58 + 45) * .35)
    extra = 6 * .03 + 6 * .08
    assert d["veg_kg"] == pytest.approx((main + extra) * 3.81)
    # Kaffee und Kuchen bringen keine Ersparnis
    assert d["saved_kg"] == pytest.approx(main * (5.63 - 3.81))

    # Tage im Planmonat ohne Essen: keine Gäste (nicht pauschal)
    app.state["today"] = date(2026, 10, 3)
    assert nutrition(client)["today"] is None

    admin = client.get("/verwaltung").get_data(as_text=True)
    assert "Oktober 2026" in admin and "/verwaltung/essensplan/2026-10" in admin

    # Löschen → wieder pauschal
    assert client.post("/verwaltung/essensplan/2026-10/loeschen", data={"csrf": csrf}).status_code == 302
    assert nutrition(client)["plan_months"] == [9]


def test_partial_upload_with_bad_sheet(client):
    csrf = admin_login(client)
    buf = new_file(extra=[("Plan Nov", 2026, 11, {}, {"weekday_shift": 3})])
    page = upload(client, csrf, buf).get_data(as_text=True)
    assert "Blatt „Plan Nov“ wird nicht übernommen" in page
    assert "September 2026 und Oktober 2026 übernehmen" in page
    page = upload(client, csrf, make_new([("Plan Nov", 2026, 11, {}, {"weekday_shift": 3})])).get_data(as_text=True)
    assert "übernehmen</button>" not in page


def test_manual_correction(app, client):
    app.state["today"] = date(2026, 10, 2)
    csrf = admin_login(client)
    confirm_from_preview(client, csrf, upload(client, csrf, new_file()).get_data(as_text=True))

    page = client.get("/verwaltung/essensplan/2026-10").get_data(as_text=True)
    assert 'name="2026-10-02_coffee" value="6"' in page
    form = dict(re.findall(r'name="(2026-10-\d\d_\w+)" value="([^"]*)"', page))
    assert len(form) == 31 * len(essensplan.FIELDS)

    # Ungültiger Wert: nichts gespeichert
    bad = dict(form, csrf=csrf, **{"2026-10-02_coffee": "zehn", "2026-10-02_dinner": "50"})
    page = client.post("/verwaltung/essensplan/2026-10", data=bad).get_data(as_text=True)
    assert "Nicht gespeichert" in page and "Kaffee am 2." in page
    assert nutrition(client)["today"]["dinner"] == 45

    good = dict(form, csrf=csrf, **{"2026-10-02_coffee": "10", "2026-10-02_dinner": "50",
                                    "2026-10-03_snack": ""})
    r = client.post("/verwaltung/essensplan/2026-10", data=good)
    assert r.status_code == 302
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "2 Werte geändert" in page and "Zuletzt manuell korrigiert" in page
    t = nutrition(client)["today"]
    assert (t["coffee"], t["dinner"], t["cake"]) == (10, 50, 6)

    # Erneuter Upload warnt vor dem Verlust der Korrekturen und setzt sie zurück
    page = upload(client, csrf, new_file()).get_data(as_text=True)
    assert "bereits importiert" in page and "gehen dabei verloren" in page
    confirm_from_preview(client, csrf, page)
    assert nutrition(client)["today"]["coffee"] == 6
    assert "Zuletzt manuell korrigiert" not in client.get("/verwaltung/essensplan/2026-10").get_data(as_text=True)


def test_meal_plan_routes_require_admin_and_valid_data(app, client):
    other = app.test_client()
    r = other.post("/verwaltung/essensplan", data={"plan": (new_file(), "p.xlsx")},
                   content_type="multipart/form-data")
    assert r.status_code == 302 and "/verwaltung/login" in r.headers["Location"]
    assert other.get("/verwaltung/essensplan/2026-10").status_code == 302
    csrf = admin_login(client)
    assert client.get("/verwaltung/essensplan/2026-10").status_code == 404  # nicht importiert
    assert client.get("/verwaltung/essensplan/kaputt").status_code == 404
    # Korrektur ohne CSRF-Token
    confirm_from_preview(client, csrf, upload(client, csrf, new_file()).get_data(as_text=True))
    assert client.post("/verwaltung/essensplan/2026-10", data={}).status_code == 400
    bad = [{"year": 2026, "month": 11, "days": [{"day": "2026-11-01", "breakfast": -1}]}]
    for data in (json.dumps(bad), "{", "[]"):
        r = client.post("/verwaltung/essensplan/uebernehmen", data={"csrf": csrf, "data": data})
        assert r.status_code == 400


def test_migration_from_first_meal_schema(tmp_path):
    import sqlite3
    from anreise import create_app
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE meals (day TEXT PRIMARY KEY, breakfast INTEGER NOT NULL DEFAULT 0,
            lunch INTEGER NOT NULL DEFAULT 0, dinner INTEGER NOT NULL DEFAULT 0,
            departures INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE meal_imports (month TEXT PRIMARY KEY, filename TEXT NOT NULL DEFAULT '',
            imported_at TEXT NOT NULL);
        INSERT INTO meals VALUES ('2026-11-06', 43, 13, 43, 0);
        INSERT INTO meal_imports VALUES ('2026-11', 'alt.xlsx', '2026-10-08T10:00');
    """)
    conn.commit()
    conn.close()
    app = create_app({"DATABASE": str(path), "SECRET_KEY": "t", "TESTING": True})
    from anreise import db
    from anreise.app import get_db
    with app.app_context():
        day = db.meal_days(get_db(), "2026-11")[0]
        assert day["packs"] == 30 and day["coffee"] == 0
        assert db.meal_import(get_db(), "2026-11")["corrected_at"] == ""


def test_meal_weights_setting(client):
    csrf = admin_login(client)
    client.post("/verwaltung/einstellungen", data={
        "csrf": csrf, "meals_days_per_year": "20000", "meals_organic_share": "50",
        "meals_veg_kg_per_day": "3.81", "meals_mixed_kg_per_day": "5.63", "closed_12": "on",
        "screen_rotation_seconds": "30", "meals_weight_breakfast": "20", "meals_weight_lunch": "40",
        "meals_weight_packs": "30", "meals_weight_dinner": "40", "meals_weight_snack": "20",
        "meals_weight_coffee": "2", "meals_weight_cake": "10"})
    d = nutrition(client)
    assert d["weights"] == {"breakfast": 20, "lunch": 40, "packs": 30, "dinner": 40, "snack": 20,
                            "coffee": 2, "cake": 10}
