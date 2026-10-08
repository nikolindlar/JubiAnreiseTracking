import hmac
import io
import csv
import os
import secrets
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template,
                   request, session, url_for)

from . import db, pv, vergleiche


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("ANREISE_DB", "anreise.sqlite3"),
        SECRET_KEY=os.environ.get("ANREISE_SECRET_KEY", ""),
        ADMIN_PASSWORD=os.environ.get("ANREISE_ADMIN_PASSWORD", ""),
        KIOSK_PASSWORD=os.environ.get("ANREISE_KIOSK_PASSWORD", ""),
        # Tablets bleiben lange angemeldet, die Verwaltung nur ADMIN_SESSION_HOURS
        PERMANENT_SESSION_LIFETIME=timedelta(days=400),
        ADMIN_SESSION_HOURS=12,
        # Storno-Fenster auf dem Tablet (Countdown) und serverseitige Frist
        # mit etwas Puffer für die Übertragung
        UNDO_SECONDS=20,
        UNDO_GRACE_SECONDS=10,
        SESSION_COOKIE_SAMESITE="Lax",
        TODAY=date.today,  # überschreibbar für Tests
        PV_COLLECTOR=True,      # Hintergrundabfrage des Wechselrichters
        PV_INTERVAL_SECONDS=60,
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = load_or_create_secret(app.config["DATABASE"])

    with app.app_context():
        db.init_db(get_db())
        pv.init_db(get_db())

    app.extensions["pv_collector"] = None
    if app.config["PV_COLLECTOR"] and not app.config.get("TESTING"):
        path = app.config["DATABASE"]
        collector = pv.Collector(lambda: db.connect(path), db.get_settings,
                                 app.config["PV_INTERVAL_SECONDS"])
        collector.start()
        app.extensions["pv_collector"] = collector

    # Storno-Marken nur im Arbeitsspeicher: Marke -> addierte Werte (ohne Name),
    # verfallen nach wenigen Sekunden und gehen bei einem Neustart verloren.
    app.extensions["anreise_undo"] = {"lock": threading.Lock(), "tokens": {}}

    app.teardown_appcontext(close_db)
    register_routes(app)
    return app


def load_or_create_secret(db_path):
    """Dauerhafter Sitzungsschlüssel neben der Datenbank, damit Tablets nach
    einem Neustart des Servers angemeldet bleiben."""
    path = os.path.join(os.path.dirname(os.path.abspath(db_path)), "secret_key")
    try:
        with open(path) as f:
            key = f.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    key = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def check_password(given, expected):
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def is_admin():
    return session.get("admin_until", 0) > time.time()


def is_kiosk():
    return bool(session.get("kiosk")) or is_admin()


def get_db():
    from flask import current_app
    if "db" not in g:
        g.db = db.connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_admin():
            session.pop("admin_until", None)
            return redirect(url_for("login"))
        if request.method == "POST" and not hmac.compare_digest(
                request.form.get("csrf", ""), session.get("csrf", "")):
            abort(400)
        return view(*args, **kwargs)
    return wrapper


def kiosk_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_kiosk():
            if request.path.startswith("/api/"):
                return jsonify(ok=False, login=True), 401
            return redirect(url_for("kiosk_login"))
        return view(*args, **kwargs)
    return wrapper


def pv_status(conn, collector):
    """Kurzer Status der PV-Abfrage für die Verwaltung."""
    import time as _t
    state = conn.execute("SELECT ts FROM pv_state WHERE id = 1").fetchone()
    last = state["ts"] if state else None
    return {
        "last": datetime.fromtimestamp(last).strftime("%d.%m.%Y %H:%M") if last else None,
        "fresh": bool(last and _t.time() - last <= pv.STALE_AFTER),
        "error": collector.last_error if collector else None,
        "running": collector is not None,
    }


def parse_float(value, default=0.0):
    try:
        return max(0.0, float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def register_routes(app):
    def today():
        return app.config["TODAY"]()

    # ---------- Dashboards (Empfang) ----------
    # Reihenfolge = Reihenfolge des Seitenwechsels am Empfangsbildschirm
    dashboard_names = {"dashboard_travel": "Anreise-Dashboard", "dashboard_meals": "Verpflegungs-Dashboard",
                       "dashboard_pv": "PV-Dashboard"}

    def dashboards():
        pages = ["dashboard_travel", "dashboard_meals"]
        if db.get_settings(get_db()).get("pv_source", "").strip():
            pages.append("dashboard_pv")
        return pages

    @app.get("/")
    def index():
        return redirect(url_for("dashboard_travel", **request.args))

    @app.get("/ernaehrung")
    def old_nutrition_url():
        return redirect(url_for("dashboard_meals", **request.args))

    @app.get("/dashboard/anreise")
    def dashboard_travel():
        return render_template("dashboard_anreise.html", **screen_context("dashboard_travel"))

    @app.get("/dashboard/verpflegung")
    def dashboard_meals():
        return render_template("dashboard_verpflegung.html", **screen_context("dashboard_meals"))

    @app.get("/dashboard/pv")
    def dashboard_pv():
        return render_template("dashboard_pv.html", **screen_context("dashboard_pv"))

    @app.get("/api/dashboard/pv")
    def api_pv():
        data = pv.overview(get_db(), today(), db.get_settings(get_db()))
        y = data["year_total"]
        data["compare"] = {
            "flights": vergleiche.vergleich(y["co2_avoided_kg"] * 1000, vergleiche.FLUEGE),
            "ev_km": y["pv_kwh"] / data["ev_kwh_per_100km"] * 100 if data["ev_kwh_per_100km"] else None,
        }
        return jsonify(data)

    @app.get("/api/dashboard/verpflegung")
    def api_nutrition():
        data = db.nutrition(db.get_settings(get_db()), today())
        data["compare"] = vergleiche.vergleich(data["saved_kg"] * 1000, vergleiche.FLUEGE)
        return jsonify(data)

    def screen_context(current):
        """Seitenwechsel für den Empfangsbildschirm: nur aktiv, wenn die Seite
        mit ?empfang aufgerufen wird (z. B. http://<pi>:8080/?empfang)."""
        pages = dashboards()
        if current not in pages:
            pages = pages + [current]
        seconds = 0
        if "empfang" in request.args:
            try:
                seconds = max(0, int(db.get_settings(get_db()).get("screen_rotation_seconds", "0")))
            except ValueError:
                seconds = 0
        nxt = pages[(pages.index(current) + 1) % len(pages)]
        return {"rotate_seconds": seconds, "next_url": url_for(nxt, empfang=1),
                "next_name": dashboard_names[nxt]}

    @app.get("/api/dashboard/anreise")
    def api_overview():
        conn = get_db()
        try:
            min_trips = max(0, int(db.get_settings(conn).get("privacy_min_trips_today", "3")))
        except ValueError:
            min_trips = 3
        data = db.overview(conn, today(), min_trips)
        y = data["year_total"]
        data["compare"] = {
            "distance": vergleiche.vergleich(y["km"], vergleiche.STRECKEN),
            "human": vergleiche.vergleich(data["human_km"], vergleiche.MUSKELKRAFT),
            "flights": vergleiche.vergleich(y["saved_g"], vergleiche.FLUEGE),
        }
        return jsonify(data)

    # ---------- Erfassung (Tablets) ----------
    @app.route("/erfassung/login", methods=["GET", "POST"])
    def kiosk_login():
        error = None
        if request.method == "POST":
            pw = app.config["KIOSK_PASSWORD"]
            if check_password(request.form.get("password", ""), pw):
                session["kiosk"] = True
                session.permanent = True
                return redirect(url_for("kiosk"))
            error = ("Kein Erfassungspasswort konfiguriert (ANREISE_KIOSK_PASSWORD)."
                     if not pw else "Falsches Passwort.")
        return render_template("login.html", error=error, heading="Erfassung",
                               hint="Einmalige Anmeldung für dieses Tablet.")

    @app.get("/erfassung")
    @kiosk_required
    def kiosk():
        conn = get_db()
        employees = conn.execute(
            "SELECT id, name FROM employees WHERE active = 1 AND distance_km > 0"
            " ORDER BY name COLLATE NOCASE").fetchall()
        modes = conn.execute(
            "SELECT id, label, icon FROM modes WHERE active = 1 ORDER BY sort").fetchall()
        notice = db.kiosk_notice(db.get_settings(conn), today())
        return render_template("kiosk.html", employees=employees, modes=modes, notice=notice)

    @app.post("/api/anreise")
    @kiosk_required
    def api_arrival():
        data = request.get_json(silent=True) or {}
        try:
            employee_id = int(data["employee_id"])
            mode_id = int(data["mode_id"])
        except (KeyError, TypeError, ValueError):
            return jsonify(ok=False), 400
        entry = db.record_arrival(get_db(), today().isoformat(), employee_id, mode_id)
        if entry is None:
            return jsonify(ok=False), 400
        undo = app.extensions["anreise_undo"]
        token = secrets.token_urlsafe(16)
        now = time.monotonic()
        with undo["lock"]:
            for t in [t for t, (exp, _) in undo["tokens"].items() if exp < now]:
                del undo["tokens"][t]
            undo["tokens"][token] = (
                now + app.config["UNDO_SECONDS"] + app.config["UNDO_GRACE_SECONDS"], entry)
        return jsonify(ok=True, undo_token=token, undo_seconds=app.config["UNDO_SECONDS"])

    @app.post("/api/anreise/storno")
    @kiosk_required
    def api_arrival_undo():
        token = str((request.get_json(silent=True) or {}).get("undo_token", ""))
        undo = app.extensions["anreise_undo"]
        with undo["lock"]:
            expires, entry = undo["tokens"].pop(token, (0, None))
        if entry is None or expires < time.monotonic():
            return jsonify(ok=False), 410
        return jsonify(ok=db.revert_arrival(get_db(), entry))

    # ---------- Verwaltung ----------
    @app.route("/verwaltung/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            pw = app.config["ADMIN_PASSWORD"]
            if check_password(request.form.get("password", ""), pw):
                session["admin_until"] = time.time() + app.config["ADMIN_SESSION_HOURS"] * 3600
                session["csrf"] = secrets.token_hex(16)
                return redirect(url_for("admin"))
            error = ("Kein Verwaltungspasswort konfiguriert (ANREISE_ADMIN_PASSWORD)."
                     if not pw else "Falsches Passwort.")
        return render_template("login.html", error=error, heading="Verwaltung")

    @app.post("/verwaltung/logout")
    def logout():
        # Nur die Verwaltungsanmeldung beenden, eine Tablet-Anmeldung bleibt bestehen
        session.pop("admin_until", None)
        session.pop("csrf", None)
        return redirect(url_for("dashboard_travel"))

    @app.get("/verwaltung")
    @admin_required
    def admin():
        conn = get_db()
        employees = conn.execute(
            "SELECT * FROM employees ORDER BY name COLLATE NOCASE").fetchall()
        modes = conn.execute("SELECT * FROM modes ORDER BY sort").fetchall()
        settings = db.get_settings(conn)
        collector = app.extensions.get("pv_collector")
        return render_template("admin.html", employees=employees, modes=modes,
                               settings=settings, closed=db.closed_months(settings),
                               notice_active=db.kiosk_notice(settings, today()) is not None,
                               notice_max=db.NOTICE_MAX_CHARS,
                               pv_status=pv_status(conn, collector), csrf=session["csrf"])

    @app.post("/verwaltung/mitarbeitende")
    @admin_required
    def employee_add():
        name = request.form.get("name", "").strip()
        if name:
            conn = get_db()
            conn.execute("INSERT INTO employees (name, distance_km) VALUES (?, ?)",
                         (name, parse_float(request.form.get("distance_km"))))
            conn.commit()
        return redirect(url_for("admin"))

    @app.post("/verwaltung/mitarbeitende/<int:emp_id>")
    @admin_required
    def employee_update(emp_id):
        conn = get_db()
        if request.form.get("action") == "delete":
            conn.execute("DELETE FROM employees WHERE id = ?", (emp_id,))
        else:
            name = request.form.get("name", "").strip()
            if name:
                conn.execute(
                    "UPDATE employees SET name = ?, distance_km = ?, active = ? WHERE id = ?",
                    (name, parse_float(request.form.get("distance_km")),
                     1 if request.form.get("active") else 0, emp_id))
        conn.commit()
        return redirect(url_for("admin"))

    @app.post("/verwaltung/verkehrsmittel/<int:mode_id>")
    @admin_required
    def mode_update(mode_id):
        conn = get_db()
        label = request.form.get("label", "").strip()
        if label:
            conn.execute(
                "UPDATE modes SET label = ?, icon = ?, factor_g = ?, source = ?, active = ?,"
                " is_green = ?, is_human = ? WHERE id = ?",
                (label, request.form.get("icon", "").strip(),
                 parse_float(request.form.get("factor_g")),
                 request.form.get("source", "").strip(),
                 1 if request.form.get("active") else 0,
                 1 if request.form.get("is_green") else 0,
                 1 if request.form.get("is_human") else 0, mode_id))
            if request.form.get("is_baseline"):
                conn.execute("UPDATE modes SET is_baseline = (id = ?)", (mode_id,))
            conn.commit()
        return redirect(url_for("admin"))

    @app.post("/verwaltung/einstellungen")
    @admin_required
    def settings_update():
        conn = get_db()
        f = request.form
        db.set_setting(conn, "meals_days_per_year", f"{parse_float(f.get('meals_days_per_year')):g}")
        db.set_setting(conn, "meals_organic_share",
                       f"{min(100.0, parse_float(f.get('meals_organic_share'))):g}")
        db.set_setting(conn, "meals_veg_kg_per_day", f"{parse_float(f.get('meals_veg_kg_per_day')):g}")
        db.set_setting(conn, "meals_mixed_kg_per_day", f"{parse_float(f.get('meals_mixed_kg_per_day')):g}")
        months = [str(m) for m in range(1, 13) if f.get(f"closed_{m}")]
        if len(months) == 12:  # mindestens ein Monat muss geöffnet sein
            months = months[:-1]
        db.set_setting(conn, "meals_closed_months", ",".join(months))
        db.set_setting(conn, "screen_rotation_seconds", f"{int(parse_float(f.get('screen_rotation_seconds')))}")
        if f.get("privacy_min_trips_today") is not None:
            db.set_setting(conn, "privacy_min_trips_today",
                           f"{int(parse_float(f.get('privacy_min_trips_today'), 3))}")
        conn.commit()
        return redirect(url_for("admin") + "#einstellungen")

    @app.post("/verwaltung/hinweis")
    @admin_required
    def notice_update():
        conn = get_db()
        text = (request.form.get("kiosk_notice") or "").replace("\r\n", "\n").strip()
        until = (request.form.get("kiosk_notice_until") or "").strip()
        try:
            until = date.fromisoformat(until).isoformat() if until else ""
        except ValueError:
            until = ""
        if request.form.get("action") == "delete":
            text, until = "", ""
        db.set_setting(conn, "kiosk_notice", text[:db.NOTICE_MAX_CHARS])
        db.set_setting(conn, "kiosk_notice_until", until)
        conn.commit()
        return redirect(url_for("admin") + "#hinweis")

    @app.post("/verwaltung/pv")
    @admin_required
    def pv_settings_update():
        conn = get_db()
        f = request.form
        db.set_setting(conn, "pv_source", (f.get("pv_source") or "").strip())
        kwp = parse_float(f.get("pv_kwp"))
        db.set_setting(conn, "pv_kwp", f"{kwp:g}" if kwp else "")
        db.set_setting(conn, "pv_co2_g_per_kwh", f"{parse_float(f.get('pv_co2_g_per_kwh'), 344):g}")
        db.set_setting(conn, "pv_ev_kwh_per_100km", f"{parse_float(f.get('pv_ev_kwh_per_100km'), 18) or 18:g}")
        conn.commit()
        return redirect(url_for("admin") + "#pv")

    @app.get("/verwaltung/export.csv")
    @admin_required
    def export_csv():
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["Tag", "Verkehrsmittel", "Anreisen", "km",
                    "CO2 kg", "CO2 kg alle mit Auto"])
        for r in db.daily_rows(get_db()):
            w.writerow([r["day"], r["mode"], r["trips"],
                        f"{r['km']:.1f}".replace(".", ","),
                        f"{r['co2_g'] / 1000:.2f}".replace(".", ","),
                        f"{r['baseline_g'] / 1000:.2f}".replace(".", ",")])
        return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition":
                                 "attachment; filename=anreise-co2.csv"})
