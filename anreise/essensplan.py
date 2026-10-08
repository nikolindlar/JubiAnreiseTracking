"""Import des Hauswirtschafts-Monatsplans (Excel).

Jedes sichtbare Blatt mit einer Tageszeile 1, 2, 3, … ist ein Monat:
- Monat: Feld „Monat:“ mit dem Namen rechts daneben, sonst aus dem Blattnamen
  (z. B. „Hauswirtschaftsplan Okt“)
- Jahr: Feld „Jahr:“, sonst eine Jahreszahl im Blattnamen, sonst aus den
  Wochentagen unter der Tageszeile (Vorjahr, laufendes Jahr oder Folgejahr)
- Zeilen mit der Beschriftung in Spalte A: Anreise, Abreise, Frühstück,
  Mittagessen, Lunch(paket), Kaffee, Kuchen, Abendessen, Brotzeit.
  Leere Zellen zählen als 0.

Fehlt die Zeile „Lunch“ (ältere Vorlage), gilt Lunchpakete = Frühstück −
Mittagessen. Die Zeilen werden über die Beschriftung gefunden, nicht über
feste Zellen.
"""
import calendar
import re
from datetime import date

MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
               "September", "Oktober", "November", "Dezember"]
MONTHS = {name.lower(): i for i, name in enumerate(MONTH_NAMES, 1)}
MONTHS.update({"jänner": 1, "maerz": 3, "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4,
               "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9, "okt": 10, "nov": 11,
               "dez": 12})
WEEKDAYS = ["mo", "di", "mi", "do", "fr", "sa", "so"]

# Reihenfolge = Anzeige
FIELDS = ("arrivals", "departures", "breakfast", "lunch", "packs", "coffee", "cake",
          "dinner", "snack")
MEALS = ("breakfast", "lunch", "packs", "coffee", "cake", "dinner", "snack")
FIELD_NAMES = {"arrivals": "Anreise", "departures": "Abreise", "breakfast": "Frühstück",
               "lunch": "Mittagessen", "packs": "Lunchpakete", "coffee": "Kaffee",
               "cake": "Kuchen", "dinner": "Abendessen", "snack": "Brotzeit"}
# Beschriftung in Spalte A (Anfang, klein geschrieben) → Feld
LABELS = (("anreise", "arrivals"), ("abreise", "departures"), ("frühstück", "breakfast"),
          ("fruehstueck", "breakfast"), ("mittag", "lunch"), ("lunch", "packs"),
          ("kaffee", "coffee"), ("kuchen", "cake"), ("abend", "dinner"),
          ("brotzeit", "snack"))
REQUIRED = ("breakfast", "lunch", "dinner")
MAX_VALUE = 100000

MAX_ROWS = 200
MAX_COLS = 60


class PlanError(Exception):
    pass


def lunch_packs(breakfast, lunch):
    """Nur für die ältere Vorlage ohne Zeile „Lunch“: Lunchpakete werden beim
    Frühstück gepackt, wer mittags im Haus isst, bekommt keins."""
    return max(0, breakfast - lunch)


def month_name(month):
    return MONTH_NAMES[month - 1]


def month_label(key):
    """„2026-10“ → „Oktober 2026“"""
    return f"{month_name(int(key[5:7]))} {key[:4]}"


def col_name(col):
    name = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        name = chr(65 + rem) + name
    return name


def _text(value):
    return str(value).strip() if value is not None else ""


def as_int(value):
    """Ganze Zahl ≥ 0 (leer = 0) oder None, wenn der Wert ungültig ist."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value < 0 or value != int(value) or value > MAX_VALUE:
            return None
        return int(value)
    try:
        return as_int(float(str(value).strip().replace(",", ".")))
    except ValueError:
        return None


def _right_of(row, c):
    for value in row[c + 1:]:
        if _text(value):
            return value
    return None


def read_sheets(fileobj):
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise PlanError("Das Python-Paket openpyxl fehlt (pip install -r requirements.txt).") from exc
    try:
        wb = openpyxl.load_workbook(fileobj, read_only=True, data_only=True)
    except Exception as exc:  # BadZipFile, InvalidFileException, KeyError …
        raise PlanError("Die Datei ist keine lesbare Excel-Datei (.xlsx). "
                        "Ältere .xls-Dateien bitte in Excel als .xlsx speichern.") from exc
    try:
        sheets = []
        for ws in wb.worksheets:
            if getattr(ws, "sheet_state", "visible") != "visible":
                continue
            rows = []
            for row in ws.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True):
                rows.append(list(row) + [None] * (MAX_COLS - len(row)))
            sheets.append((ws.title, rows))
        return sheets
    finally:
        wb.close()


def parse(fileobj, today):
    """Liest alle Blätter. Gibt eine Liste je Blatt zurück:
    {"sheet", "plan"} oder {"sheet", "error"}. Blätter ohne Tageszeile werden
    übergangen. PlanError, wenn die Datei nicht lesbar ist oder kein Blatt
    einen Monatsplan enthält."""
    results = []
    for title, rows in read_sheets(fileobj):
        try:
            plan = parse_sheet(rows, title, today)
        except PlanError as exc:
            results.append({"sheet": title, "error": str(exc)})
            continue
        if plan:
            results.append({"sheet": title, "plan": plan})
    if not results:
        raise PlanError("Kein Blatt mit einer Tageszeile 1, 2, 3, … gefunden.")
    keys = [r["plan"]["key"] for r in results if "plan" in r]
    for r in results:
        if "plan" in r and keys.count(r["plan"]["key"]) > 1:
            r.pop("plan")
            r["error"] = "Der Monat kommt in mehreren Blättern vor."
    return results


def _month_from_title(title):
    for token in re.findall(r"[a-zäöü]+", title.lower()):
        if token in MONTHS:
            return MONTHS[token]
    return None


def _find_day_row(rows):
    for r, row in enumerate(rows):
        for c in range(MAX_COLS - 2):
            v = row[c]
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v == 1 \
                    and as_int(row[c + 1]) == 2 and as_int(row[c + 2]) == 3:
                return r
    return None


def parse_sheet(rows, title, today):
    """Ein Blatt → Plan-dict oder None (kein Monatsplan). PlanError bei Fehlern."""
    day_row = _find_day_row(rows)
    if day_row is None:
        return None
    errors = []
    month = year = None
    label_rows = {}
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            t = _text(value).lower().rstrip(":").strip()
            if t == "monat" and month is None:
                v = _right_of(row, c)
                month = MONTHS.get(_text(v).lower()) or (
                    as_int(v) if isinstance(v, (int, float)) and 1 <= (as_int(v) or 0) <= 12 else None)
                if month is None:
                    raise PlanError(f"Monat „{_text(v)}“ nicht erkannt.")
            elif t == "jahr" and year is None:
                v = as_int(_right_of(row, c))
                if not v or not 2000 <= v <= 2100:
                    raise PlanError("Jahr nicht erkannt.")
                year = v
        label = _text(row[0]).lower()
        for prefix, field in LABELS:
            if label.startswith(prefix) and field not in label_rows:
                label_rows[field] = r
                break

    if month is None:
        month = _month_from_title(title)
    if month is None:
        raise PlanError(f"Monat nicht erkannt: weder Feld „Monat:“ noch Monatsname im Blattnamen „{title}“.")
    if year is None:
        m = re.search(r"\b(20\d\d)\b", title)
        year = int(m.group(1)) if m else None
    missing = [FIELD_NAMES[f] for f in REQUIRED if f not in label_rows]
    if missing:
        raise PlanError("Zeile " + ", ".join(f"„{m}“" for m in missing) +
                        " nicht gefunden (Beschriftung in Spalte A).")

    day_cols = {}
    for c, value in enumerate(rows[day_row]):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            d = as_int(value)
            if d and 1 <= d <= 31:
                if d in day_cols:
                    raise PlanError(f"Tag {d} kommt in Zeile {day_row + 1} doppelt vor.")
                day_cols[d] = c
    weekday_row = rows[day_row + 1] if day_row + 1 < len(rows) else [None] * MAX_COLS
    weekdays = {d: _text(weekday_row[c]).lower()[:2] for d, c in day_cols.items()}
    weekdays = {d: w for d, w in weekdays.items() if w in WEEKDAYS}

    def matches(y):
        n = calendar.monthrange(y, month)[1]
        return all(d > n or WEEKDAYS[date(y, month, d).weekday()] == w for d, w in weekdays.items())

    notes = []
    if year is None:
        if not weekdays:
            raise PlanError("Jahr nicht erkannt: kein Feld „Jahr:“, keine Jahreszahl im Blattnamen "
                            "und keine Wochentage unter den Tagen.")
        # Innerhalb von drei aufeinanderfolgenden Jahren ist das Muster eindeutig
        candidates = [y for y in range(today.year - 1, today.year + 2) if matches(y)]
        if len(candidates) != 1:
            raise PlanError(f"Jahr nicht erkannt: Die Wochentage passen zu keinem {month_name(month)} "
                            f"zwischen {today.year - 1} und {today.year + 1}.")
        year = candidates[0]
        notes.append(f"Jahr {year} aus den Wochentagen bestimmt.")
    elif not matches(year):
        d = next(d for d, w in weekdays.items()
                 if d <= calendar.monthrange(year, month)[1]
                 and WEEKDAYS[date(year, month, d).weekday()] != w)
        raise PlanError(f"Wochentag passt nicht zum Kalender: der {d}. {month_name(month)} {year} ist ein "
                        f"{WEEKDAYS[date(year, month, d).weekday()].capitalize()}, in der Datei steht "
                        f"{weekdays[d].capitalize()}. Stimmen Monat und Jahr?")

    n_days = calendar.monthrange(year, month)[1]
    missing_days = [d for d in range(1, n_days + 1) if d not in day_cols]
    if missing_days:
        errors.append(f"In Zeile {day_row + 1} fehlen die Tage {', '.join(map(str, missing_days))}.")
    extra = sorted(d for d in day_cols if d > n_days)
    if extra:
        errors.append(f"Tag {extra[0]} gibt es im {month_name(month)} {year} nicht.")
    if errors:
        raise PlanError(" ".join(errors))

    days = []
    for d in range(1, n_days + 1):
        c = day_cols[d]
        entry = {"day": date(year, month, d).isoformat()}
        for field in FIELDS:
            r = label_rows.get(field)
            value = as_int(rows[r][c]) if r is not None else 0
            if value is None:
                errors.append(f"{FIELD_NAMES[field]} am {d}. (Zelle {col_name(c)}{r + 1}): "
                              f"„{_text(rows[r][c])}“ ist keine ganze Zahl ≥ 0.")
                value = 0
            entry[field] = value
        if "packs" not in label_rows:
            entry["packs"] = lunch_packs(entry["breakfast"], entry["lunch"])
        days.append(entry)
    if errors:
        raise PlanError(" ".join(errors))

    if "packs" not in label_rows:
        notes.append("Keine Zeile „Lunch“: Lunchpakete berechnet als Frühstück − Mittagessen.")
    warnings = check(days)
    if year != today.year:
        warnings.insert(0, f"Der Plan gilt für {year}, nicht für das laufende Jahr {today.year}.")
    return {"year": year, "month": month, "key": f"{year:04d}-{month:02d}",
            "label": f"{month_name(month)} {year}", "days": days,
            "notes": notes, "warnings": warnings}


def check(days):
    """Plausibilitätsprüfung, nur Warnungen."""
    warnings = []
    for d in days:
        n = int(d["day"][8:])
        if d["departures"] > d["breakfast"]:
            warnings.append(f"{n}.: mehr Abreisen ({d['departures']}) als Frühstück ({d['breakfast']}).")
        if d["cake"] > d["coffee"]:
            warnings.append(f"{n}.: mehr Kuchen ({d['cake']}) als Kaffee ({d['coffee']}).")
    return warnings


def validate(data):
    """Prüft die aus der Vorschau zurückgeschickten Daten (Liste von Monaten)."""
    if not isinstance(data, list) or not data:
        return None
    out = []
    for plan in data:
        try:
            year, month = int(plan["year"]), int(plan["month"])
            days = plan["days"]
            n_days = calendar.monthrange(year, month)[1]
            if len(days) != n_days:
                return None
            clean = []
            for i, d in enumerate(days):
                entry = {"day": date(year, month, i + 1).isoformat()}
                if d.get("day") != entry["day"]:
                    return None
                for field in FIELDS:
                    v = d[field]
                    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= MAX_VALUE:
                        return None
                    entry[field] = v
                clean.append(entry)
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
        out.append({"year": year, "month": month, "key": f"{year:04d}-{month:02d}", "days": clean})
    return out


def summary(days):
    s = {f: sum(d[f] for d in days) for f in FIELDS}
    s["meals"] = sum(s[f] for f in MEALS)
    return s
