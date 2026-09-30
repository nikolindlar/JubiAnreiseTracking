import hmac
import io
import csv
import os
import secrets
import time
from datetime import date, timedelta
from functools import wraps

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template,
                   request, session, url_for)

from . import db


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
        SESSION_COOKIE_SAMESITE="Lax",
        TODAY=date.today,  # überschreibbar für Tests
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        app.config["SECRET_KEY"] = load_or_create_secret(app.config["DATABASE"])

    with app.app_context():
        db.init_db(get_db())

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


def parse_float(value, default=0.0):
    try:
        return max(0.0, float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def register_routes(app):
    def today():
        return app.config["TODAY"]()

    # ---------- Übersicht (Empfang) ----------
    @app.get("/")
    def dashboard():
        return render_template("dashboard.html")

    @app.get("/api/uebersicht")
    def api_overview():
        conn = get_db()
        t = today()
        year_start = t.replace(month=1, day=1).isoformat()
        return jsonify(
            date=t.isoformat(),
            year=t.year,
            today=db.totals(conn, t.isoformat(), t.isoformat()),
            year_total=db.totals(conn, year_start, t.isoformat()),
        )

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
        return render_template("kiosk.html", employees=employees, modes=modes)

    @app.post("/api/anreise")
    @kiosk_required
    def api_arrival():
        data = request.get_json(silent=True) or {}
        try:
            employee_id = int(data["employee_id"])
            mode_id = int(data["mode_id"])
        except (KeyError, TypeError, ValueError):
            return jsonify(ok=False), 400
        if not db.record_arrival(get_db(), today().isoformat(), employee_id, mode_id):
            return jsonify(ok=False), 400
        return jsonify(ok=True)

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
        return redirect(url_for("dashboard"))

    @app.get("/verwaltung")
    @admin_required
    def admin():
        conn = get_db()
        employees = conn.execute(
            "SELECT * FROM employees ORDER BY name COLLATE NOCASE").fetchall()
        modes = conn.execute("SELECT * FROM modes ORDER BY sort").fetchall()
        return render_template("admin.html", employees=employees, modes=modes,
                               csrf=session["csrf"])

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
                "UPDATE modes SET label = ?, icon = ?, factor_g = ?, source = ?, active = ?"
                " WHERE id = ?",
                (label, request.form.get("icon", "").strip(),
                 parse_float(request.form.get("factor_g")),
                 request.form.get("source", "").strip(),
                 1 if request.form.get("active") else 0, mode_id))
            if request.form.get("is_baseline"):
                conn.execute("UPDATE modes SET is_baseline = (id = ?)", (mode_id,))
            conn.commit()
        return redirect(url_for("admin"))

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
