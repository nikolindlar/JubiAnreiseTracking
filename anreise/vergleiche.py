"""Anschauliche Vergleiche für die Übersicht.

Entfernungen sind Luftlinie (Großkreis), gerundet. Die Jubi-Koordinate ist der
Ortskern von Bad Hindelang (ca. 47,505 N / 10,375 E) und auf wenige Kilometer genau.
"""

# Gesamtstrecke aller Anreisen
STRECKEN = [
    ("München–Jubi", 114),
    ("München–Berlin", 504),
    ("Jubi–Oslo", 1380),
    ("Jubi–Nordkap", 2753),
    ("Hamburg–New York", 6130),
    ("Jubi–Sydney", 16430),
    ("um die Erde", 40075),
    ("Erde–Mond", 384400),
]

# Mit Muskelkraft (zu Fuß, Rad) zurückgelegte Strecke
MUSKELKRAFT = [
    ("Jubi–Zugspitze", 47),
    ("München–Zugspitze", 91),
    ("München–Jubi", 114),
    ("Jubi–Venedig", 273),
    ("Jubi–Mont Blanc", 326),
    ("Jubi–Rom", 645),
    ("Jubi–Oslo", 1380),
    ("Jubi–Lissabon", 1853),
    ("Jubi–Nordkap", 2753),
    ("Jubi–Kapstadt", 9090),
    ("um die Erde", 40075),
]

# Eingespartes CO2 als Flüge ab München, einfache Strecke, in g CO2e pro Person.
# Näherung: Entfernung ab Flughafen München × 290 g/Pkm (UBA Inlandsflug 2024,
# inkl. Nicht-CO2-Effekte) bzw. × 210 g/Pkm für Auslandsflüge (abgeleitet aus dem
# UBA-Beispiel Frankfurt–New York, ca. 2,7 t hin und zurück).
FLUEGE = [
    ("München–Berlin", 462 * 290),
    ("München–Mallorca", 1216 * 210),
    ("München–Teneriffa", 3318 * 210),
    ("München–Dubai", 4564 * 210),
    ("München–New York", 6481 * 210),
    ("München–Bangkok", 8799 * 210),
    ("München–Sydney", 16312 * 210),
]


def vergleich(wert, liste):
    """Größter Vergleich, der mindestens einmal in den Wert passt.
    Ist der Wert kleiner als der kleinste Vergleich, wird dieser mit
    einem Faktor < 1 zurückgegeben. Bei 0 oder weniger: None."""
    if wert <= 0:
        return None
    passend = [(name, groesse) for name, groesse in liste if groesse <= wert]
    name, groesse = passend[-1] if passend else liste[0]
    return {"label": name, "factor": wert / groesse}
