"""Import des monatlichen Dienstplans Hauswirtschaft (Excel).

Erwartete Vorlage (ein Blatt):
- irgendwo „Monat:“ mit dem Monatsnamen (oder der Zahl) rechts daneben,
  „Jahr:“ mit dem Jahr rechts daneben
- eine Zeile mit den Tagen 1, 2, 3, … (je Spalte ein Tag), darunter optional
  die Wochentage (Mo, Di, …) – sie werden gegen den Kalender geprüft
- Zeilen mit der Beschriftung in Spalte A: Abreise, Frühstück, Mittagessen,
  Abendessen. Leere Zellen zählen als 0.

Die Zeilen werden über die Beschriftung gefunden, nicht über feste Zellen.
"""
import calendar
from datetime import date

MONTHS = {
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "april": 4,
    "mai": 5, "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}
WEEKDAYS = ["mo", "di", "mi", "do", "fr", "sa", "so"]
FIELDS = ("breakfast", "lunch", "dinner", "departures")
LABELS = {"Frühstück": "breakfast", "Mittag": "lunch", "Abend": "dinner", "Abreise": "departures"}
# Zeilen, die in der Datei vorhanden sein müssen
REQUIRED = ("breakfast", "lunch", "dinner")
FIELD_NAMES = {"breakfast": "Frühstück", "lunch": "Mittagessen", "dinner": "Abendessen",
               "departures": "Abreise"}

MAX_ROWS = 200
MAX_COLS = 60


class PlanError(Exception):
    pass


def lunch_packs(breakfast, lunch):
    """Lunchpakete werden beim Frühstück gepackt. Wer mittags im Haus isst,
    bekommt keins."""
    return max(0, breakfast - lunch)


def col_name(col):
    name = ""
    col += 1
    while col:
        col, rem = divmod(col - 1, 26)
        name = chr(65 + rem) + name
    return name


def _text(value):
    return str(value).strip() if value is not None else ""


def _as_int(value):
    """Ganze Zahl ≥ 0 oder None, wenn die Zelle keinen gültigen Wert enthält."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value < 0 or value != int(value):
            return None
        return int(value)
    try:
        return _as_int(float(str(value).strip().replace(",", ".")))
    except ValueError:
        return None


def _right_of(rows, r, c):
    for value in rows[r][c + 1:]:
        if _text(value):
            return value
    return None


def read_rows(fileobj):
    try:
        import openpyxl
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError as exc:  # pragma: no cover
        raise PlanError("Das Python-Paket openpyxl fehlt (pip install -r requirements.txt).") from exc
    try:
        wb = openpyxl.load_workbook(fileobj, read_only=True, data_only=True)
    except (InvalidFileException, KeyError, OSError, ValueError, TypeError) as exc:
        raise PlanError("Die Datei ist keine lesbare Excel-Datei (.xlsx). "
                        "Ältere .xls-Dateien bitte in Excel als .xlsx speichern.") from exc
    except Exception as exc:  # zipfile.BadZipFile u. a.
        raise PlanError("Die Datei ist keine lesbare Excel-Datei (.xlsx).") from exc
    try:
        ws = wb.worksheets[0]
        rows = []
        for row in ws.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True):
            rows.append(list(row) + [None] * (MAX_COLS - len(row)))
        return rows
    finally:
        wb.close()


def parse(fileobj):
    """Liest den Plan. Gibt ein dict mit year, month, days (Liste je Tag) und
    warnings zurück oder wirft PlanError mit einer verständlichen Meldung."""
    return parse_rows(read_rows(fileobj))


def parse_rows(rows):
    errors = []
    month = year = None
    day_row = None
    label_rows = {}

    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            t = _text(value).lower().rstrip(":").strip()
            if t == "monat" and month is None:
                v = _right_of(rows, r, c)
                month = MONTHS.get(_text(v).lower())
                if month is None and _as_int(v) and 1 <= _as_int(v) <= 12:
                    month = _as_int(v)
                if month is None:
                    errors.append(f"Monat „{_text(v)}“ nicht erkannt.")
                    month = False
            elif t == "jahr" and year is None:
                v = _as_int(_right_of(rows, r, c))
                year = v if v and 2000 <= v <= 2100 else False
                if not year:
                    errors.append("Jahr nicht erkannt.")
        label = _text(row[0])
        for prefix, field in LABELS.items():
            if label.lower().startswith(prefix.lower()) and field not in label_rows:
                label_rows[field] = r
        if day_row is None:
            cols = [c for c, v in enumerate(row) if _as_int(v) == 1 and not isinstance(v, str)]
            for c in cols:
                if c + 2 < MAX_COLS and _as_int(row[c + 1]) == 2 and _as_int(row[c + 2]) == 3:
                    day_row = r
                    break

    if month is None:
        errors.append("Feld „Monat:“ nicht gefunden.")
    if year is None:
        errors.append("Feld „Jahr:“ nicht gefunden.")
    if day_row is None:
        errors.append("Zeile mit den Tagen 1, 2, 3, … nicht gefunden.")
    for field in REQUIRED:
        if field not in label_rows:
            errors.append(f"Zeile „{FIELD_NAMES[field]}“ nicht gefunden (Beschriftung in Spalte A).")
    if errors:
        raise PlanError(" ".join(errors))

    n_days = calendar.monthrange(year, month)[1]
    day_cols = {}
    for c, value in enumerate(rows[day_row]):
        d = _as_int(value) if value is not None and not isinstance(value, str) else None
        if d and 1 <= d <= 31:
            if d in day_cols:
                errors.append(f"Tag {d} kommt in Zeile {day_row + 1} doppelt vor.")
            day_cols[d] = c
    missing = [d for d in range(1, n_days + 1) if d not in day_cols]
    extra = [d for d in day_cols if d > n_days]
    if missing:
        errors.append(f"In Zeile {day_row + 1} fehlen die Tage {', '.join(map(str, missing))}.")
    if extra:
        errors.append(f"Tag {extra[0]} gibt es im {month_name(month)} {year} nicht.")

    # Wochentage prüfen (Zeile unter den Tagen), falls vorhanden
    if day_row + 1 < len(rows):
        wd_row = rows[day_row + 1]
        for d, c in day_cols.items():
            t = _text(wd_row[c]).lower()[:2]
            if d <= n_days and t in WEEKDAYS:
                expected = WEEKDAYS[date(year, month, d).weekday()]
                if t != expected:
                    errors.append(
                        f"Wochentag passt nicht zum Kalender: der {d}. {month_name(month)} {year} ist ein "
                        f"{expected.capitalize()}, in der Datei steht {_text(wd_row[c])}. "
                        "Stimmen Monat und Jahr?")
                    break
    if errors:
        raise PlanError(" ".join(errors))

    days = []
    for d in range(1, n_days + 1):
        c = day_cols[d]
        entry = {"day": date(year, month, d).isoformat()}
        for field in FIELDS:
            r = label_rows.get(field)
            value = _as_int(rows[r][c]) if r is not None else 0
            if value is None:
                errors.append(f"{FIELD_NAMES[field]} am {d}. (Zelle {col_name(c)}{r + 1}): "
                              f"„{_text(rows[r][c])}“ ist keine ganze Zahl ≥ 0.")
                value = 0
            entry[field] = value
        days.append(entry)
    if errors:
        raise PlanError(" ".join(errors))

    return {"year": year, "month": month, "days": days, "warnings": check(days)}


def check(days):
    """Plausibilitätsprüfung, nur Warnungen."""
    warnings = []
    for i, d in enumerate(days):
        n = int(d["day"][8:])
        if d["departures"] > d["breakfast"]:
            warnings.append(f"{n}.: mehr Abreisen ({d['departures']}) als Frühstück ({d['breakfast']}).")
        if i + 1 < len(days) and d["dinner"] != days[i + 1]["breakfast"]:
            warnings.append(f"{n}.: {d['dinner']} Abendessen, aber am Folgetag "
                            f"{days[i + 1]['breakfast']} Frühstück.")
    return warnings


def validate(data):
    """Prüft die aus der Vorschau zurückgeschickten Daten."""
    try:
        year, month = int(data["year"]), int(data["month"])
        days = data["days"]
        n_days = calendar.monthrange(year, month)[1]
        if len(days) != n_days:
            return None
        out = []
        for i, d in enumerate(days):
            entry = {"day": date(year, month, i + 1).isoformat()}
            if d.get("day") != entry["day"]:
                return None
            for field in FIELDS:
                v = d[field]
                if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100000:
                    return None
                entry[field] = v
            out.append(entry)
        return {"year": year, "month": month, "days": out}
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


MONTH_NAMES = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
               "September", "Oktober", "November", "Dezember"]


def month_name(month):
    return MONTH_NAMES[month - 1]


def summary(days):
    s = {f: sum(d[f] for d in days) for f in FIELDS}
    s["packs"] = sum(lunch_packs(d["breakfast"], d["lunch"]) for d in days)
    s["meals"] = s["breakfast"] + s["packs"] + s["lunch"] + s["dinner"]
    return s
