import hmac
import io
import csv
import os
import secrets
from datetime import date
from functools import wraps

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template,
                   request, session, url_for)

from . import db


def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("ANREISE_DB", "anreise.sqlite3"),
        SECRET_KEY=os.environ.get("ANREISE_SECRET_KEY") or secrets.token_hex(32),
        ADMIN_PASSWORD=os.environ.get("ANREISE_ADMIN_PASSWORD", ""),
        TODAY=date.today,  # überschreibbar für Tests
    )
    if config:
        app.config.update(config)

    with app.app_context():
        db.init_db(get_db())

    app.teardown_appcontext(close_db)
    register_routes(app)
    return app


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
        if not session.get("admin"):
            return redirect(url_for("login"))
        if request.method == "POST" and not hmac.compare_digest(
                request.form.get("csrf", ""), session.get("csrf", "")):
            abort(400)
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
    @app.get("/erfassung")
    def kiosk():
        conn = get_db()
        employees = conn.execute(
            "SELECT id, name FROM employees WHERE active = 1 AND distance_km > 0"
            " ORDER BY name COLLATE NOCASE").fetchall()
        modes = conn.execute(
            "SELECT id, label, icon FROM modes WHERE active = 1 ORDER BY sort").fetchall()
        return render_template("kiosk.html", employees=employees, modes=modes)

    @app.post("/api/anreise")
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
            if pw and hmac.compare_digest(request.form.get("password", ""), pw):
                session.clear()
                session["admin"] = True
                session["csrf"] = secrets.token_hex(16)
                return redirect(url_for("admin"))
            error = ("Kein Verwaltungspasswort konfiguriert (ANREISE_ADMIN_PASSWORD)."
                     if not pw else "Falsches Passwort.")
        return render_template("login.html", error=error)

    @app.post("/verwaltung/logout")
    def logout():
        session.clear()
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
