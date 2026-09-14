#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Erzeugt aus statuses.json + stations.json ein in sich geschlossenes HTML-Dashboard.

Auswertungen:
- Kennzahlen-Übersicht (Check-ins, km, Reisezeit, Punkte, Stationen/Linien, Zeitraum)
- Statistiken zu Linien, Baureihen, Fahrzeugen und Stationen
  (Ein-/Ausstieg/Durchfahrt, Rankings, Unique-Kombis, Kreuztabellen)
- Geo-Karte mit Heatmap gerichteter Kanten: pro Fahrt werden die tatsächlich
  befahrenen aufeinanderfolgenden Zwischenhalte als gerichtete Segmente gezählt;
  häufiger befahrene Segmente werden dicker/röter dargestellt.
- Durchsuch- und sortierbare Fahrten-Tabelle mit aufklappbarer Detailansicht
  (alle befahrenen Zwischenhalte mit Zeiten, Gleis, Verspätung).

Nur Standardbibliothek. Die Karte lädt Leaflet + OpenStreetMap-Kacheln (Internet);
der Rest funktioniert offline.

Beispiel:
    python3 build_dashboard.py --open
"""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime

from version import __version__


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def json_for_script(data):
    """Serialisiert `data` so, dass es sicher in einen <script>-Block passt.

    `json.dumps` escapt `<`/`>` nicht, ein `</script>` in einem Freitextfeld (Body,
    Tag-Wert, Stationsname) würde also aus dem Script-Tag ausbrechen. `\\uXXXX` ist
    gültige JSON-String-Syntax, der geparste Wert bleibt unverändert. U+2028/U+2029
    sind dabei, weil `ensure_ascii=False` sie roh durchlässt und sie in
    JS-String-Literalen erst ab ES2019 erlaubt sind.
    """
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for raw, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        blob = blob.replace(raw, escaped)
    return blob


def parse_dt(value):
    """Parst einen ISO-8601-Zeitstempel (auch mit 'Z') oder gibt None zurück."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def trip_datetime(status):
    """Liefert (datetime|None, raw_iso) für den Reisezeitpunkt eines Status.

    Reihenfolge: departureReal → manualDeparture → departurePlanned → createdAt.
    HAFAS liefert gelegentlich geplante Zeiten ein Jahr in der Zukunft; weicht der
    Kandidat um mehr als 2 Tage *nach* createdAt ab, wird er verworfen und der
    nächste versucht (typisch: Fallback auf createdAt).
    """
    checkin = status.get("checkin") or {}
    origin = checkin.get("origin") or {}
    created_raw = status.get("createdAt")
    created_dt = parse_dt(created_raw)
    candidates = [
        origin.get("departureReal"),
        checkin.get("manualDeparture"),
        origin.get("departurePlanned"),
        created_raw,
    ]
    for raw in candidates:
        dt = parse_dt(raw)
        if not dt:
            continue
        if created_dt and (dt.date() - created_dt.date()).days > 2:
            continue
        return dt, raw
    return created_dt, created_raw


def delay_minutes(planned, real):
    """Verspätung in Minuten (real − planned) oder None."""
    p, r = parse_dt(planned), parse_dt(real)
    if not p or not r:
        return None
    return round((r - p).total_seconds() / 60)


DELAY_BUCKETS = ("früh", "0–5", "6–15", "16–30", "31–60", ">60", "unbekannt")
WEEKDAY_KEYS = ("0", "1", "2", "3", "4", "5", "6")  # Mo … So
# Trennt Linienname und Operator im internen Linien-Schlüssel (nicht anzeigen).
LINE_SEP = "\x1f"


def line_key(line_name, operator):
    """Interner Linien-Schlüssel: Name + Operator, damit gleichnamige Linien
    verschiedener Betreiber nicht zusammenfallen."""
    return f"{line_name or ''}{LINE_SEP}{operator or ''}"


def delay_bucket(delay):
    """Verspätungs-Bucket-Label für Kreuztabellen."""
    if delay is None:
        return "unbekannt"
    if delay < 0:
        return "früh"
    if delay <= 5:
        return "0–5"
    if delay <= 15:
        return "6–15"
    if delay <= 30:
        return "16–30"
    if delay <= 60:
        return "31–60"
    return ">60"


class EntityAgg:
    """Akkumulator für Linie / Baureihe / Fahrzeug (einheitliches Metric-Schema)."""

    __slots__ = (
        "count", "distance_km", "duration_min", "points", "segments",
        "delay_sum", "delay_n", "on_time",
        "first", "last", "dates",
        "min_distance_km", "max_distance_km", "min_route", "max_route",
        "vehicles", "loc_classes", "lines", "routes", "weekdays", "months",
        "loc_class_counts",
    )

    def __init__(self):
        self.count = 0
        self.distance_km = 0.0
        self.duration_min = 0
        self.points = 0
        self.segments = 0
        self.delay_sum = 0
        self.delay_n = 0
        self.on_time = 0
        self.first = None
        self.last = None
        self.dates = set()
        self.min_distance_km = None
        self.max_distance_km = None
        self.min_route = ""
        self.max_route = ""
        self.vehicles = set()
        self.loc_classes = set()
        self.lines = set()
        self.routes = set()
        self.weekdays = set()
        self.months = set()
        self.loc_class_counts = Counter()

    def add(self, *, distance_km, duration_min, points, segments, delay,
            date_str, route, weekday=None, month="",
            line="", loc_class="", vehicles=None):
        self.count += 1
        self.distance_km += distance_km
        self.duration_min += duration_min
        self.points += points
        self.segments += segments
        if delay is not None:
            self.delay_sum += delay
            self.delay_n += 1
            if delay <= 5:
                self.on_time += 1
        if date_str:
            self.dates.add(date_str)
            if self.first is None or date_str < self.first:
                self.first = date_str
            if self.last is None or date_str > self.last:
                self.last = date_str
        if self.min_distance_km is None or distance_km < self.min_distance_km:
            self.min_distance_km = distance_km
            self.min_route = route
        if self.max_distance_km is None or distance_km > self.max_distance_km:
            self.max_distance_km = distance_km
            self.max_route = route
        if route:
            self.routes.add(route)
        if weekday is not None:
            self.weekdays.add(str(weekday))
        if month:
            self.months.add(month)
        if line:
            self.lines.add(line)
        if loc_class:
            self.loc_classes.add(loc_class)
            self.loc_class_counts[loc_class] += 1
        for v in vehicles or ():
            if v:
                self.vehicles.add(v)

    def to_row(self, key, kind):
        """Serialisiert den Bucket; `kind` steuert die unique-*-Felder."""
        n = self.count or 1
        row = {
            "key": key,
            "count": self.count,
            "distanceKm": round(self.distance_km, 1),
            "durationMin": self.duration_min,
            "points": self.points,
            "avgDistanceKm": round(self.distance_km / n, 1),
            "avgDurationMin": round(self.duration_min / n, 1),
            "first": self.first,
            "last": self.last,
            "minDistanceKm": self.min_distance_km,
            "maxDistanceKm": self.max_distance_km,
            "minRoute": self.min_route,
            "maxRoute": self.max_route,
            "avgDelay": (
                round(self.delay_sum / self.delay_n, 1) if self.delay_n else None
            ),
            "onTimePct": (
                round(100.0 * self.on_time / self.delay_n, 1) if self.delay_n else None
            ),
            "segments": self.segments,
            "uniqueRoutes": len(self.routes),
            "uniqueWeekdays": len(self.weekdays),
            "uniqueMonths": len(self.months),
        }
        if kind == "line":
            row["uniqueVehicles"] = len(self.vehicles)
            row["uniqueLocClasses"] = len(self.loc_classes)
        elif kind == "locClass":
            row["uniqueVehicles"] = len(self.vehicles)
            row["uniqueLines"] = len(self.lines)
        elif kind == "vehicle":
            row["uniqueLines"] = len(self.lines)
            if self.loc_class_counts:
                row["locClass"] = self.loc_class_counts.most_common(1)[0][0]
            else:
                row["locClass"] = ""
        return row


def _finalize_entities(aggs, kind, limit=None):
    rows = [a.to_row(k, kind) for k, a in aggs.items() if k]
    rows.sort(key=lambda r: (-r["distanceKm"], -r["count"], r["key"]))
    if limit is not None:
        rows = rows[:limit]
    return rows


class EdgeAgg:
    """Akkumulator für eine gerichtete Segmentkante (fromId → toId)."""

    __slots__ = (
        "from_name", "to_name", "count", "first", "last", "dates",
        "vehicles", "lines", "loc_classes",
    )

    def __init__(self, from_name, to_name):
        self.from_name = from_name or ""
        self.to_name = to_name or ""
        self.count = 0
        self.first = None
        self.last = None
        self.dates = set()
        self.vehicles = set()
        self.lines = set()
        self.loc_classes = set()

    def add(self, date_str, *, line="", loc_class="", vehicles=None):
        self.count += 1
        if date_str:
            self.dates.add(date_str)
            if self.first is None or date_str < self.first:
                self.first = date_str
            if self.last is None or date_str > self.last:
                self.last = date_str
        if line:
            self.lines.add(line)
        if loc_class:
            self.loc_classes.add(loc_class)
        for v in vehicles or ():
            if v:
                self.vehicles.add(v)

    def to_row(self):
        return {
            "from": self.from_name,
            "to": self.to_name,
            "count": self.count,
            "first": self.first,
            "last": self.last,
            "uniqueVehicles": len(self.vehicles),
            "uniqueLines": len(self.lines),
            "uniqueLocClasses": len(self.loc_classes),
        }


class StationAgg:
    """Akkumulator für Stationen: Einstieg / Ausstieg / Durchfahrt."""

    __slots__ = (
        "name", "boarded", "alighted", "through", "first", "last", "dates", "lines",
        "first_used", "dates_used", "first_through", "dates_through",
    )

    def __init__(self, name=""):
        self.name = name or ""
        self.boarded = 0
        self.alighted = 0
        self.through = 0
        self.first = None
        self.last = None
        self.dates = set()
        self.lines = set()
        self.first_used = None
        self.dates_used = set()
        self.first_through = None
        self.dates_through = set()

    def _touch(self, date_str, line=""):
        if date_str:
            self.dates.add(date_str)
            if self.first is None or date_str < self.first:
                self.first = date_str
            if self.last is None or date_str > self.last:
                self.last = date_str
        if line:
            self.lines.add(line)

    def _touch_used(self, date_str):
        if not date_str:
            return
        self.dates_used.add(date_str)
        if self.first_used is None or date_str < self.first_used:
            self.first_used = date_str

    def _touch_through(self, date_str):
        if not date_str:
            return
        self.dates_through.add(date_str)
        if self.first_through is None or date_str < self.first_through:
            self.first_through = date_str

    def add_boarded(self, date_str, line=""):
        self.boarded += 1
        self._touch(date_str, line)
        self._touch_used(date_str)

    def add_alighted(self, date_str, line=""):
        self.alighted += 1
        self._touch(date_str, line)
        self._touch_used(date_str)

    def add_through(self, date_str, line=""):
        self.through += 1
        self._touch(date_str, line)
        self._touch_through(date_str)

    def to_row(self):
        return {
            "key": self.name,
            "boarded": self.boarded,
            "alighted": self.alighted,
            "through": self.through,
            "total": self.boarded + self.alighted + self.through,
            "first": self.first,
            "last": self.last,
            "uniqueLines": len(self.lines),
        }


class DatedCombo:
    """Zähler mit first/last für Kombi-Listen (Fahrzeug×Kante×…)."""

    __slots__ = ("count", "first", "last", "dates", "lines")

    def __init__(self):
        self.count = 0
        self.first = None
        self.last = None
        self.dates = set()
        self.lines = set()

    def add(self, date_str, line=""):
        self.count += 1
        if date_str:
            self.dates.add(date_str)
            if self.first is None or date_str < self.first:
                self.first = date_str
            if self.last is None or date_str > self.last:
                self.last = date_str
        if line:
            self.lines.add(line)


def _get_dated(mapping, key):
    a = mapping.get(key)
    if a is None:
        a = DatedCombo()
        mapping[key] = a
    return a


def _bump_cross(store, row, col, km):
    """store[(row,col)] = [count, distanceKm]."""
    if row is None or col is None or row == "" or col == "":
        return
    cell = store.get((row, col))
    if cell is None:
        store[(row, col)] = [1, km]
    else:
        cell[0] += 1
        cell[1] += km


def _pack_cross(store, row_order=None, col_order=None, row_limit=None, col_limit=None):
    """Baut {rows, cols, counts, kms} aus dem Cross-Store."""
    if not store:
        return {"rows": [], "cols": [], "counts": [], "kms": []}
    row_tot = Counter()
    col_tot = Counter()
    for (r, c), (n, _km) in store.items():
        row_tot[r] += n
        col_tot[c] += n
    if row_order is None:
        rows = [r for r, _ in row_tot.most_common()]
    else:
        rows = [r for r in row_order if r in row_tot]
        rows += [r for r, _ in row_tot.most_common() if r not in rows]
    if col_order is None:
        cols = [c for c, _ in col_tot.most_common()]
    else:
        cols = [c for c in col_order if c in col_tot]
        cols += [c for c, _ in col_tot.most_common() if c not in cols]
    if row_limit is not None:
        rows = rows[:row_limit]
    if col_limit is not None:
        cols = cols[:col_limit]
    row_set, col_set = set(rows), set(cols)
    counts = [[0] * len(cols) for _ in rows]
    kms = [[0.0] * len(cols) for _ in rows]
    ri = {r: i for i, r in enumerate(rows)}
    ci = {c: i for i, c in enumerate(cols)}
    for (r, c), (n, km) in store.items():
        if r not in row_set or c not in col_set:
            continue
        counts[ri[r]][ci[c]] = n
        kms[ri[r]][ci[c]] = round(km, 1)
    return {"rows": rows, "cols": cols, "counts": counts, "kms": kms}


def traveled_stopovers(status):
    """Gibt das tatsächlich befahrene Teilstück der Trip-Stopovers zurück.

    Slice von `checkin.origin` bis `checkin.destination` (inklusive). Bevorzugt
    `stopoverId` (eindeutig bei Ringfahrten); Fallback: erste Origin-Station,
    dann erste Ziel-Station mit Index strikt nach dem Start. Fällt auf die
    komplette Stopover-Liste bzw. origin/destination zurück, wenn die Grenzen
    nicht eindeutig bestimmbar sind. Gibt eine Liste von Stopover-Dicts zurück.

    ACHTUNG: identische Kopie in download_statuses.py – Änderungen dort
    mitpflegen (die beiden Skripte importieren sich bewusst nicht gegenseitig).
    """
    checkin = status.get("checkin") or {}
    trip = status.get("trip") or {}
    stopovers = trip.get("stopovers") or []
    origin = checkin.get("origin") or {}
    destination = checkin.get("destination") or {}

    if not stopovers:
        # Kein Trip: nur Start/Ziel aus dem Checkin (haben keine Koordinaten,
        # liefern aber wenigstens die IDs für die Fallback-Kante).
        return [s for s in (origin, destination) if s.get("id") is not None]

    def index_of_stopover_id(stopover_id):
        if stopover_id is None:
            return None
        for i, s in enumerate(stopovers):
            if s.get("stopoverId") == stopover_id:
                return i
        return None

    def index_of_station(station_id, after=-1):
        if station_id is None:
            return None
        for i, s in enumerate(stopovers):
            if i > after and s.get("id") == station_id:
                return i
        return None

    i_start = index_of_stopover_id(origin.get("stopoverId"))
    if i_start is None:
        i_start = index_of_station(origin.get("id"))

    i_end = index_of_stopover_id(destination.get("stopoverId"))
    if i_end is None and i_start is not None:
        # strikt nach Start: Ringfahrten (gleiche Stations-id) treffen den Rückkehr-Halt
        i_end = index_of_station(destination.get("id"), after=i_start)
    if i_end is None:
        i_end = index_of_station(destination.get("id"))

    if i_start is None or i_end is None or i_start > i_end:
        return stopovers
    return stopovers[i_start : i_end + 1]


def load_loc_class_families(path):
    """Liest die manuelle Baureihe→Familie-Zuordnung.

    JSON-Objekt-Syntax, in der derselbe Schlüssel mehrfach vorkommen darf
    (eine Baureihe in mehreren Familien). `json.load` würde sonst nur den
    letzten Wert behalten, daher `object_pairs_hook`. Schlüssel mit
    führendem '_' werden übersprungen. Fehlt die Datei, wird {} zurückgegeben.

    Rückgabe: {Baureihe: [Familie, ...]} in Dateireihenfolge, ohne
    doppelte Familie je Baureihe.
    """
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            pairs = json.load(f, object_pairs_hook=list)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Baureihenfamilien {path} nicht lesbar: {e}") from e
    if not isinstance(pairs, list):
        raise ValueError(
            f"Baureihenfamilien {path}: erwartet ein Objekt "
            f"{{Baureihe: Familie}}, bekommen {type(pairs).__name__}."
        )

    out = {}
    for item in pairs:
        if not (isinstance(item, (list, tuple)) and len(item) == 2):
            raise ValueError(
                f"Baureihenfamilien {path}: ungültiger Eintrag {item!r}."
            )
        key, value = item
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"Baureihenfamilien {path}: Wert für {key!r} muss "
                f"ein nicht-leerer String sein."
            )
        families = out.setdefault(key, [])
        if value not in families:
            families.append(value)
    return out


def pack_loc_class_families(mapping, variants):
    """Invertiert Baureihe→Familie(n) zu Familie→[Baureihen], nur Treffer in variants."""
    if not mapping:
        return {}
    present = {v[1] for v in variants if len(v) > 1 and v[1]}
    members = {}
    for loc_class, families in mapping.items():
        if loc_class not in present:
            continue
        if isinstance(families, str):
            families = [families]
        for family in families:
            bucket = members.setdefault(family, [])
            if loc_class not in bucket:
                bucket.append(loc_class)
    return {fam: sorted(cls) for fam, cls in sorted(members.items())}


def build_data(statuses, stations, ignore_plus=False, loc_class_families=None):
    """Berechnet KPIs, Tabellenzeilen und Kanten für das Dashboard.

    `ignore_plus`: wenn True, werden Wagennummern-Tags nicht am '+' getrennt
    (Doppeltraktion wie "463001+463501" bleibt ein Fahrzeug); Standard trennt.
    `loc_class_families`: optionales Mapping Baureihe → [Familie, ...] für den
    Kartenfilter (nur Familien mit mindestens einem Treffer in den Daten).
    """
    total_distance_m = 0
    total_duration_min = 0
    total_points = 0
    lines = set()
    station_ids = set()
    dates = []

    trips = []
    vehicle_records = []           # ein Datensatz je getaggter Wagennummer x Fahrt
    edge_counter = Counter()       # gerichtetes (id_from, id_to) -> Anzahl (je Fahrtrichtung)
    node_counter = Counter()       # station_id -> Befahrungen (eine je Station pro Fahrt)
    edge_names = {}                # id -> Name (für Anzeige)
    line_colors = {}               # lineKey (Name+Operator) -> [bg, fg]
    # Nach Fahrt-Attributen (Linie, Baureihe, Kategorie, Operator, Jahr)
    # aufgeschlüsselte Zähler für die filterbare Karte; JS summiert später die zum
    # Filter passenden Buckets auf. Die Attribut-Kombis werden beim Serialisieren
    # zu einer variants-Liste dedupliziert, damit das JSON kompakt bleibt.
    edge_lc = Counter()            # (id_from, id_to, *variant) -> Anzahl
    node_lc = Counter()            # (station_id, *variant) -> Befahrungen
    node_lc_used = Counter()       # (station_id, *variant) -> Ein-/Ausstiege
    # Fahrzeug-Buckets separat: Mehrfachwagen würden in der Variante beim
    # Aufsummieren von "Alle" doppelt zählen. Bei Fahrzeug-Filter greift JS
    # auf diese Buckets zu; ohne Filter bleiben edge_lc/node_lc maßgeblich.
    edge_veh = Counter()           # (id_from, id_to, veh_key, *variant) -> Anzahl
    node_veh = Counter()           # (station_id, veh_key, *variant) -> Befahrungen
    node_veh_used = Counter()      # (station_id, veh_key, *variant) -> Ein-/Ausstiege

    # Statistik-Akkumulatoren (Linie / Baureihe / Fahrzeug + Kreuztabellen).
    line_aggs = {}
    loc_aggs = {}
    veh_aggs = {}
    cross_stores = {
        "crossLocClassLine": {},
        "crossVehicleLine": {},
        "crossLineMonth": {},
        "crossLineWeekday": {},
        "crossLocClassMonth": {},
        "crossLocClassWeekday": {},
        "crossLocClassCategory": {},
        "crossLineCategory": {},
        "crossLineDelay": {},
        "crossLocClassDelay": {},
        "crossVehicleMonth": {},
    }
    # Unique-Kombis mit first/last (für Tagesziele + KPI-Zähler).
    line_vehicle = {}  # (lineKey, veh) -> DatedCombo
    line_loc = {}  # (lineKey, locClass) -> DatedCombo
    vehicle_loc = {}  # (veh, locClass) -> DatedCombo
    uniq_routes = set()
    station_line = {}  # (station_id, lineKey) -> DatedCombo
    trips_with_loc = 0
    trips_with_veh = 0
    min_trip = None  # {line, from, to, km, date}
    max_trip = None
    # Segmentkanten + Wiederholungs-Kombis für Statistik-Tab.
    edge_aggs = {}  # (from_id, to_id) -> EdgeAgg
    veh_edge = {}  # (veh, from_id, to_id) -> DatedCombo
    veh_edge_line = {}  # (veh, from_id, to_id, lineKey) -> DatedCombo
    line_edge = {}  # (lineKey, from_id, to_id) -> DatedCombo
    loc_edge = {}  # (locClass, from_id, to_id) -> DatedCombo
    loc_edge_line = {}  # (locClass, from_id, to_id, lineKey) -> DatedCombo
    station_aggs = {}  # station_id -> StationAgg (Ein/Aus/Durch)

    def get_agg(mapping, key):
        a = mapping.get(key)
        if a is None:
            a = EntityAgg()
            mapping[key] = a
        return a

    def get_edge(a_id, b_id, a_name, b_name):
        key = (a_id, b_id)
        e = edge_aggs.get(key)
        if e is None:
            e = EdgeAgg(a_name, b_name)
            edge_aggs[key] = e
        return e

    def get_station(sid, name=""):
        a = station_aggs.get(sid)
        if a is None:
            a = StationAgg(name)
            station_aggs[sid] = a
        elif name and (not a.name or a.name == str(sid)):
            a.name = name
        return a

    for status in statuses:
        checkin = status.get("checkin") or {}
        origin = checkin.get("origin") or {}
        destination = checkin.get("destination") or {}

        distance = checkin.get("distance") or 0
        duration = checkin.get("duration") or 0
        points = checkin.get("points")
        if points is None:
            points = status.get("points") or 0

        total_distance_m += distance
        total_duration_min += duration
        total_points += points

        dt, dep = trip_datetime(status)
        if dt:
            dates.append(dt)

        # Linie, Baureihe und Wagennummern der Fahrt. Baureihe/Wagennummer stammen
        # aus den Träwelling-Tags (nur bei selbst getaggten Fahrten gesetzt) und
        # werden für die Karte, die Fahrtenliste und die Fahrzeug-Datensätze genutzt.
        line_name = checkin.get("lineName") or ""
        category = checkin.get("category") or ""
        operator = (checkin.get("operator") or {}).get("name") or ""
        # Intern immer Name+Operator, damit z.B. zwei „S1“ nicht zusammenfallen.
        lk = line_key(line_name, operator) if line_name or operator else ""
        if lk:
            lines.add(lk)
        # Linienfarbe (Hintergrund/Text) aus dem Check-in; je (Linie, Operator) einmal.
        rc = checkin.get("routeColor")
        if lk and rc and lk not in line_colors:
            bg = rc if rc.startswith("#") else "#" + rc
            rtc = checkin.get("routeTextColor") or "000000"
            fg = rtc if rtc.startswith("#") else "#" + rtc
            line_colors[lk] = [bg, fg]
        tags = status.get("tags") or []
        loc_class = next(
            (
                t.get("value")
                for t in tags
                if t.get("key") == "trwl:locomotive_class" and t.get("value")
            ),
            "",
        )
        veh_values = [
            t.get("value")
            for t in tags
            if t.get("key") == "trwl:vehicle_number" and t.get("value")
        ]
        # Mehrfachwagen (z.B. "381,382") defensiv trennen. Ein "+" (Doppeltraktion,
        # z.B. "463001+463501") wird standardmäßig ebenfalls als Trenner behandelt
        # und ergibt zwei Fahrzeuge; --ignore-plus (ignore_plus) schaltet das ab.
        sep = r"[,;]" if ignore_plus else r"[,;+]"
        veh_numbers = [
            n.strip() for v in veh_values for n in re.split(sep, v) if n.strip()
        ]

        # Attribut-Variante dieser Fahrt für die filterbaren Karten-Buckets:
        # (Linien-Key, Baureihe, Kategorie, Operator, Datum). Das Jahr-Dropdown
        # leitet JS aus dem Datum ab (Präfix YYYY).
        date_str = dt.date().isoformat() if dt else (dep or "")[:10]
        variant = (lk, loc_class, category, operator, date_str)

        segment = traveled_stopovers(status)
        # Fahrzeug-Schlüssel dieser Fahrt: je getaggter Wagennummer (Baureihe, Nr.),
        # sonst ein Leer-Schlüssel für "(ohne Fahrzeug)" auf der Karte.
        veh_keys = [(loc_class, n) for n in veh_numbers] or [("", "")]
        # je Fahrt zählt jede Station nur einmal (trip-lokales Set); über mehrere
        # Fahrten hinweg summieren sich die Befahrungen aber weiter auf.
        seen_in_trip = set()
        for s in segment:
            sid = s.get("id")
            if sid is None:
                continue
            station_ids.add(sid)
            edge_names[sid] = s.get("name")
            if sid not in seen_in_trip:
                seen_in_trip.add(sid)
                node_counter[sid] += 1
                node_lc[(sid, *variant)] += 1
                for vk in veh_keys:
                    node_veh[(sid, vk, *variant)] += 1
        def mark_used_node(sid):
            """Ein-/Ausstieg dieser Fahrt für die Karten-Marker (filtertreu)."""
            node_lc_used[(sid, *variant)] += 1
            for vk in veh_keys:
                node_veh_used[(sid, vk, *variant)] += 1

        # Station-Rollen: Einstieg = segment[0], Ausstieg = segment[-1],
        # Durchfahrt = Zwischenhalte (pro Fahrt je Station-ID einmal).
        # len==1: dieselbe Station zählt als Ein und Aus, nicht als Durchfahrt.
        if segment:
            s0 = segment[0]
            sid0 = s0.get("id")
            if sid0 is not None:
                get_station(
                    sid0, s0.get("name") or edge_names.get(sid0) or str(sid0)
                ).add_boarded(date_str, line=lk)
                mark_used_node(sid0)
                if lk:
                    _get_dated(station_line, (sid0, lk)).add(date_str)
            if len(segment) == 1:
                if sid0 is not None:
                    get_station(sid0).add_alighted(date_str, line=lk)
            else:
                sn = segment[-1]
                sidn = sn.get("id")
                if sidn is not None:
                    get_station(
                        sidn, sn.get("name") or edge_names.get(sidn) or str(sidn)
                    ).add_alighted(date_str, line=lk)
                    if sidn != sid0:
                        mark_used_node(sidn)
                    if lk:
                        _get_dated(station_line, (sidn, lk)).add(date_str)
                seen_through = set()
                for s in segment[1:-1]:
                    sid = s.get("id")
                    if sid is None or sid in seen_through:
                        continue
                    seen_through.add(sid)
                    get_station(
                        sid, s.get("name") or edge_names.get(sid) or str(sid)
                    ).add_through(date_str, line=lk)
                    if lk:
                        _get_dated(station_line, (sid, lk)).add(date_str)
        # gerichtete Kanten zwischen aufeinanderfolgenden *nicht* cancelled Halten:
        # ausgefallene Zwischenhalte überspringen (A→B✕→C wird zu A→C).
        # Jede Fahrtrichtung zählt in ihre eigene Kante (from->to als Schlüssel).
        edge_segment = [s for s in segment if not s.get("cancelled")]
        for a, b in zip(edge_segment, edge_segment[1:]):
            a_id, b_id = a.get("id"), b.get("id")
            if a_id is None or b_id is None or a_id == b_id:
                continue
            edge_counter[(a_id, b_id)] += 1
            edge_lc[(a_id, b_id, *variant)] += 1
            for vk in veh_keys:
                edge_veh[(a_id, b_id, vk, *variant)] += 1
            a_name = a.get("name") or edge_names.get(a_id) or str(a_id)
            b_name = b.get("name") or edge_names.get(b_id) or str(b_id)
            get_edge(a_id, b_id, a_name, b_name).add(
                date_str, line=lk, loc_class=loc_class, vehicles=veh_numbers
            )
            if lk:
                _get_dated(line_edge, (lk, a_id, b_id)).add(date_str)
            if loc_class:
                _get_dated(loc_edge, (loc_class, a_id, b_id)).add(date_str)
                if lk:
                    _get_dated(loc_edge_line, (loc_class, a_id, b_id, lk)).add(date_str)
            for num in veh_numbers:
                ve = _get_dated(veh_edge, (num, a_id, b_id))
                ve.add(date_str, line=lk)
                if lk:
                    _get_dated(veh_edge_line, (num, a_id, b_id, lk)).add(date_str)

        # Detail-Stopovers für die Tabelle
        detail = [
            {
                "id": s.get("id"),
                "name": s.get("name"),
                "arrivalPlanned": s.get("arrivalPlanned"),
                "arrivalReal": s.get("arrivalReal"),
                "departurePlanned": s.get("departurePlanned"),
                "departureReal": s.get("departureReal"),
                "platform": s.get("departurePlatformReal")
                or s.get("departurePlatformPlanned")
                or s.get("platform"),
                "cancelled": s.get("cancelled", False),
            }
            for s in segment
        ]

        trips.append(
            {
                "date": dep,
                "line": lk,
                "operator": operator,
                "category": checkin.get("category") or "",
                "locClass": loc_class,
                "vehicles": ", ".join(veh_numbers),
                "from": origin.get("name") or "",
                "to": destination.get("name") or "",
                "depPlanned": origin.get("departurePlanned") or None,
                "depReal": origin.get("departureReal")
                or checkin.get("manualDeparture")
                or None,
                "arrPlanned": destination.get("arrivalPlanned") or None,
                "arrReal": destination.get("arrivalReal")
                or checkin.get("manualArrival")
                or None,
                "viaStops": max(0, len(segment) - 2),
                "distanceKm": round(distance / 1000, 1),
                "durationMin": duration,
                "delay": delay_minutes(
                    destination.get("arrivalPlanned"), destination.get("arrivalReal")
                ),
                "points": points,
                "body": status.get("body") or "",
                "stopovers": detail,
            }
        )

        # --- Statistik: Linie / Baureihe / Fahrzeug ---
        distance_km = round(distance / 1000, 1)
        from_name = origin.get("name") or ""
        to_name = destination.get("name") or ""
        route = f"{from_name} → {to_name}" if (from_name or to_name) else ""
        # date_str schon oben für Kanten-Stats gesetzt
        weekday = dt.weekday() if dt else None
        month = date_str[:7] if len(date_str) >= 7 else ""
        delay = delay_minutes(
            destination.get("arrivalPlanned"), destination.get("arrivalReal")
        )
        n_segments = max(0, len(edge_segment) - 1)
        d_bucket = delay_bucket(delay)
        wd_key = str(weekday) if weekday is not None else ""

        if route:
            uniq_routes.add(route)
        trip_info = {
            "line": line_name,
            "from": from_name,
            "to": to_name,
            "km": distance_km,
            "date": date_str,
        }
        if min_trip is None or distance_km < min_trip["km"]:
            min_trip = trip_info
        if max_trip is None or distance_km > max_trip["km"]:
            max_trip = trip_info

        if loc_class:
            trips_with_loc += 1
        if veh_numbers:
            trips_with_veh += 1

        if lk:
            get_agg(line_aggs, lk).add(
                distance_km=distance_km,
                duration_min=duration,
                points=points,
                segments=n_segments,
                delay=delay,
                date_str=date_str,
                route=route,
                weekday=weekday,
                month=month,
                loc_class=loc_class,
                vehicles=veh_numbers,
            )
            _bump_cross(cross_stores["crossLineMonth"], lk, month, distance_km)
            _bump_cross(cross_stores["crossLineWeekday"], lk, wd_key, distance_km)
            _bump_cross(
                cross_stores["crossLineCategory"], lk, category, distance_km
            )
            _bump_cross(
                cross_stores["crossLineDelay"], lk, d_bucket, distance_km
            )

        if loc_class:
            get_agg(loc_aggs, loc_class).add(
                distance_km=distance_km,
                duration_min=duration,
                points=points,
                segments=n_segments,
                delay=delay,
                date_str=date_str,
                route=(f"{line_name}: {route}" if line_name else route),
                weekday=weekday,
                month=month,
                line=lk,
                vehicles=veh_numbers,
            )
            _bump_cross(
                cross_stores["crossLocClassLine"], loc_class, lk, distance_km
            )
            _bump_cross(
                cross_stores["crossLocClassMonth"], loc_class, month, distance_km
            )
            _bump_cross(
                cross_stores["crossLocClassWeekday"], loc_class, wd_key, distance_km
            )
            _bump_cross(
                cross_stores["crossLocClassCategory"],
                loc_class,
                category,
                distance_km,
            )
            _bump_cross(
                cross_stores["crossLocClassDelay"], loc_class, d_bucket, distance_km
            )
            if lk:
                _get_dated(line_loc, (lk, loc_class)).add(date_str)

        for num in veh_numbers:
            get_agg(veh_aggs, num).add(
                distance_km=distance_km,
                duration_min=duration,
                points=points,
                segments=n_segments,
                delay=delay,
                date_str=date_str,
                route=(f"{line_name}: {route}" if line_name else route),
                weekday=weekday,
                month=month,
                line=lk,
                loc_class=loc_class,
            )
            _bump_cross(
                cross_stores["crossVehicleLine"], num, lk, distance_km
            )
            _bump_cross(
                cross_stores["crossVehicleMonth"], num, month, distance_km
            )
            if lk:
                _get_dated(line_vehicle, (lk, num)).add(date_str)
            _get_dated(vehicle_loc, (num, loc_class or "")).add(date_str)

        # Fahrzeug-Datensätze aus den Träwelling-Tags (sofern vorhanden).
        # loc_class und veh_numbers sind oben schon ermittelt.
        for num in veh_numbers:
            vehicle_records.append(
                {
                    "vehicleNumber": num,
                    "locClass": loc_class,
                    "line": lk,
                    "category": checkin.get("category") or "",
                    "operator": operator,
                    "date": dt.date().isoformat() if dt else (dep or "")[:10],
                    "from": origin.get("name") or "",
                    "to": destination.get("name") or "",
                    "depTime": dep,
                    "arrTime": destination.get("arrivalReal")
                    or destination.get("arrivalPlanned"),
                    "distanceKm": round(distance / 1000, 1),
                    "durationMin": duration,
                    "points": points,
                    "segments": max(0, len(segment) - 1),
                }
            )

    # Segment-Rangliste für die Übersicht: global über alle Fahrten, ungefiltert.
    segment_ranking = []
    for (a_id, b_id), count in edge_counter.items():
        a = stations.get(str(a_id)) or stations.get(a_id) or {}
        b = stations.get(str(b_id)) or stations.get(b_id) or {}
        a_name = a.get("name") or edge_names.get(a_id) or str(a_id)
        b_name = b.get("name") or edge_names.get(b_id) or str(b_id)
        segment_ranking.append({"from": a_name, "to": b_name, "count": count})
    segment_ranking.sort(key=lambda x: x["count"], reverse=True)

    # Karten-Daten nach (Linie, Baureihe) aufgeschlüsselt. Die Koordinaten liegen
    # einmalig in map_stations; Kanten/Knoten referenzieren nur Station-IDs, damit
    # das eingebettete JSON kompakt bleibt. JS summiert die zum Filter passenden
    # Buckets auf und berechnet Farb-/Dicken-Skala je Ansicht neu.
    map_stations = {}

    def station_coord(sid):
        """[lat, lon, name] der Station oder None, falls Koordinaten fehlen."""
        st = stations.get(str(sid)) or stations.get(sid) or {}
        if st.get("latitude") is None or st.get("longitude") is None:
            return None
        name = st.get("name") or edge_names.get(sid) or str(sid)
        return [st["latitude"], st["longitude"], name]

    # Attribut-Varianten deduplizieren: jede eindeutige (line, loc, category,
    # operator, date) erhält einen Index; edges/nodes referenzieren nur den Index,
    # damit sich die Strings im JSON nicht pro Kante/Knoten wiederholen.
    variants = []
    variant_index = {}

    def variant_id(v):
        idx = variant_index.get(v)
        if idx is None:
            idx = len(variants)
            variant_index[v] = idx
            variants.append(list(v))
        return idx

    map_edges = []
    for key, count in edge_lc.items():
        a_id, b_id = key[0], key[1]
        ca, cb = station_coord(a_id), station_coord(b_id)
        if ca is None or cb is None:
            continue
        map_stations[str(a_id)] = ca
        map_stations[str(b_id)] = cb
        map_edges.append([a_id, b_id, variant_id(key[2:]), count])

    map_nodes = []
    for key, cnt in node_lc.items():
        sid = key[0]
        c = station_coord(sid)
        if c is None:
            continue
        map_stations[str(sid)] = c
        map_nodes.append([sid, variant_id(key[1:]), cnt, node_lc_used.get(key, 0)])

    # Fahrzeug-Liste + Kanten/Knoten mit Fahrzeug-Index (für Karten-Filter).
    map_vehicles = []  # [locClass, number]
    vehicle_index = {}

    def vehicle_id(vk):
        idx = vehicle_index.get(vk)
        if idx is None:
            idx = len(map_vehicles)
            vehicle_index[vk] = idx
            map_vehicles.append(list(vk))
        return idx

    map_veh_edges = []
    for key, count in edge_veh.items():
        a_id, b_id, vk = key[0], key[1], key[2]
        ca, cb = station_coord(a_id), station_coord(b_id)
        if ca is None or cb is None:
            continue
        map_stations[str(a_id)] = ca
        map_stations[str(b_id)] = cb
        map_veh_edges.append(
            [a_id, b_id, variant_id(key[3:]), vehicle_id(vk), count]
        )

    map_veh_nodes = []
    for key, cnt in node_veh.items():
        sid, vk = key[0], key[1]
        c = station_coord(sid)
        if c is None:
            continue
        map_stations[str(sid)] = c
        map_veh_nodes.append(
            [sid, variant_id(key[2:]), vehicle_id(vk), cnt, node_veh_used.get(key, 0)]
        )

    dates.sort()
    kpis = {
        "count": len(trips),
        "distanceKm": round(total_distance_m / 1000, 1),
        "durationMin": total_duration_min,
        "points": total_points,
        "stations": len(station_ids),
        "lines": len(lines),
        "first": dates[0].date().isoformat() if dates else None,
        "last": dates[-1].date().isoformat() if dates else None,
    }

    n_trips = len(trips) or 1
    # Volle Rankings/Kreuztabellen; die UI zeigt zunächst die initialen Top-N
    # und lädt den Rest per „Mehr laden“.
    by_line = _finalize_entities(line_aggs, "line")
    by_loc = _finalize_entities(loc_aggs, "locClass")
    by_veh = _finalize_entities(veh_aggs, "vehicle")
    cross_loc_line = _pack_cross(cross_stores["crossLocClassLine"])
    cross_veh_line = _pack_cross(cross_stores["crossVehicleLine"])
    months_sorted = sorted(
        {c for (_r, c) in cross_stores["crossLineMonth"]}
        | {c for (_r, c) in cross_stores["crossLocClassMonth"]}
        | {c for (_r, c) in cross_stores["crossVehicleMonth"]}
    )
    cross_line_month = _pack_cross(
        cross_stores["crossLineMonth"], col_order=months_sorted
    )
    cross_line_weekday = _pack_cross(
        cross_stores["crossLineWeekday"],
        col_order=list(WEEKDAY_KEYS),
    )
    cross_loc_month = _pack_cross(
        cross_stores["crossLocClassMonth"], col_order=months_sorted
    )
    cross_loc_weekday = _pack_cross(
        cross_stores["crossLocClassWeekday"], col_order=list(WEEKDAY_KEYS)
    )
    cross_loc_cat = _pack_cross(cross_stores["crossLocClassCategory"])
    cross_line_cat = _pack_cross(cross_stores["crossLineCategory"])
    cross_line_delay = _pack_cross(
        cross_stores["crossLineDelay"],
        col_order=list(DELAY_BUCKETS),
    )
    cross_loc_delay = _pack_cross(
        cross_stores["crossLocClassDelay"], col_order=list(DELAY_BUCKETS)
    )
    cross_veh_month = _pack_cross(
        cross_stores["crossVehicleMonth"],
        col_order=months_sorted,
    )

    top_line = by_line[0] if by_line else None
    top_veh = by_veh[0] if by_veh else None

    by_station = [a.to_row() for a in station_aggs.values()]
    by_station.sort(
        key=lambda r: (-r["total"], -r["boarded"], -r["alighted"], -r["through"], r["key"])
    )
    stations_boarded_n = sum(1 for a in station_aggs.values() if a.boarded)
    stations_alighted_n = sum(1 for a in station_aggs.values() if a.alighted)
    stations_through_n = sum(1 for a in station_aggs.values() if a.through)

    # --- Kanten- und Wiederholungs-Statistiken ---
    def edge_names_for(a_id, b_id):
        e = edge_aggs.get((a_id, b_id))
        if e:
            return e.from_name, e.to_name
        a = stations.get(str(a_id)) or stations.get(a_id) or {}
        b = stations.get(str(b_id)) or stations.get(b_id) or {}
        return (
            a.get("name") or edge_names.get(a_id) or str(a_id),
            b.get("name") or edge_names.get(b_id) or str(b_id),
        )

    all_edge_rows = [e.to_row() for e in edge_aggs.values()]
    # Volle Listen; die UI zeigt initial 40 und lädt den Rest per „Mehr laden“.
    edges_by_count = sorted(
        all_edge_rows, key=lambda r: (-r["count"], r["from"], r["to"])
    )
    # „Am längsten nicht befahren“ / Einmal-Kanten: nach last aufsteigend
    # (= meiste Tage her). Absolute „Tage her“ berechnet das Frontend am Systemdatum.
    edges_by_days = sorted(
        all_edge_rows,
        key=lambda r: (r["last"] or "9999", -r["count"], r["from"]),
    )
    edges_by_last = sorted(
        all_edge_rows,
        key=lambda r: (r["last"] or "", r["from"]),
        reverse=True,
    )
    edges_by_first = sorted(
        all_edge_rows,
        key=lambda r: (r["first"] or "9999", r["from"]),
    )
    edges_by_first_new = sorted(
        all_edge_rows,
        key=lambda r: (r["first"] or "", r["from"]),
        reverse=True,
    )
    edges_once = sorted(
        [r for r in all_edge_rows if r["count"] == 1],
        key=lambda r: (r["last"] or "9999", r["from"]),
    )

    def combo_sort_key(r):
        return (-r["count"], r.get("last") or "", r.get("from") or "")

    veh_edge_gt1_all = []
    for (num, a_id, b_id), c in veh_edge.items():
        if c.count <= 1:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        veh_edge_gt1_all.append({
            "vehicle": num,
            "from": fn,
            "to": tn,
            "count": c.count,
            "first": c.first,
            "last": c.last,
            "lines": len(c.lines),
        })
    veh_edge_gt1_all.sort(key=combo_sort_key)
    veh_edge_repeat_n = len(veh_edge_gt1_all)
    veh_edge_gt1 = veh_edge_gt1_all

    veh_edge_line_gt1 = []
    for (num, a_id, b_id, line), c in veh_edge_line.items():
        if c.count <= 1:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        veh_edge_line_gt1.append({
            "vehicle": num,
            "from": fn,
            "to": tn,
            "line": line,
            "count": c.count,
            "first": c.first,
            "last": c.last,
        })
    veh_edge_line_gt1.sort(key=combo_sort_key)

    line_edge_gt1 = []
    for (line, a_id, b_id), c in line_edge.items():
        if c.count <= 1:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        line_edge_gt1.append({
            "line": line,
            "from": fn,
            "to": tn,
            "count": c.count,
            "first": c.first,
            "last": c.last,
        })
    line_edge_gt1.sort(key=combo_sort_key)

    loc_edge_gt1 = []
    for (loc, a_id, b_id), c in loc_edge.items():
        if c.count <= 1:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        loc_edge_gt1.append({
            "locClass": loc,
            "from": fn,
            "to": tn,
            "count": c.count,
            "first": c.first,
            "last": c.last,
        })
    loc_edge_gt1.sort(key=combo_sort_key)

    multi_vehicle_edges = sorted(
        [r for r in all_edge_rows if r["uniqueVehicles"] > 1],
        key=lambda r: (-r["uniqueVehicles"], -r["count"], r["from"]),
    )

    edges_repeat_n = sum(1 for r in all_edge_rows if r["count"] > 1)

    stats = {
        "extra": {
            "lines": len(line_aggs),
            "locClasses": len(loc_aggs),
            "vehicles": len(veh_aggs),
            "tagLocPct": round(100.0 * trips_with_loc / n_trips, 1),
            "tagVehPct": round(100.0 * trips_with_veh / n_trips, 1),
            "uniqueLineVehicle": len(line_vehicle),
            "uniqueLineLocClass": len(line_loc),
            "uniqueVehicleLocClass": len(vehicle_loc),
            "uniqueRoutes": len(uniq_routes),
            "edges": len(edge_aggs),
            "edgesRepeat": edges_repeat_n,
            "vehEdgeRepeat": veh_edge_repeat_n,
            "stationsBoarded": stations_boarded_n,
            "stationsAlighted": stations_alighted_n,
            "stationsThrough": stations_through_n,
            "minTrip": min_trip,
            "maxTrip": max_trip,
            "topLine": (
                {"key": top_line["key"], "km": top_line["distanceKm"]}
                if top_line
                else None
            ),
            "topVehicle": (
                {"key": top_veh["key"], "km": top_veh["distanceKm"]}
                if top_veh
                else None
            ),
        },
        "byLine": by_line,
        "byLocClass": by_loc,
        "byVehicle": by_veh,
        "byStation": by_station,
        "crossLocClassLine": cross_loc_line,
        "crossVehicleLine": cross_veh_line,
        "crossLineMonth": cross_line_month,
        "crossLineWeekday": cross_line_weekday,
        "crossLocClassMonth": cross_loc_month,
        "crossLocClassWeekday": cross_loc_weekday,
        "crossLocClassCategory": cross_loc_cat,
        "crossLineCategory": cross_line_cat,
        "crossLineDelay": cross_line_delay,
        "crossLocClassDelay": cross_loc_delay,
        "crossVehicleMonth": cross_veh_month,
        "edgesByCount": edges_by_count,
        "edgesByDaysSince": edges_by_days,
        "edgesByLast": edges_by_last,
        "edgesByFirst": edges_by_first,
        "edgesByFirstNew": edges_by_first_new,
        "edgesOnce": edges_once,
        "vehEdgeGt1": veh_edge_gt1,
        "vehEdgeLineGt1": veh_edge_line_gt1,
        "lineEdgeGt1": line_edge_gt1,
        "locEdgeGt1": loc_edge_gt1,
        "multiVehicleEdges": multi_vehicle_edges,
    }

    # --- Tagesziele: Erstvorkommen und Wiederholungen je Kalendertag ---
    daily_firsts = {}
    daily_repeats = {}

    def _push_day(store, date_str, bucket, row):
        if not date_str:
            return
        day = store.setdefault(date_str, {})
        day.setdefault(bucket, []).append(row)

    def _emit_dated(dates, first, bucket, row):
        _push_day(daily_firsts, first, bucket, row)
        for d in dates or ():
            if d and d != first:
                _push_day(daily_repeats, d, bucket, row)

    for k, a in line_aggs.items():
        if k and a.first:
            _emit_dated(a.dates, a.first, "lines", {"key": k})
    for k, a in loc_aggs.items():
        if k and a.first:
            _emit_dated(a.dates, a.first, "locClasses", {"key": k})
    for (num, loc), c in vehicle_loc.items():
        if not num or not c.first:
            continue
        row = {"key": num}
        if loc:
            row["locClass"] = loc
        _emit_dated(c.dates, c.first, "vehicles", row)
    for e in edge_aggs.values():
        if e.first:
            _emit_dated(
                e.dates, e.first, "edges", {"from": e.from_name, "to": e.to_name}
            )
    for sid, a in station_aggs.items():
        name = a.name or str(sid)
        if a.first_used:
            _emit_dated(a.dates_used, a.first_used, "stationsUsed", {"key": name})
        if a.first_through:
            _emit_dated(
                a.dates_through, a.first_through, "stationsThrough", {"key": name}
            )

    for (line, veh), c in line_vehicle.items():
        if c.first:
            _emit_dated(
                c.dates, c.first, "lineVehicle", {"line": line, "vehicle": veh}
            )
    for (line, loc), c in line_loc.items():
        if c.first:
            _emit_dated(
                c.dates, c.first, "lineLocClass", {"line": line, "locClass": loc}
            )

    for (num, a_id, b_id), c in veh_edge.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "vehEdge",
            {"vehicle": num, "from": fn, "to": tn},
        )
    for (line, a_id, b_id), c in line_edge.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "lineEdge",
            {"line": line, "from": fn, "to": tn},
        )
    for (loc, a_id, b_id), c in loc_edge.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "locEdge",
            {"locClass": loc, "from": fn, "to": tn},
        )

    for (sid, line), c in station_line.items():
        if not c.first:
            continue
        st = station_aggs.get(sid)
        name = (st.name if st else "") or edge_names.get(sid) or str(sid)
        _emit_dated(
            c.dates, c.first, "stationLine", {"station": name, "line": line}
        )

    for (num, a_id, b_id, line), c in veh_edge_line.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "vehEdgeLine",
            {"vehicle": num, "from": fn, "to": tn, "line": line},
        )
    for (loc, a_id, b_id, line), c in loc_edge_line.items():
        if not c.first:
            continue
        fn, tn = edge_names_for(a_id, b_id)
        _emit_dated(
            c.dates, c.first, "locEdgeLine",
            {"locClass": loc, "from": fn, "to": tn, "line": line},
        )

    # Top-Wagen der Baureihe: nur Neu, wenn ein Wagen den bisherigen Leader
    # derselben Baureihe strikt überholt (km, dann Fahrten). Das erste Fahrzeug
    # einer Baureihe zählt nicht — das steht schon unter „Fahrzeuge“.
    class_stats = {}  # locClass -> {veh: [km, count]}
    last_leader = {}  # locClass -> letzter eindeutiger Leader
    for rec in sorted(
        vehicle_records,
        key=lambda r: (r.get("date") or "", r.get("depTime") or "", r.get("vehicleNumber") or ""),
    ):
        loc = rec.get("locClass") or ""
        veh = rec.get("vehicleNumber") or ""
        if not loc or not veh:
            continue
        veh_stats = class_stats.setdefault(loc, {})
        km, n = veh_stats.get(veh, (0.0, 0))
        veh_stats[veh] = (km + (rec.get("distanceKm") or 0), n + 1)
        ranked = sorted(
            veh_stats.items(),
            key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]),
        )
        new_leader = ranked[0][0]
        if len(ranked) > 1:
            top_km, top_n = ranked[0][1]
            next_km, next_n = ranked[1][1]
            if (top_km, top_n) == (next_km, next_n):
                new_leader = None
        prev = last_leader.get(loc)
        if new_leader and prev and new_leader != prev:
            top_km, top_n = veh_stats[new_leader]
            _push_day(
                daily_firsts,
                rec.get("date") or "",
                "topVehicleInClass",
                {
                    "vehicle": new_leader,
                    "locClass": loc,
                    "prevVehicle": prev,
                    "km": round(top_km, 1),
                    "count": top_n,
                },
            )
        if new_leader:
            last_leader[loc] = new_leader

    # Stabile Sortierung innerhalb der Buckets.
    def _sort_key(row):
        return tuple(str(row.get(k, "")) for k in (
            "key", "line", "vehicle", "locClass", "station", "from", "to",
            "prevVehicle",
        ))

    for day_buckets in list(daily_firsts.values()) + list(daily_repeats.values()):
        for bucket, rows in day_buckets.items():
            rows.sort(key=_sort_key)

    return {
        "kpis": kpis,
        "trips": trips,
        "vehicles": vehicle_records,
        "lineColors": line_colors,
        "stations": map_stations,
        "variants": variants,
        "edges": map_edges,
        "nodes": map_nodes,
        "mapVehicles": map_vehicles,
        "vehEdges": map_veh_edges,
        "vehNodes": map_veh_nodes,
        "segments": segment_ranking,
        "stats": stats,
        "dailyFirsts": daily_firsts,
        "dailyRepeats": daily_repeats,
        "locClassFamilies": pack_loc_class_families(
            loc_class_families or {}, variants
        ),
    }


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Walita Dashboard</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
  integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
<style>
  :root { --bg:#f4f6fb; --panel:#ffffff; --panel2:#eef2f8; --fg:#111a27; --muted:#64748b;
          --accent:#2563eb; --accent-fg:#ffffff; --accent-soft:#2563eb14; --border:#e3e8f0;
          --veh-streak-gold:#d4a017;
          --sidebar:#0f172a; --sidebar-fg:#e6ecf5; --sidebar-muted:#8b98ad;
          --sidebar-active:#1e293b; --sidebar-border:#1e293b;
          --pos:#d1242f; --neg:#1a7f37;
          --shadow:0 1px 2px rgba(15,23,42,.06), 0 4px 12px rgba(15,23,42,.05);
          --radius:12px; }
  :root[data-theme="dark"] { --bg:#0b1220; --panel:#141d2e; --panel2:#1b2740; --fg:#e7eef8;
          --muted:#93a2b8; --accent:#5b95f7; --accent-fg:#0b1220; --accent-soft:#5b95f724;
          --veh-streak-gold:#e6b422;
          --border:#28344c; --sidebar:#0a0f1c; --sidebar-fg:#e7eef8; --sidebar-muted:#7f8da3;
          --sidebar-active:#182238; --sidebar-border:#1a2237; --pos:#ff6b74; --neg:#4ade80;
          --shadow:0 1px 2px rgba(0,0,0,.4), 0 6px 18px rgba(0,0,0,.35); }
  * { box-sizing:border-box; }
  html,body { height:100%; overflow:hidden; }
  body { margin:0; background:var(--bg); color:var(--fg); display:flex;
         font:14px/1.5 system-ui,Segoe UI,Roboto,sans-serif;
         -webkit-font-smoothing:antialiased; }

  /* ---- Sidebar ---- */
  .sidebar { flex:0 0 224px; width:224px; background:var(--sidebar); color:var(--sidebar-fg);
        display:flex; flex-direction:column; height:100%; min-height:0;
        border-right:1px solid var(--sidebar-border); }
  .brand { padding:20px 20px 16px; }
  .brand .logo { display:flex; align-items:center; gap:10px; font-size:16px; font-weight:700;
        letter-spacing:.01em; }
  .brand .logo .mark { width:30px; height:30px; border-radius:9px; display:grid;
        place-items:center; background:linear-gradient(135deg,var(--accent),#7c3aed);
        font-size:16px; }
  .navlist { flex:1; display:flex; flex-direction:column; gap:2px; padding:6px 12px;
        overflow:auto; }
  .navlist > button, .nav-group > button { display:flex; align-items:center; gap:12px;
        width:100%; text-align:left; background:none; border:none; color:var(--sidebar-muted);
        padding:10px 12px; border-radius:9px; cursor:pointer; font-size:14px; font-weight:500;
        transition:background .12s,color .12s; }
  .navlist > button .ic, .nav-group > button .ic { font-size:17px; width:20px; text-align:center;
        flex:0 0 20px; }
  .navlist > button:hover, .nav-group > button:hover { background:var(--sidebar-active);
        color:var(--sidebar-fg); }
  .navlist > button.active, .nav-group > button.active { background:var(--sidebar-active);
        color:var(--sidebar-fg); }
  .nav-group { display:flex; flex-direction:column; gap:1px; }
  .nav-sub { display:none; flex-direction:column; gap:1px; padding:2px 0 8px 10px; }
  .nav-group.open .nav-sub { display:flex; }
  .nav-sub a { display:block; color:var(--sidebar-muted); text-decoration:none;
        padding:5px 10px; border-radius:7px; font-size:12.5px; line-height:1.35;
        font-weight:500; }
  .nav-sub a:hover { background:var(--sidebar-active); color:var(--sidebar-fg); }
  .nav-sub .nav-sec { display:flex; flex-direction:column; gap:1px; margin-top:4px; }
  .nav-sub .nav-sec:first-child { margin-top:0; }
  .nav-sub .nav-sec-head { color:var(--sidebar-fg); font-weight:600; font-size:12.5px; }
  .nav-sub .nav-leaf { padding-left:18px; font-size:12px; font-weight:400; }
  .sidebar-foot { padding:14px 16px; border-top:1px solid var(--sidebar-border);
        display:flex; flex-direction:column; gap:10px; }
  .sidebar-foot .sub { color:var(--sidebar-muted); font-size:12px; }
  .theme-toggle { display:flex; align-items:center; justify-content:center; gap:8px;
        background:var(--sidebar-active); color:var(--sidebar-fg); border:none;
        border-radius:9px; padding:9px 12px; cursor:pointer; font-size:13px; font-weight:500; }
  .theme-toggle:hover { filter:brightness(1.15); }

  /* ---- Main ---- */
  main { flex:1; min-width:0; min-height:0; height:100%; overflow-y:auto; }
  .tab { display:none; padding:28px 32px; }
  .tab.active { display:block; }
  .tab-head { margin:0 0 20px; }
  .tab-head h2 { margin:0; font-size:22px; font-weight:700; }
  .tab-head p { margin:4px 0 0; color:var(--muted); font-size:13px; }

  /* ---- Cards / KPIs ---- */
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:16px; }
  .card { background:var(--panel); border:1px solid var(--border); border-radius:var(--radius);
          padding:18px 20px; box-shadow:var(--shadow); }
  .card .v { font-size:27px; font-weight:700; letter-spacing:-.01em; }
  .card .l { color:var(--muted); font-size:11px; text-transform:uppercase;
             letter-spacing:.05em; margin-top:6px; font-weight:600; }
  h3 { font-size:16px; font-weight:700; }

  #mapview { height:74vh; border:1px solid var(--border); border-radius:var(--radius);
        background:var(--panel2); box-shadow:var(--shadow); }
  .legend { background:var(--panel); padding:9px 12px; border-radius:10px;
            border:1px solid var(--border); color:var(--fg); font-size:12px; line-height:1.8;
            box-shadow:var(--shadow); }
  .legend i { display:inline-block; width:24px; height:4px; margin-right:6px;
              vertical-align:middle; border-radius:2px; }
  .leaflet-popup-content-wrapper, .leaflet-popup-tip { background:var(--panel); color:var(--fg); }

  .toolbar { margin-bottom:16px; display:flex; gap:12px; align-items:center; flex-wrap:wrap;
        background:var(--panel); border:1px solid var(--border); border-radius:var(--radius);
        padding:12px 14px; box-shadow:var(--shadow); }
  input[type=search] { background:var(--panel2); border:1px solid var(--border); color:var(--fg);
        padding:9px 12px; border-radius:9px; width:260px; font-size:14px; }
  input[type=search]:focus, .ctl select:focus, .ctl input[type=date]:focus {
        outline:2px solid var(--accent-soft); border-color:var(--accent); }

  .panel { background:var(--panel); border:1px solid var(--border); border-radius:var(--radius);
        box-shadow:var(--shadow); overflow:hidden; }
  .panel > h3 { margin:0; padding:14px 16px; border-bottom:1px solid var(--border); font-size:15px; }
  /* Tabellen-Panels: intern scrollbar, max. ~800px */
  #overview .panel,
  #trips .panel { max-height:800px; overflow:auto; }
  table { width:100%; border-collapse:collapse; }
  .panel table { padding:0; }
  th,td { text-align:left; padding:9px 12px; border-bottom:1px solid var(--border);
          font-size:13px; }
  th { color:var(--muted); cursor:pointer; user-select:none; position:sticky; top:0;
       background:var(--panel); font-weight:600; z-index:1; }
  th.sorted::after { content:" ▾"; }
  th.sorted.asc::after { content:" ▴"; }
  tbody tr:last-child td { border-bottom:none; }
  tr.trip { cursor:pointer; }
  tr.trip:hover { background:var(--panel2); }
  .pos { color:var(--pos); } .neg { color:var(--neg); }
  .detail td { background:var(--panel2); }
  .stops { display:grid; grid-template-columns:1fr auto auto auto; gap:2px 16px; }
  .stops .h { color:var(--muted); font-size:11px; text-transform:uppercase; }
  .muted { color:var(--muted); }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:24px; }
  @media (max-width:800px){ .grid2{grid-template-columns:1fr;} }
  /* Fahrzeug-Matrix */
  .ctl { color:var(--muted); font-size:13px; display:inline-flex; align-items:center; gap:6px; }
  .ctl select, .ctl input[type=date] { background:var(--panel2); border:1px solid var(--border); color:var(--fg);
        padding:8px 10px; border-radius:9px; font-size:13px; }
  .ctl-checks { flex-wrap:wrap; gap:8px 12px; }
  .ctl-checks label { display:inline-flex; align-items:center; gap:4px; color:var(--fg);
        cursor:pointer; white-space:nowrap; }
  .ctl-checks input { margin:0; accent-color:var(--accent); }
  #tripsTable.no-delay th[data-k="delay"],
  #tripsTable.no-delay td.delay { display:none; }
  #tripsTable.no-route th[data-k="route"],
  #tripsTable.no-route td.route { display:none; }
  #tripsTable td.route { white-space:normal; max-width:28em; line-height:1.35; }
  .matrix-wrap { overflow:auto; max-height:800px; border:1px solid var(--border);
        border-radius:var(--radius); margin-bottom:24px; background:var(--panel);
        box-shadow:var(--shadow); }
  .matrix-wrap h3 { margin:0; padding:13px 15px; border-bottom:1px solid var(--border);
        font-size:15px; }
  table.matrix { width:auto; }
  table.matrix th, table.matrix td { white-space:nowrap; text-align:center;
        padding:4px 8px; font-size:12px; border-right:1px solid var(--border); }
  table.matrix th.lbl, table.matrix td.lbl { text-align:left; }
  table.matrix th.lineh { text-align:center; }
  table.matrix th { position:sticky; top:0; z-index:2; }
  table.matrix th.sticky, table.matrix td.sticky { position:sticky; left:0; z-index:1;
        background:var(--panel); text-align:left; }
  table.matrix th.sticky { z-index:3; }
  table.matrix .veh-stats { font-weight:400; font-size:11px; margin-left:6px; }
  /* Goldstreifen links bei aufeinanderfolgenden Wagennummern */
  table.matrix td.sticky.veh-streak { padding-left:11px; }
  table.matrix td.sticky.veh-streak::before {
    content:""; position:absolute; left:0; top:0; bottom:0; width:3px;
    background:var(--veh-streak-gold, #d4a017);
  }
  table.matrix td.sticky.veh-streak-start::before {
    background:linear-gradient(to bottom, transparent, var(--veh-streak-gold, #d4a017));
  }
  table.matrix td.sticky.veh-streak-mid::before {
    background:var(--veh-streak-gold, #d4a017);
  }
  table.matrix td.sticky.veh-streak-end::before {
    background:linear-gradient(to bottom, var(--veh-streak-gold, #d4a017), transparent);
  }
  table.matrix td.has { cursor:pointer; color:var(--accent); background:var(--tint,transparent);
        font-weight:600; }
  table.matrix tr.veh:hover td { background:var(--panel2); }
  table.matrix tr.veh:hover td.has { background:var(--tint,var(--panel2)); }
  .line-badge { display:inline-block; padding:0 6px; border-radius:4px;
        font-size:11px; font-weight:700; line-height:18px; }
  .modal-overlay { display:none; position:fixed; inset:0; background:rgba(0,0,0,.5);
        align-items:flex-start; justify-content:center; z-index:1000; padding:6vh 16px;
        backdrop-filter:blur(2px); }
  .modal-overlay.open { display:flex; }
  .modal { background:var(--panel); border:1px solid var(--border); border-radius:14px;
        max-width:560px; width:100%; max-height:80vh; overflow:auto; padding:22px 24px;
        position:relative; box-shadow:0 20px 50px rgba(0,0,0,.35); }
  .modal-x { position:absolute; top:12px; right:14px; background:none; border:none;
        font-size:22px; line-height:1; color:var(--muted); cursor:pointer; }
  .modal h3 { margin:0 0 12px; font-size:16px; }
  .ride { border-top:1px solid var(--border); padding:10px 0; }
  .ride .d { font-weight:600; }

  /* ---- Statistiken ---- */
  .stats-sec { margin:0 0 36px; scroll-margin-top:16px; }
  .stats-sec h3 { margin:0 0 6px; font-size:17px; }
  .stats-sec .hint { margin:0 0 12px; color:var(--muted); font-size:13px; }
  .stats-jump { scroll-margin-top:16px; }
  .stats-scroll { overflow:auto; max-width:100%; max-height:800px; }
  table.stats { width:100%; border-collapse:collapse; font-size:12px; }
  table.stats th, table.stats td { padding:7px 8px; border-bottom:1px solid var(--border);
        text-align:right; white-space:nowrap; vertical-align:middle; }
  table.stats th { position:sticky; top:0; background:var(--panel); z-index:1; cursor:pointer;
        font-weight:600; color:var(--muted); user-select:none; }
  table.stats th.lbl, table.stats td.lbl { text-align:left; }
  table.stats th.sort-asc::after { content:" ▲"; font-size:9px; }
  table.stats th.sort-desc::after { content:" ▼"; font-size:9px; }
  table.stats td.route { max-width:180px; overflow:hidden; text-overflow:ellipsis;
        white-space:nowrap; text-align:left; color:var(--muted); font-size:11px; }
  .barcell { display:flex; align-items:center; gap:8px; min-width:120px; }
  .barcell .n { min-width:3.5em; text-align:right; font-variant-numeric:tabular-nums; }
  .bar { flex:1; height:8px; background:var(--panel2); border-radius:4px; overflow:hidden; min-width:40px; }
  .bar > i { display:block; height:100%; background:var(--accent); border-radius:4px; }
  .heatmap { display:grid; gap:2px; width:max-content; font-size:10px; }
  .heatmap .hm-corner, .heatmap .hm-col, .heatmap .hm-row { color:var(--muted); padding:2px 4px; }
  .heatmap .hm-col { text-align:center; writing-mode:horizontal-tb; max-width:64px;
        overflow:hidden; text-overflow:ellipsis; }
  .heatmap .hm-row { text-align:right; padding-right:6px; max-width:120px;
        overflow:hidden; text-overflow:ellipsis; }
  .heatmap .hm-cell { min-width:28px; height:22px; border-radius:3px; text-align:center;
        line-height:22px; font-variant-numeric:tabular-nums; font-weight:600; }
  .stats-roles { display:flex; flex-wrap:wrap; gap:12px 18px; align-items:center;
        margin:0 0 12px; font-size:13px; color:var(--muted); }
  .stats-roles label { display:inline-flex; align-items:center; gap:6px; cursor:pointer;
        color:var(--text); user-select:none; }
  .stats-roles .sep { width:1px; height:14px; background:var(--border); }
  .stats-more { display:block; width:100%; margin-top:10px; padding:8px 12px;
        border:1px solid var(--border); border-radius:8px; background:var(--panel2);
        color:var(--fg); font:inherit; font-size:13px; cursor:pointer; }
  .stats-more:hover { border-color:var(--accent); background:var(--accent-soft); }
  .toolbar-btn { margin-left:auto; background:var(--panel2); border:1px solid var(--border);
        color:var(--fg); padding:8px 12px; border-radius:9px; cursor:pointer;
        font:inherit; font-size:13px; }
  .toolbar-btn:hover { border-color:var(--accent); background:var(--accent-soft); }
  .daily-nav { width:36px; height:34px; border:1px solid var(--border); border-radius:8px;
        background:var(--panel2); color:var(--fg); cursor:pointer; font-size:18px; line-height:1; }
  .daily-nav:hover { border-color:var(--accent); background:var(--accent-soft); }
  .daily-nav:disabled { opacity:.4; cursor:default; }
  .daily-chips { display:flex; flex-wrap:wrap; gap:6px; margin:0 0 16px; }
  .daily-chips span { background:var(--panel2); border:1px solid var(--border); border-radius:999px;
        padding:3px 10px; font-size:12px; color:var(--muted); }
  .daily-group { margin:0 0 28px; }
  .daily-group > h3 { margin:0 0 12px; font-size:17px; }
  table.stats tr.daily-implied td { background:var(--panel2); color:var(--muted); }

  /* ---- Mobile: Sidebar wird zur oberen Leiste ---- */
  .navtoggle { display:none; }
  @media (max-width:760px){
    body { flex-direction:column; }
    .sidebar { flex:none; width:100%; height:auto; z-index:50;
          flex-direction:row; align-items:center; border-right:none;
          border-bottom:1px solid var(--sidebar-border); }
    .brand { padding:12px 16px; }
    .navlist { flex-direction:row; overflow-x:auto; padding:8px; gap:4px; }
    .navlist > button, .nav-group > button { flex-direction:column; gap:3px;
          padding:8px 10px; font-size:11px; width:auto; }
    .navlist > button .ic, .nav-group > button .ic { font-size:18px; }
    .nav-group { position:relative; flex:0 0 auto; }
    .nav-group.open .nav-sub { position:absolute; top:100%; left:0; z-index:60;
          min-width:220px; max-height:60vh; overflow:auto; margin-top:4px;
          padding:8px; background:var(--sidebar); border:1px solid var(--sidebar-border);
          border-radius:10px; box-shadow:var(--shadow); }
    main { flex:1; height:auto; }
    .stats-sec, .stats-jump { scroll-margin-top:12px; }
    .sidebar-foot { flex-direction:row; align-items:center; border-top:none; padding:10px 14px; }
    .sidebar-foot .sub { display:none; }
    .tab { padding:20px 16px; }
  }
</style>
</head>
<body>
<aside class="sidebar">
  <div class="brand">
    <div class="logo"><span class="mark">🚆</span> Walita</div>
  </div>
  <nav class="navlist">
    <button data-tab="overview" class="active"><span class="ic">📊</span> Übersicht</button>
    <div class="nav-group" id="navStats">
      <button data-tab="stats"><span class="ic">📈</span> Statistiken</button>
      <div class="nav-sub">
        <a href="#stats-extra" data-jump="stats-extra">Überblick</a>
        <a href="#stats-lines" data-jump="stats-lines">Linien</a>
        <a href="#stats-loc" data-jump="stats-loc">Baureihen</a>
        <a href="#stats-veh" data-jump="stats-veh">Fahrzeuge</a>
        <div class="nav-sec">
          <a href="#stats-stations" data-jump="stats-stations" class="nav-sec-head">Stationen</a>
          <a href="#stats-stations" data-jump="stats-stations" data-station-sort="boarded" class="nav-leaf">Häufigste Einstiege</a>
          <a href="#stats-stations" data-jump="stats-stations" data-station-sort="alighted" class="nav-leaf">Häufigste Ausstiege</a>
          <a href="#stats-stations" data-jump="stats-stations" data-station-sort="through" class="nav-leaf">Häufigste Durchfahrten</a>
        </div>
        <div class="nav-sec">
          <a href="#stats-edges" data-jump="stats-edges" class="nav-sec-head">Kanten</a>
          <a href="#stats-edges-by-count" data-jump="stats-edges-by-count" class="nav-leaf">Häufigste Kanten</a>
          <a href="#stats-edges-by-days" data-jump="stats-edges-by-days" class="nav-leaf">Am längsten nicht befahren</a>
          <a href="#stats-edges-by-last" data-jump="stats-edges-by-last" class="nav-leaf">Zuletzt befahren</a>
          <a href="#stats-edges-by-first" data-jump="stats-edges-by-first" class="nav-leaf">Älteste Erstbefahrung</a>
          <a href="#stats-edges-by-first-new" data-jump="stats-edges-by-first-new" class="nav-leaf">Jüngste Erstbefahrung</a>
          <a href="#stats-edges-once" data-jump="stats-edges-once" class="nav-leaf">Vergessene Einmal-Kanten</a>
        </div>
        <div class="nav-sec">
          <a href="#stats-repeat" data-jump="stats-repeat" class="nav-sec-head">Wiederholungen</a>
          <a href="#stats-repeat-veh-edge" data-jump="stats-repeat-veh-edge" class="nav-leaf">Fahrzeug × Kante</a>
          <a href="#stats-repeat-veh-edge-line" data-jump="stats-repeat-veh-edge-line" class="nav-leaf">Fahrzeug × Kante × Linie</a>
          <a href="#stats-repeat-line-edge" data-jump="stats-repeat-line-edge" class="nav-leaf">Linie × Kante</a>
          <a href="#stats-repeat-loc-edge" data-jump="stats-repeat-loc-edge" class="nav-leaf">Baureihe × Kante</a>
          <a href="#stats-repeat-multi-veh" data-jump="stats-repeat-multi-veh" class="nav-leaf">Kanten mit mehreren Fahrzeugen</a>
        </div>
        <div class="nav-sec">
          <a href="#stats-cross" data-jump="stats-cross" class="nav-sec-head">Kreuztabellen</a>
          <a href="#stats-cross-loc-line" data-jump="stats-cross-loc-line" class="nav-leaf">Baureihe × Linie</a>
          <a href="#stats-cross-veh-line" data-jump="stats-cross-veh-line" class="nav-leaf">Fahrzeug × Linie</a>
          <a href="#stats-cross-line-month" data-jump="stats-cross-line-month" class="nav-leaf">Linie × Monat</a>
          <a href="#stats-cross-line-weekday" data-jump="stats-cross-line-weekday" class="nav-leaf">Linie × Wochentag</a>
          <a href="#stats-cross-loc-month" data-jump="stats-cross-loc-month" class="nav-leaf">Baureihe × Monat</a>
          <a href="#stats-cross-loc-weekday" data-jump="stats-cross-loc-weekday" class="nav-leaf">Baureihe × Wochentag</a>
          <a href="#stats-cross-loc-category" data-jump="stats-cross-loc-category" class="nav-leaf">Baureihe × Kategorie</a>
          <a href="#stats-cross-line-category" data-jump="stats-cross-line-category" class="nav-leaf">Linie × Kategorie</a>
          <a href="#stats-cross-line-delay" data-jump="stats-cross-line-delay" class="nav-leaf">Linie × Verspätung</a>
          <a href="#stats-cross-loc-delay" data-jump="stats-cross-loc-delay" class="nav-leaf">Baureihe × Verspätung</a>
          <a href="#stats-cross-veh-month" data-jump="stats-cross-veh-month" class="nav-leaf">Fahrzeug × Monat</a>
        </div>
      </div>
    </div>
    <button data-tab="map"><span class="ic">🗺️</span> Karte</button>
    <button data-tab="trips"><span class="ic">📋</span> Fahrten</button>
    <button data-tab="vehicles"><span class="ic">🚆</span> Fahrzeuge</button>
    <button data-tab="daily"><span class="ic">🏁</span> Tagesziele</button>
  </nav>
  <div class="sidebar-foot">
    <div class="sub" id="subtitle"></div>
    <button class="theme-toggle" id="themeToggle" title="Hell/Dunkel umschalten">
      <span id="themeIcon">🌙</span> <span id="themeLabel">Dunkel</span>
    </button>
  </div>
</aside>

<main>
<section id="overview" class="tab active">
  <div class="tab-head"><h2>Übersicht</h2><p>Kennzahlen und meistbefahrene Segmente.</p></div>
  <div class="cards" id="kpis"></div>
  <div class="panel" id="segpanel" style="margin-top:24px">
    <h3>Meistbefahrene Segmente</h3>
    <p class="hint" style="margin:0 0 10px;color:var(--muted);font-size:13px">Zunächst 30 Einträge.</p>
    <table id="segtable"><thead><tr><th>Von</th><th>Nach</th><th>Anzahl</th></tr></thead>
      <tbody></tbody></table>
  </div>
</section>

<section id="stats" class="tab">
  <div class="tab-head"><h2>Statistiken</h2>
    <p>Linien, Baureihen, Fahrzeuge und Stationen – Rankings, Unique-Kombis und Kreuztabellen.</p></div>
  <div id="stats-extra" class="stats-sec"></div>
  <div id="stats-lines" class="stats-sec"></div>
  <div id="stats-loc" class="stats-sec"></div>
  <div id="stats-veh" class="stats-sec"></div>
  <div id="stats-stations" class="stats-sec"></div>
  <div id="stats-edges" class="stats-sec"></div>
  <div id="stats-repeat" class="stats-sec"></div>
  <div id="stats-cross" class="stats-sec"></div>
</section>

<section id="map" class="tab">
  <div class="tab-head"><h2>Karte</h2><p>Gerichtete Befahrungs-Heatmap – filterbar nach Fahrt-Attributen.</p></div>
  <div class="toolbar">
    <label class="ctl">Linie <select id="mapLine"></select></label>
    <label class="ctl">Baureihe <select id="mapLoc"></select></label>
    <label class="ctl">Fahrzeug <select id="mapVehicle"></select></label>
    <label class="ctl">Kategorie <select id="mapCat"></select></label>
    <label class="ctl">Operator <select id="mapOperator"></select></label>
    <label class="ctl">Jahr <select id="mapYear"></select></label>
    <label class="ctl">Datum <input type="date" id="mapDate"></label>
    <span class="muted" id="mapCount"></span>
  </div>
  <div id="mapview"></div>
</section>

<section id="trips" class="tab">
  <div class="tab-head"><h2>Fahrten</h2><p>Alle Check-ins – durchsuchbar und sortierbar.</p></div>
  <div class="toolbar">
    <input type="search" id="filter" placeholder="Filtern (Linie, Operator, Station, Datum …)">
    <label class="ctl">Von <input type="date" id="tripDateFrom"></label>
    <label class="ctl">Bis <input type="date" id="tripDateTo"></label>
    <span class="ctl ctl-checks">
      <label><input type="checkbox" id="tripShowDelay"> Verspätung</label>
      <label><input type="checkbox" id="tripShowRoute"> Laufweg</label>
    </span>
    <label class="ctl">Zeiten
      <select id="tripTimeMode">
        <option value="planned">Plan</option>
        <option value="real">Ist</option>
      </select>
    </label>
    <span class="muted" id="tripcount"></span>
    <button type="button" class="toolbar-btn" id="tripsCopy">Als TSV kopieren</button>
  </div>
  <div class="panel">
    <table id="tripsTable">
      <thead><tr>
        <th data-k="date" class="sorted" title="Sortieren">Datum</th>
        <th data-k="line" title="Sortieren">Linie</th>
        <th data-k="operator" title="Sortieren">Operator</th>
        <th data-k="locClass" title="Sortieren">Baureihe</th>
        <th data-k="vehicles" title="Sortieren">Wagen</th>
        <th data-k="from" title="Sortieren">Von</th>
        <th data-k="to" title="Sortieren">Nach</th>
        <th data-k="depTime" title="Sortieren">Ab</th>
        <th data-k="arrTime" title="Sortieren">An</th>
        <th data-k="viaStops" title="Sortieren">Zwischenhalte</th>
        <th data-k="route" title="Sortieren">Laufweg</th>
        <th data-k="distanceKm" title="Sortieren">km</th>
        <th data-k="durationMin" title="Sortieren">Min</th>
        <th data-k="delay" title="Sortieren">Versp.</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</section>

<section id="vehicles" class="tab">
  <div class="tab-head"><h2>Fahrzeuge</h2><p>Getaggte Wagennummern je Baureihe/Kategorie und Linie.</p></div>
  <div class="toolbar">
    <label class="ctl">Tabellen je
      <select id="vehGroup">
        <option value="locClass">Baureihe</option>
        <option value="category">Produktkategorie</option>
      </select>
    </label>
    <label class="ctl">Operator <select id="vehOperator"></select></label>
    <label class="ctl">Kategorie <select id="vehCategory"></select></label>
    <label class="ctl">Jahr <select id="vehYear"></select></label>
    <input type="search" id="vehFilter" placeholder="Suche (Wagennr., Linie …)">
    <span class="ctl ctl-checks">Anzeige
      <label><input type="checkbox" name="vehShow" value="date" checked> Datum</label>
      <label><input type="checkbox" name="vehShow" value="count"> Häufigkeit</label>
      <label><input type="checkbox" name="vehShow" value="km"> km</label>
      <label><input type="checkbox" name="vehShow" value="segments"> Segmente</label>
    </span>
    <span class="muted" id="vehCount"></span>
  </div>
  <div id="vehMatrices"></div>
</section>

<section id="daily" class="tab">
  <div class="tab-head"><h2>Tagesziele</h2>
    <p>Was an einem Tag zum ersten Mal vorkam – und was an dem Tag wiederholt befahren wurde.</p></div>
  <div class="toolbar">
    <button type="button" class="daily-nav" id="dailyPrev" title="Vorheriger Tag mit Fahrten">‹</button>
    <label class="ctl">Datum <input type="date" id="dailyDate"></label>
    <button type="button" class="daily-nav" id="dailyNext" title="Nächster Tag mit Fahrten">›</button>
    <span class="muted" id="dailySummary"></span>
  </div>
  <div id="dailyBody"></div>
</section>
</main>

<div id="vehModal" class="modal-overlay">
  <div class="modal">
    <button class="modal-x" id="vehModalClose" aria-label="Schließen">×</button>
    <div id="vehModalBody"></div>
  </div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
  integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
const DATA = __DATA__;

// ---------- Helpers ----------
function fmtDate(iso){ if(!iso) return ""; const d=new Date(iso);
  return isNaN(d)? iso : d.toLocaleDateString("de-DE",{year:"numeric",month:"2-digit",day:"2-digit"}); }
function fmtTime(iso){ if(!iso) return "—"; const d=new Date(iso);
  return isNaN(d)? "—" : d.toLocaleTimeString("de-DE",{hour:"2-digit",minute:"2-digit"}); }
function fmtDuration(min){ const h=Math.floor(min/60), m=min%60;
  return h? h+" h "+m+" min" : m+" min"; }
function esc(s){ return (s==null?"":String(s)).replace(/[&<>"']/g,c=>(
  {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

function isoToday(){
  const d=new Date();
  const m=String(d.getMonth()+1).padStart(2,"0");
  const day=String(d.getDate()).padStart(2,"0");
  return d.getFullYear()+"-"+m+"-"+day;
}
function datePickerMax(last){
  const t=isoToday();
  return (last && last>t)?last:t;
}
function bindDateInput(el, first, last){
  if(!el) return;
  if(first) el.min=first;
  el.max=datePickerMax(last);
}

// Interner Linien-Schlüssel = Name + \\x1f + Operator; Anzeige nur der Name.
const LINE_SEP="\x1f";
const lineName=k=>{
  if(k==null||k==="") return "";
  const s=String(k), i=s.indexOf(LINE_SEP);
  return i<0 ? s : s.slice(0,i);
};

// Produktkategorie -> Label (global, von Karte und Fahrzeugen genutzt).
const CAT_LABEL={suburban:"S-Bahn",regional:"Regional",regionalExp:"Regional-Express",
  nationalExpress:"Fernverkehr",national:"Fernverkehr",tram:"Tram",subway:"U-Bahn",
  bus:"Bus",ferry:"Fähre"};
const catLabel=c=>CAT_LABEL[c]||c||"Unbekannt";

// ---------- Theme (hell/dunkel) ----------
(function(){
  const root=document.documentElement;
  const btn=document.getElementById("themeToggle");
  const icon=document.getElementById("themeIcon");
  const label=document.getElementById("themeLabel");
  let stored=null;
  try { stored=localStorage.getItem("trwl-theme"); } catch(e){}
  const prefersDark=window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  let theme = stored || (prefersDark ? "dark" : "light");
  function apply(){
    root.dataset.theme = theme;
    // Der Button zeigt das Ziel des nächsten Klicks an.
    if(theme==="dark"){ icon.textContent="☀️"; label.textContent="Hell"; }
    else { icon.textContent="🌙"; label.textContent="Dunkel"; }
  }
  apply();
  btn.onclick=()=>{
    theme = theme==="dark" ? "light" : "dark";
    try { localStorage.setItem("trwl-theme", theme); } catch(e){}
    apply();
  };
})();

// ---------- Tabs ----------
function showTab(tabId){
  document.querySelectorAll(".navlist [data-tab]").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".tab").forEach(x=>x.classList.remove("active"));
  const btn=document.querySelector(`.navlist [data-tab="${tabId}"]`);
  if(btn) btn.classList.add("active");
  const tab=document.getElementById(tabId);
  if(tab) tab.classList.add("active");
  const statsGroup=document.getElementById("navStats");
  if(statsGroup) statsGroup.classList.toggle("open", tabId==="stats");
  if(tabId==="map" && map){ setTimeout(()=>{
    map.invalidateSize();
    if(!mapFitted && mapBounds.length){ map.fitBounds(mapBounds,{padding:[30,30]}); mapFitted=true; }
  },80); }
}
document.querySelectorAll(".navlist [data-tab]").forEach(b=>b.onclick=()=>{
  showTab(b.dataset.tab);
  if(b.dataset.tab==="stats"){
    const head=document.querySelector("#stats .tab-head");
    if(head) head.scrollIntoView({behavior:"smooth",block:"start"});
  }
});
document.querySelectorAll(".nav-sub [data-jump]").forEach(a=>{
  a.addEventListener("click",e=>{
    e.preventDefault();
    const id=a.getAttribute("data-jump");
    showTab("stats");
    const target=document.getElementById(id);
    if(target) setTimeout(()=>target.scrollIntoView({behavior:"smooth",block:"start"}), 30);
  });
});

// ---------- Overview ----------
(function(){
  const k=DATA.kpis;
  document.getElementById("subtitle").textContent =
    (k.first&&k.last)? (k.first+" – "+k.last) : "";
  const cards=[
    ["Check-ins", k.count.toLocaleString("de-DE")],
    ["Distanz", k.distanceKm.toLocaleString("de-DE")+" km"],
    ["Reisezeit", fmtDuration(k.durationMin)],
    ["Punkte", k.points.toLocaleString("de-DE")],
    ["Stationen", k.stations.toLocaleString("de-DE")],
    ["Linien", k.lines.toLocaleString("de-DE")],
  ];
  document.getElementById("kpis").innerHTML = cards.map(c=>
    `<div class="card"><div class="v">${esc(c[1])}</div><div class="l">${esc(c[0])}</div></div>`).join("");
  (function(){
    const segs=DATA.segments||[];
    const tbody=document.querySelector("#segtable tbody");
    const panel=document.getElementById("segpanel");
    let showAll=segs.length<=30;
    function paint(){
      const data=showAll?segs:segs.slice(0,30);
      const remaining=segs.length-data.length;
      tbody.innerHTML=data.map(s=>
        `<tr><td>${esc(s.from)}</td><td>${esc(s.to)}</td><td>${s.count}</td></tr>`).join("");
      let btn=panel.querySelector(".stats-more");
      if(remaining>0){
        if(!btn){ btn=document.createElement("button"); btn.type="button"; btn.className="stats-more"; panel.appendChild(btn); }
        btn.textContent=`Mehr laden (${remaining.toLocaleString("de-DE")} weitere, ${segs.length.toLocaleString("de-DE")} gesamt)`;
        btn.onclick=()=>{ showAll=true; paint(); };
      } else if(btn){ btn.remove(); }
    }
    paint();
  })();
})();

// ---------- Statistiken (Linie / Baureihe / Fahrzeug / Station) ----------
(function(){
  const S=DATA.stats;
  if(!S){
    document.getElementById("stats-extra").innerHTML=
      "<p class='hint'>Keine Statistik-Daten vorhanden.</p>";
    return;
  }
  const WD=["Mo","Di","Mi","Do","Fr","Sa","So"];
  const LC=DATA.lineColors||{};
  const turbo=(t)=>{
    t=Math.max(0,Math.min(1,t));
    const stops=[[0.0,[48,18,59]],[0.1,[68,57,144]],[0.2,[65,117,199]],
      [0.3,[46,167,194]],[0.4,[51,200,142]],[0.5,[126,214,63]],
      [0.6,[210,214,52]],[0.7,[249,186,52]],[0.8,[245,116,36]],[1.0,[122,4,3]]];
    let a=stops[0], b=stops[stops.length-1];
    for(let i=0;i<stops.length-1;i++) if(t>=stops[i][0]&&t<=stops[i+1][0]){a=stops[i];b=stops[i+1];break;}
    const u=(t-a[0])/((b[0]-a[0])||1);
    const rgb=a[1].map((v,i)=>Math.round(v+(b[1][i]-v)*u));
    // Relative Luminanz: dunkler Hintergrund → weißer Text.
    const lum=0.2126*rgb[0]+0.7152*rgb[1]+0.0722*rgb[2];
    return {bg:`rgb(${rgb[0]},${rgb[1]},${rgb[2]})`, fg: lum<140?"#ffffff":"#111a27"};
  };
  function lineBadge(key){
    const name=lineName(key);
    const c=LC[key];
    const st=c?` style="background:${c[0]};color:${c[1]}"`:"";
    return `<span class="line-badge"${st}>${esc(name)}</span>`;
  }
  function fmtNum(v){
    if(v==null||v==="") return "—";
    return Number(v).toLocaleString("de-DE");
  }
  function fmtPct(v){ return v==null?"—":fmtNum(v)+" %"; }
  function tripLabel(t){
    if(!t) return "—";
    return `${esc(t.line||"")} · ${esc(t.from||"")} → ${esc(t.to||"")} · ${fmtNum(t.km)} km`+
      (t.date?` · ${esc(t.date)}`:"");
  }

  // KPI-Überblick
  const ex=S.extra||{};
  const cards=[
    ["Linien", fmtNum(ex.lines)],
    ["Baureihen", fmtNum(ex.locClasses)],
    ["Fahrzeuge", fmtNum(ex.vehicles)],
    ["Einstiegs-St.", fmtNum(ex.stationsBoarded)],
    ["Ausstiegs-St.", fmtNum(ex.stationsAlighted)],
    ["Durchfahrt-St.", fmtNum(ex.stationsThrough)],
    ["Kanten", fmtNum(ex.edges)],
    ["Kanten >1×", fmtNum(ex.edgesRepeat)],
    ["Fz×Kante >1", fmtNum(ex.vehEdgeRepeat)],
    ["Tag Baureihe", fmtPct(ex.tagLocPct)],
    ["Tag Fahrzeug", fmtPct(ex.tagVehPct)],
    ["Unique Linie×Fz", fmtNum(ex.uniqueLineVehicle)],
    ["Unique Linie×BR", fmtNum(ex.uniqueLineLocClass)],
    ["Unique Routen", fmtNum(ex.uniqueRoutes)],
  ];
  let extraHtml=`<h3>Überblick</h3>
    <div class="cards">${cards.map(c=>
      `<div class="card"><div class="v">${c[1]}</div><div class="l">${esc(c[0])}</div></div>`
    ).join("")}</div>
    <div class="panel" style="margin-top:16px">
      <div><strong>Längste Strecke:</strong> ${tripLabel(ex.maxTrip)}</div>
      <div style="margin-top:6px"><strong>Kürzeste Strecke:</strong> ${tripLabel(ex.minTrip)}</div>
      ${ex.topLine?`<div style="margin-top:6px"><strong>Top-Linie (km):</strong> ${lineBadge(ex.topLine.key)} · ${fmtNum(ex.topLine.km)} km</div>`:""}
      ${ex.topVehicle?`<div style="margin-top:6px"><strong>Top-Fahrzeug (km):</strong> ${esc(ex.topVehicle.key)} · ${fmtNum(ex.topVehicle.km)} km</div>`:""}
    </div>`;
  document.getElementById("stats-extra").innerHTML=extraHtml;

  function renderBarTable(containerId, title, hint, rows, cols, opts){
    opts=opts||{};
    const el=document.getElementById(containerId);
    const initialLimit=opts.initialLimit||0;
    if(!rows||!rows.length){
      el.innerHTML=`<h3>${esc(title)}</h3><p class="hint">${esc(hint||"Keine Daten.")}</p>`;
      return;
    }
    let sortKey=opts.defaultSort||"distanceKm";
    let sortAsc=!!opts.defaultAsc;
    let showAll=!initialLimit || rows.length<=initialLimit;
    function sorted(){
      return rows.slice().sort((a,b)=>{
        let av=a[sortKey], bv=b[sortKey];
        if(av==null) av=sortAsc?Infinity:-Infinity;
        if(bv==null) bv=sortAsc?Infinity:-Infinity;
        if(typeof av==="string"||typeof bv==="string"){
          const c=String(av).localeCompare(String(bv),"de",{numeric:true});
          return sortAsc?c:-c;
        }
        return sortAsc?av-bv:bv-av;
      });
    }
    function paint(){
      const all=sorted();
      const data=(!showAll && initialLimit)?all.slice(0, initialLimit):all;
      const remaining=all.length-data.length;
      const maxBar=Math.max(1,...all.map(r=>+r.distanceKm||0));
      const head=cols.map(c=>{
        const cls=c.key===sortKey?(sortAsc?"sort-asc":"sort-desc"):"";
        return `<th class="${c.lbl?"lbl ":""}${cls}" data-k="${c.key}">${esc(c.label)}</th>`;
      }).join("");
      const body=data.map(r=>{
        return "<tr>"+cols.map(c=>{
          if(c.key==="key" && opts.lineKeys){
            return `<td class="lbl">${lineBadge(r.key)}</td>`;
          }
          if(c.key==="_bar"){
            const pct=Math.round(100*(+r.distanceKm||0)/maxBar);
            return `<td><div class="barcell"><span class="n">${fmtNum(r.distanceKm)}</span>`+
              `<div class="bar"><i style="width:${pct}%"></i></div></div></td>`;
          }
          if(c.route) return `<td class="route" title="${esc(r[c.key]||"")}">${esc(r[c.key]||"—")}</td>`;
          if(c.pct) return `<td>${fmtPct(r[c.key])}</td>`;
          if(c.date) return `<td>${esc(r[c.key]||"—")}</td>`;
          if(c.lbl) return `<td class="lbl">${esc(r[c.key]==null||r[c.key]===""?"—":r[c.key])}</td>`;
          return `<td>${fmtNum(r[c.key])}</td>`;
        }).join("")+"</tr>";
      }).join("");
      const more=remaining>0
        ? `<button type="button" class="stats-more">Mehr laden (${fmtNum(remaining)} weitere, ${fmtNum(all.length)} gesamt)</button>`
        : "";
      el.innerHTML=`<h3>${esc(title)}</h3>
        <p class="hint">${esc(hint||"")}</p>
        <div class="panel stats-scroll"><table class="stats"><thead><tr>${head}</tr></thead>
        <tbody>${body}</tbody></table>${more}</div>`;
      el.querySelectorAll("th[data-k]").forEach(th=>{
        th.onclick=()=>{
          const k=th.dataset.k;
          if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=!!cols.find(c=>c.key===k&&c.date); }
          paint();
        };
      });
      const moreBtn=el.querySelector(".stats-more");
      if(moreBtn) moreBtn.onclick=()=>{ showAll=true; paint(); };
    }
    paint();
  }

  const baseCols=[
    {key:"_bar", label:"km"},
    {key:"count", label:"Fahrten"},
    {key:"avgDistanceKm", label:"Ø km"},
    {key:"durationMin", label:"Min"},
    {key:"points", label:"Pkt"},
    {key:"first", label:"zuerst", date:true},
    {key:"last", label:"zuletzt", date:true},
    {key:"minDistanceKm", label:"min km"},
    {key:"minRoute", label:"kürzeste", route:true},
    {key:"maxDistanceKm", label:"max km"},
    {key:"maxRoute", label:"längste", route:true},
    {key:"uniqueRoutes", label:"Routen"},
    {key:"uniqueWeekdays", label:"WTage"},
    {key:"uniqueMonths", label:"Monate"},
    {key:"avgDelay", label:"Ø Versp."},
    {key:"onTimePct", label:"pünktig %", pct:true},
  ];

  renderBarTable("stats-lines", "Linien",
    "Nach Kilometern – Klick auf Spaltenkopf sortiert. Zunächst 50 Einträge.",
    S.byLine, [{key:"key", label:"Linie", lbl:true}, ...baseCols,
      {key:"uniqueVehicles", label:"Fz"},
      {key:"uniqueLocClasses", label:"BR"}],
    {lineKeys:true, initialLimit:50});

  renderBarTable("stats-loc", "Baureihen",
    S.byLocClass&&S.byLocClass.length
      ? "Alle getaggten Baureihen."
      : "Keine Baureihen-Tags (trwl:locomotive_class) in den Daten.",
    S.byLocClass, [{key:"key", label:"Baureihe", lbl:true}, ...baseCols,
      {key:"uniqueVehicles", label:"Fz"},
      {key:"uniqueLines", label:"Linien"}]);

  renderBarTable("stats-veh", "Fahrzeuge",
    S.byVehicle&&S.byVehicle.length
      ? "Wagennummern nach Kilometern. Zunächst 50 Einträge."
      : "Keine Fahrzeug-Tags (trwl:vehicle_number) in den Daten.",
    S.byVehicle, [{key:"key", label:"Wagen", lbl:true},
      {key:"locClass", label:"BR", lbl:true}, ...baseCols,
      {key:"uniqueLines", label:"Linien"}],
    {initialLimit:50});

  // Stationen: Ein / Aus / Durch mit kombinierbaren Rollenfiltern
  (function renderStations(){
    const el=document.getElementById("stats-stations");
    const rows=S.byStation||[];
    if(!rows.length){
      el.innerHTML=`<h3>Stationen</h3>
        <p class="hint">Keine Stations-Daten (keine befahrbaren Stopovers).</p>`;
      return;
    }
    let useBoarded=true, useAlighted=true, useThrough=true, andMode=false;
    let sortKey="total", sortAsc=false;
    const cols=[
      {key:"key", label:"Station", lbl:true},
      {key:"boarded", label:"Eingestiegen"},
      {key:"alighted", label:"Ausgestiegen"},
      {key:"through", label:"Durchfahren"},
      {key:"total", label:"Summe"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
      {key:"uniqueLines", label:"Linien"},
    ];
    function roleSum(r){
      return (useBoarded?r.boarded:0)+(useAlighted?r.alighted:0)+(useThrough?r.through:0);
    }
    function matches(r){
      const parts=[];
      if(useBoarded) parts.push(r.boarded>0);
      if(useAlighted) parts.push(r.alighted>0);
      if(useThrough) parts.push(r.through>0);
      if(!parts.length) return false;
      return andMode?parts.every(Boolean):parts.some(Boolean);
    }
    function cellVal(r, key){
      if(key==="total") return roleSum(r);
      return r[key];
    }
    function paint(){
      const data=rows.filter(matches).map(r=>({...r, total:roleSum(r)})).sort((a,b)=>{
        let av=cellVal(a, sortKey), bv=cellVal(b, sortKey);
        if(av==null) av=sortAsc?Infinity:-Infinity;
        if(bv==null) bv=sortAsc?Infinity:-Infinity;
        if(typeof av==="string"||typeof bv==="string"){
          const c=String(av).localeCompare(String(bv),"de",{numeric:true});
          return sortAsc?c:-c;
        }
        return sortAsc?av-bv:bv-av;
      });
      const head=cols.map(c=>{
        const cls=c.key===sortKey?(sortAsc?"sort-asc":"sort-desc"):"";
        return `<th class="${c.lbl?"lbl ":""}${cls}" data-k="${c.key}">${esc(c.label)}</th>`;
      }).join("");
      const body=data.map(r=>"<tr>"+cols.map(c=>{
        if(c.date) return `<td>${esc(cellVal(r,c.key)||"—")}</td>`;
        if(c.lbl){
          const v=cellVal(r,c.key);
          return `<td class="lbl">${esc(v==null||v===""?"—":v)}</td>`;
        }
        return `<td>${fmtNum(cellVal(r,c.key))}</td>`;
      }).join("")+"</tr>").join("");
      el.innerHTML=`<h3>Stationen</h3>
        <p class="hint">Pro Fahrt: Einstieg am ersten Halt, Ausstieg am letzten, Durchfahrt an Zwischenhalten.
          Summe und Filter beziehen sich auf die aktivierten Rollen.</p>
        <div class="stats-roles">
          <label><input type="checkbox" id="stRoleB" ${useBoarded?"checked":""}> Eingestiegen</label>
          <label><input type="checkbox" id="stRoleA" ${useAlighted?"checked":""}> Ausgestiegen</label>
          <label><input type="checkbox" id="stRoleT" ${useThrough?"checked":""}> Durchfahren</label>
          <span class="sep"></span>
          <label><input type="checkbox" id="stRoleAnd" ${andMode?"checked":""}> nur Kombination</label>
          <span style="margin-left:auto">${fmtNum(data.length)} Stationen</span>
        </div>
        <div class="panel stats-scroll"><table class="stats"><thead><tr>${head}</tr></thead>
        <tbody>${body.length?body:`<tr><td colspan="${cols.length}" class="lbl">Keine Stationen für diese Filter.</td></tr>`}</tbody></table></div>`;
      el.querySelector("#stRoleB").onchange=e=>{ useBoarded=e.target.checked; paint(); };
      el.querySelector("#stRoleA").onchange=e=>{ useAlighted=e.target.checked; paint(); };
      el.querySelector("#stRoleT").onchange=e=>{ useThrough=e.target.checked; paint(); };
      el.querySelector("#stRoleAnd").onchange=e=>{ andMode=e.target.checked; paint(); };
      el.querySelectorAll("th[data-k]").forEach(th=>{
        th.onclick=()=>{
          const k=th.dataset.k;
          if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=!!cols.find(c=>c.key===k&&c.date); }
          paint();
        };
      });
    }
    paint();
    document.querySelectorAll("[data-station-sort]").forEach(a=>{
      a.addEventListener("click",()=>{
        const k=a.getAttribute("data-station-sort");
        if(!k) return;
        sortKey=k; sortAsc=false;
        if(k==="boarded") useBoarded=true;
        if(k==="alighted") useAlighted=true;
        if(k==="through") useThrough=true;
        paint();
      });
    });
  })();

  function renderComboTable(mountEl, title, hint, rows, cols, opts){
    opts=opts||{};
    const jumpId=opts.id||"";
    const idAttr=jumpId?` id="${esc(jumpId)}"`:"";
    const initialLimit=opts.initialLimit||0;
    if(!rows||!rows.length){
      const empty=`<div class="panel stats-jump" style="margin-bottom:16px"${idAttr}><h3 style="margin:0 0 6px;font-size:14px">${esc(title)}</h3>
        <p class="hint" style="margin:0">${esc(hint||"Keine Einträge.")}</p></div>`;
      if(mountEl){ mountEl.innerHTML=empty; return ""; }
      return empty;
    }
    const wrap=document.createElement("div");
    wrap.className="panel stats-scroll stats-jump";
    wrap.style.marginBottom="16px";
    if(jumpId) wrap.id=jumpId;
    let sortKey=opts.defaultSort||"count";
    let sortAsc=!!opts.defaultAsc;
    let showAll=!initialLimit || rows.length<=initialLimit;
    function daysSince(dateStr){
      if(!dateStr) return null;
      const parts=String(dateStr).slice(0,10).split("-").map(Number);
      if(parts.length<3||parts.some(n=>!Number.isFinite(n))) return null;
      const d=new Date(parts[0], parts[1]-1, parts[2]);
      const now=new Date();
      const today=new Date(now.getFullYear(), now.getMonth(), now.getDate());
      return Math.round((today-d)/86400000);
    }
    function cellVal(row, key){
      if(key==="daysSinceLast") return daysSince(row.last);
      if(key==="daysSinceFirst") return daysSince(row.first);
      return row[key];
    }
    function paint(){
      const sorted=rows.slice().sort((a,b)=>{
        let av=cellVal(a, sortKey), bv=cellVal(b, sortKey);
        if(av==null) av=sortAsc?Infinity:-Infinity;
        if(bv==null) bv=sortAsc?Infinity:-Infinity;
        if(typeof av==="string"||typeof bv==="string"){
          const c=String(av).localeCompare(String(bv),"de",{numeric:true});
          return sortAsc?c:-c;
        }
        return sortAsc?av-bv:bv-av;
      });
      const data=(!showAll && initialLimit)?sorted.slice(0, initialLimit):sorted;
      const remaining=sorted.length-data.length;
      const head=cols.map(c=>{
        const cls=c.key===sortKey?(sortAsc?"sort-asc":"sort-desc"):"";
        return `<th class="${c.lbl||c.line?"lbl ":""}${cls}" data-k="${c.key}">${esc(c.label)}</th>`;
      }).join("");
      const body=data.map(r=>"<tr>"+cols.map(c=>{
        if(c.line) return `<td class="lbl">${lineBadge(cellVal(r,c.key))}</td>`;
        if(c.date) return `<td>${esc(cellVal(r,c.key)||"—")}</td>`;
        if(c.lbl){
          const v=cellVal(r,c.key);
          return `<td class="lbl">${esc(v==null||v===""?"—":v)}</td>`;
        }
        return `<td>${fmtNum(cellVal(r,c.key))}</td>`;
      }).join("")+"</tr>").join("");
      const more=remaining>0
        ? `<button type="button" class="stats-more">Mehr laden (${fmtNum(remaining)} weitere, ${fmtNum(sorted.length)} gesamt)</button>`
        : "";
      wrap.innerHTML=`<h3 style="margin:0 0 8px;font-size:14px">${esc(title)}</h3>
        <p class="hint" style="margin:0 0 10px">${esc(hint||"")}</p>
        <table class="stats"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>${more}`;
      wrap.querySelectorAll("th[data-k]").forEach(th=>{
        th.onclick=()=>{
          const k=th.dataset.k;
          if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=!!cols.find(c=>c.key===k&&c.date); }
          paint();
        };
      });
      const moreBtn=wrap.querySelector(".stats-more");
      if(moreBtn) moreBtn.onclick=()=>{ showAll=true; paint(); };
    }
    paint();
    if(mountEl){ mountEl.appendChild(wrap); return ""; }
    return wrap.outerHTML;
  }

  const edgeCols=[
    {key:"from", label:"Von", lbl:true},
    {key:"to", label:"Nach", lbl:true},
    {key:"count", label:"×"},
    {key:"first", label:"zuerst", date:true},
    {key:"last", label:"zuletzt", date:true},
    {key:"daysSinceLast", label:"Tage her"},
    {key:"uniqueVehicles", label:"Fz"},
    {key:"uniqueLines", label:"Linien"},
    {key:"uniqueLocClasses", label:"BR"},
  ];
  // Erstbefahrung: „Tage her“ = Tage seit first, nicht seit last.
  const edgeColsFirst=edgeCols.map(c=>
    c.key==="daysSinceLast"?{key:"daysSinceFirst", label:"Tage her"}:c
  );
  const edgesEl=document.getElementById("stats-edges");
  edgesEl.innerHTML=`<h3>Kanten</h3>
    <p class="hint">Gerichtete Zwischenhalt-Segmente. „Tage her“ relativ zum heutigen Datum beim Öffnen der Seite (bei Erstbefahrung: seit zuerst).</p>`;
  const edgeBlocks=[
    ["edgesByCount", "Häufigste Kanten", "Sortiert nach Befahrungen. Zunächst 40 Einträge.", "count", false, edgeCols, "stats-edges-by-count"],
    ["edgesByDaysSince", "Am längsten nicht befahren", "Sortiert nach Tagen seit letzter Befahrung. Zunächst 40 Einträge.", "daysSinceLast", false, edgeCols, "stats-edges-by-days"],
    ["edgesByLast", "Zuletzt befahren", "Sortiert nach Datum zuletzt. Zunächst 40 Einträge.", "last", false, edgeCols, "stats-edges-by-last"],
    ["edgesByFirst", "Älteste Erstbefahrung", "Sortiert nach Datum zuerst. „Tage her“ seit Erstbefahrung. Zunächst 40 Einträge.", "first", true, edgeColsFirst, "stats-edges-by-first"],
    ["edgesByFirstNew", "Jüngste Erstbefahrung", "Sortiert nach Datum zuerst (neueste zuerst). „Tage her“ seit Erstbefahrung. Zunächst 40 Einträge.", "first", false, edgeColsFirst, "stats-edges-by-first-new"],
    ["edgesOnce", "Vergessene Einmal-Kanten", "Nur einmal befahren, sortiert nach Tagen her. Zunächst 40 Einträge.", "daysSinceLast", false, edgeCols, "stats-edges-once"],
  ];
  edgeBlocks.forEach(([key,title,hint,sort,asc,cols,id])=>{
    const holder=document.createElement("div");
    edgesEl.appendChild(holder);
    renderComboTable(holder, title, hint, S[key], cols,
      {defaultSort:sort, defaultAsc:asc, id:id, initialLimit:40});
  });

  const repeatEl=document.getElementById("stats-repeat");
  repeatEl.innerHTML=`<h3>Wiederholungen</h3>
    <p class="hint">Nur Kombinationen mit mehr als einer Befahrung (count &gt; 1).</p>`;
  function appendCombo(title, hint, rows, cols, sortOpts){
    const holder=document.createElement("div");
    repeatEl.appendChild(holder);
    const opts=Object.assign({initialLimit:100}, sortOpts||{});
    renderComboTable(holder, title, hint, rows, cols, opts);
  }
  appendCombo("Fahrzeug × Kante", "Dieselbe Wagennummer auf derselben Kante mehrfach. Zunächst 100 Einträge.",
    S.vehEdgeGt1, [
      {key:"vehicle", label:"Wagen", lbl:true},
      {key:"from", label:"Von", lbl:true},
      {key:"to", label:"Nach", lbl:true},
      {key:"count", label:"×"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
      {key:"lines", label:"Linien"},
    ], {id:"stats-repeat-veh-edge"});
  appendCombo("Fahrzeug × Kante × Linie", "Dieselbe Kombi Wagen + Segment + Linie mehrfach. Zunächst 100 Einträge.",
    S.vehEdgeLineGt1, [
      {key:"vehicle", label:"Wagen", lbl:true},
      {key:"from", label:"Von", lbl:true},
      {key:"to", label:"Nach", lbl:true},
      {key:"line", label:"Linie", line:true},
      {key:"count", label:"×"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
    ], {id:"stats-repeat-veh-edge-line"});
  appendCombo("Linie × Kante", "Linie wiederholt auf demselben Segment. Zunächst 100 Einträge.",
    S.lineEdgeGt1, [
      {key:"line", label:"Linie", line:true},
      {key:"from", label:"Von", lbl:true},
      {key:"to", label:"Nach", lbl:true},
      {key:"count", label:"×"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
    ], {id:"stats-repeat-line-edge"});
  appendCombo("Baureihe × Kante", "Baureihe wiederholt auf demselben Segment. Zunächst 100 Einträge.",
    S.locEdgeGt1, [
      {key:"locClass", label:"Baureihe", lbl:true},
      {key:"from", label:"Von", lbl:true},
      {key:"to", label:"Nach", lbl:true},
      {key:"count", label:"×"},
      {key:"first", label:"zuerst", date:true},
      {key:"last", label:"zuletzt", date:true},
    ], {defaultSort:"count", id:"stats-repeat-loc-edge"});
  appendCombo("Kanten mit mehreren Fahrzeugen", "Segmente mit mehr als einer Wagennummer. Zunächst 100 Einträge.",
    S.multiVehicleEdges, edgeCols, {defaultSort:"uniqueVehicles", id:"stats-repeat-multi-veh"});

  function mountHeatmap(parent, cross, title, rowLabelFn, colLabelFn, jumpId, opts){
    opts=opts||{};
    const initialLimit=opts.initialLimit||0;
    const wrap=document.createElement("div");
    wrap.className="panel stats-scroll stats-jump";
    wrap.style.marginBottom="16px";
    if(jumpId) wrap.id=jumpId;
    if(!cross||!cross.rows||!cross.rows.length||!cross.cols||!cross.cols.length){
      wrap.innerHTML=`<h3 style="margin:0 0 8px;font-size:14px">${esc(title)}</h3>
        <p class="hint" style="margin:0">Keine Daten.</p>`;
      parent.appendChild(wrap);
      return;
    }
    const allRows=cross.rows, cols=cross.cols, counts=cross.counts, kms=cross.kms||[];
    let max=1;
    counts.forEach(r=>r.forEach(v=>{ if(v>max) max=v; }));
    let showAll=!initialLimit || allRows.length<=initialLimit;
    function paint(){
      const nShow=showAll?allRows.length:Math.min(initialLimit, allRows.length);
      const remaining=allRows.length-nShow;
      const colsN=cols.length;
      let html=`<h3 style="margin:0 0 10px;font-size:14px">${esc(title)}</h3>`;
      if(remaining>0){
        html+=`<p class="hint" style="margin:0 0 10px">Zunächst ${fmtNum(initialLimit)} Zeilen.</p>`;
      }
      html+=`<div class="heatmap" style="grid-template-columns:max-content repeat(${colsN},minmax(28px,auto))">`;
      html+=`<div class="hm-corner"></div>`;
      cols.forEach(c=>{
        html+=`<div class="hm-col" title="${esc(colLabelFn(c))}">${esc(colLabelFn(c))}</div>`;
      });
      for(let ri=0; ri<nShow; ri++){
        const r=allRows[ri];
        html+=`<div class="hm-row" title="${esc(rowLabelFn(r))}">${esc(rowLabelFn(r))}</div>`;
        cols.forEach((_c,ci)=>{
          const n=counts[ri][ci]||0;
          const km=(kms[ri]&&kms[ri][ci])||0;
          const col=n?turbo(n/max):null;
          const bg=col?col.bg:"var(--panel2)";
          const fg=col?col.fg:"var(--fg)";
          const titleAttr=`${esc(rowLabelFn(r))} × ${esc(colLabelFn(_c))}: ${n} · ${km} km`;
          html+=`<div class="hm-cell" style="background:${bg};color:${fg}" title="${titleAttr}">${n||""}</div>`;
        });
      }
      html+="</div>";
      if(remaining>0){
        html+=`<button type="button" class="stats-more">Mehr laden (${fmtNum(remaining)} weitere, ${fmtNum(allRows.length)} gesamt)</button>`;
      }
      wrap.innerHTML=html;
      const moreBtn=wrap.querySelector(".stats-more");
      if(moreBtn) moreBtn.onclick=()=>{ showAll=true; paint(); };
    }
    paint();
    parent.appendChild(wrap);
  }
  const id=x=>x;
  const wdLab=k=>{ const i=+k; return (i>=0&&i<7)?WD[i]:k; };
  const catLab=c=>catLabel(c);
  const crossEl=document.getElementById("stats-cross");
  crossEl.innerHTML=`<h3>Kreuztabellen</h3><p class="hint">Zellen = Anzahl Fahrten; Hover zeigt Kilometer.</p>`;
  mountHeatmap(crossEl, S.crossLocClassLine, "Baureihe × Linie", id, lineName, "stats-cross-loc-line");
  mountHeatmap(crossEl, S.crossVehicleLine, "Fahrzeug × Linie", id, lineName, "stats-cross-veh-line", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLineMonth, "Linie × Monat", lineName, id, "stats-cross-line-month", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLineWeekday, "Linie × Wochentag", lineName, wdLab, "stats-cross-line-weekday", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLocClassMonth, "Baureihe × Monat", id, id, "stats-cross-loc-month");
  mountHeatmap(crossEl, S.crossLocClassWeekday, "Baureihe × Wochentag", id, wdLab, "stats-cross-loc-weekday");
  mountHeatmap(crossEl, S.crossLocClassCategory, "Baureihe × Kategorie", id, catLab, "stats-cross-loc-category");
  mountHeatmap(crossEl, S.crossLineCategory, "Linie × Kategorie", lineName, catLab, "stats-cross-line-category", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLineDelay, "Linie × Verspätung", lineName, id, "stats-cross-line-delay", {initialLimit:50});
  mountHeatmap(crossEl, S.crossLocClassDelay, "Baureihe × Verspätung", id, id, "stats-cross-loc-delay");
  mountHeatmap(crossEl, S.crossVehicleMonth, "Fahrzeug × Monat", id, id, "stats-cross-veh-month", {initialLimit:50});
})();

// ---------- Map ----------
let map=null, mapBounds=[], mapFitted=false;
(function(){
  // Karte mit gültiger Startansicht initialisieren; fitBounds erst beim ersten
  // Anzeigen des Tabs, sonst rechnet Leaflet auf einem 0-Pixel-Container.
  map=L.map("mapview",{preferCanvas:true,maxZoom:19}).setView([51,10],6);
  // Provider-Kette: schlägt einer fehl (DNS/Referer/Block), wird automatisch der
  // nächste versucht. Esri braucht keinen Referer und keinen API-Key.
  // maxZoom (einheitlich 19) erlaubt das Reinzoomen; über maxNativeZoom hinaus
  // werden die letzten verfügbaren Kacheln hochskaliert statt das Zoomen zu sperren.
  const PROVIDERS=[
    {url:"https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
     opts:{maxZoom:19, maxNativeZoom:16, attribution:"&copy; Esri, HERE, Garmin"}},
    {url:"https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
     // OSM verlangt laut Tile-Policy einen identifizierbaren Referer; "unsafe-url"
     // sendet die volle Seiten-URL mit, damit die Kacheln nicht (rate-)geblockt
     // werden. Wirkt nur, wenn das Dashboard über http(s) ausgeliefert wird –
     // bei file:// unterdrücken Browser den Referer generell.
     opts:{maxZoom:19, maxNativeZoom:19, subdomains:"abc",
       referrerPolicy:"unsafe-url", attribution:"&copy; OpenStreetMap"}},
    {url:"https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
     opts:{maxZoom:19, maxNativeZoom:17, subdomains:"abc", attribution:"&copy; OpenTopoMap (CC-BY-SA)"}},
  ];
  let baseLayer=null, tileErrors=0;
  function useProvider(i){
    if(baseLayer) map.removeLayer(baseLayer);
    const p=PROVIDERS[i]; tileErrors=0;
    baseLayer=L.tileLayer(p.url, Object.assign(
      {referrerPolicy:"strict-origin-when-cross-origin"}, p.opts));
    baseLayer.on("tileerror", ()=>{
      if(++tileErrors>6 && i+1<PROVIDERS.length){ useProvider(i+1); }
    });
    baseLayer.addTo(map);
  }
  useProvider(0);

  // Rohdaten: Kanten/Knoten referenzieren einen Variant-Index (Attribut-Kombi
  // aus DATA.variants) und nur Station-IDs; die Koordinaten liegen einmalig in ST.
  // Fahrzeug-Filter nutzt eigene Buckets (vehEdges/vehNodes), damit Mehrfachwagen
  // bei "Alle" nicht doppelt zählen.
  const ST=DATA.stations||{};        // "id" -> [lat, lon, name]
  const VAR=DATA.variants||[];       // [lineKey, locClass, category, operator, date]
  const FAM=DATA.locClassFamilies||{}; // Familie -> [locClass, ...]
  const VEH=DATA.mapVehicles||[];    // [locClass, number]
  const rawEdges=DATA.edges||[];     // [a_id, b_id, variantIdx, count]
  const rawNodes=DATA.nodes||[];     // [sid, variantIdx, count, usedCount]
  const rawVehEdges=DATA.vehEdges||[]; // [a_id, b_id, variantIdx, vehIdx, count]
  const rawVehNodes=DATA.vehNodes||[]; // [sid, variantIdx, vehIdx, count, usedCount]
  if(!rawEdges.length){ return; }
  const lineEl=document.getElementById("mapLine");
  const locEl=document.getElementById("mapLoc");
  const vehEl=document.getElementById("mapVehicle");
  const catEl=document.getElementById("mapCat");
  const opEl=document.getElementById("mapOperator");
  const yearEl=document.getElementById("mapYear");
  const dateEl=document.getElementById("mapDate");
  const countEl=document.getElementById("mapCount");
  const kpis=DATA.kpis||{};
  bindDateInput(dateEl, kpis.first, kpis.last);

  // Farbskala nach Häufigkeit, Turbo-Spektrum Blau -> Rot.
  function color(t){ // t in [0,1]
    const stops=[[48,105,229],[0,193,212],[38,201,111],[173,220,48],
                 [250,214,40],[247,150,32],[224,52,32]];
    const x=t*(stops.length-1), i=Math.min(Math.floor(x),stops.length-2), f=x-i;
    const a=stops[i], b=stops[i+1];
    return `rgb(${Math.round(a[0]+(b[0]-a[0])*f)},${Math.round(a[1]+(b[1]-a[1])*f)},${Math.round(a[2]+(b[2]-a[2])*f)})`;
  }

  // Filter-Dropdowns aus den Varianten befüllen (nach Label sortiert). `labelFn`
  // erlaubt eine abweichende Anzeige (z.B. Kategorie-Code -> Klartext). Leere
  // Werte (ungetaggte Fahrt) als eigene "(ohne …)"-Option anbieten.
  function fillSelect(el, values, emptyLabel, labelFn, desc){
    const lf = labelFn || (v=>v);
    const set=new Set(values);
    const hasEmpty=emptyLabel && set.has("");
    let nonEmpty=[...set].filter(v=>v!=="")
      .sort((a,b)=>String(lf(a)).localeCompare(String(lf(b)),undefined,{numeric:true}));
    if(desc) nonEmpty.reverse();
    el.innerHTML='<option value="__all__">Alle</option>'
      + nonEmpty.map(v=>`<option value="${esc(v)}">${esc(lf(v))}</option>`).join("")
      + (hasEmpty?`<option value="__none__">${esc(emptyLabel)}</option>`:"");
  }
  function vehLabel(v){
    if(!v) return "";
    const loc=v[0]||"", num=v[1]||"";
    if(loc && num) return loc+" · "+num;
    return num||loc;
  }
  function fillVehicleSelect(){
    const tagged=VEH.map((v,i)=>({i,v})).filter(x=>x.v[0]||x.v[1]);
    tagged.sort((a,b)=>vehLabel(a.v).localeCompare(vehLabel(b.v),undefined,{numeric:true}));
    const hasEmpty=VEH.some(v=>!v[0]&&!v[1]);
    vehEl.innerHTML='<option value="__all__">Alle</option>'
      + tagged.map(x=>`<option value="${x.i}">${esc(vehLabel(x.v))}</option>`).join("")
      + (hasEmpty?'<option value="__none__">(ohne Fahrzeug)</option>':'');
  }
  function fillLocSelect(){
    const set=new Set(VAR.map(v=>v[1]));
    const hasEmpty=set.has("");
    const classes=[...set].filter(v=>v!=="")
      .sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    const families=Object.keys(FAM)
      .sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    let html='<option value="__all__">Alle</option>';
    if(families.length){
      html+='<optgroup label="Familien">'+
        families.map(f=>`<option value="__fam__${esc(f)}">${esc(f)}</option>`).join("")+
        '</optgroup>';
    }
    if(classes.length){
      html+='<optgroup label="Baureihen">'+
        classes.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join("")+
        '</optgroup>';
    }
    if(hasEmpty) html+='<option value="__none__">(ohne Baureihe)</option>';
    locEl.innerHTML=html;
  }
  fillSelect(lineEl, VAR.map(v=>v[0]), "(ohne Linie)", lineName);
  fillLocSelect();
  fillVehicleSelect();
  fillSelect(catEl, VAR.map(v=>v[2]), "(ohne Kategorie)", catLabel);
  fillSelect(opEl, VAR.map(v=>v[3]), "(ohne Operator)");
  fillSelect(yearEl, VAR.map(v=>(v[4]||"").slice(0,4)), "(ohne Jahr)", null, true);

  // Einzelnen Select-Wert gegen ein Attribut prüfen (__all__/__none__/Wert).
  function sel(val, actual){
    if(val==="__all__") return true;
    if(val==="__none__") return actual==="";
    return actual===val;
  }
  // Baureihen-Filter: exakter Tag, oder alle Mitglieder einer Familie (__fam__).
  function selLoc(val, actual){
    if(val==="__all__") return true;
    if(val==="__none__") return actual==="";
    if(val.startsWith("__fam__")){
      const members=FAM[val.slice(7)]||[];
      return members.indexOf(actual)>=0;
    }
    return actual===val;
  }
  // Aktuelle Filter-Auswahl auf eine Variante (Attribut-Kombi) anwenden.
  // Index 4 ist das Reisedatum (YYYY-MM-DD); Jahr-Dropdown filtert per Präfix,
  // solange kein konkretes Datum gesetzt ist (Datum ist spezieller).
  function matchesVar(vi){
    const v=VAR[vi]; if(!v) return false;
    const date=v[4]||"";
    const dateWant=dateEl.value;
    const yearOk=dateWant
      ? true
      : sel(yearEl.value, date.slice(0,4));
    const dateOk=!dateWant || date===dateWant;
    return sel(lineEl.value, v[0]) && selLoc(locEl.value, v[1])
        && sel(catEl.value, v[2]) && sel(opEl.value, v[3]) && yearOk && dateOk;
  }
  function matchesVeh(vehIdx){
    const want=vehEl.value;
    if(want==="__all__") return true;
    const v=VEH[vehIdx]; if(!v) return false;
    if(want==="__none__") return !v[0]&&!v[1];
    return String(vehIdx)===want;
  }

  // Sichtbare Kanten/Knoten aus den passenden Buckets aggregieren. Skala je
  // Ansicht neu aus den sichtbaren Zählwerten (min..max) bestimmen.
  let vEdges=[], vNodes=[], vMin=1, vMax=1;
  function scale(c){ if(vMax===vMin) return 0.5;
    const t=(Math.log(Math.max(c,1))-Math.log(vMin))/(Math.log(vMax)-Math.log(vMin));
    return Math.max(0, Math.min(1, t)); }
  function computeVisible(){
    const useVeh=vehEl.value!=="__all__";
    const em=new Map();
    if(useVeh){
      rawVehEdges.forEach(row=>{
        const a=row[0], b=row[1], vi=row[2], vhi=row[3], cnt=row[4];
        if(!matchesVar(vi) || !matchesVeh(vhi)) return;
        const sa=ST[a], sb=ST[b];
        if(!sa||!sb) return;
        const key=a+"|"+b, cur=em.get(key);
        if(cur) cur.count+=cnt;
        else em.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2],count:cnt});
      });
    } else {
      rawEdges.forEach(row=>{
        const a=row[0], b=row[1];
        if(!matchesVar(row[2])) return;
        const sa=ST[a], sb=ST[b];
        if(!sa||!sb) return;
        const key=a+"|"+b, cur=em.get(key);
        if(cur) cur.count+=row[3];
        else em.set(key,{a:[sa[0],sa[1]],b:[sb[0],sb[1]],from:sa[2],to:sb[2],count:row[3]});
      });
    }
    vEdges=[...em.values()];
    const nm=new Map();
    if(useVeh){
      rawVehNodes.forEach(row=>{
        const sid=row[0], vi=row[1], vhi=row[2], cnt=row[3], used=row[4]||0;
        if(!matchesVar(vi) || !matchesVeh(vhi)) return;
        const s=ST[sid]; if(!s) return;
        const cur=nm.get(sid);
        if(cur){ cur.count+=cnt; cur.usedCount+=used; }
        else nm.set(sid,{lat:s[0],lon:s[1],name:s[2],count:cnt,usedCount:used});
      });
    } else {
      rawNodes.forEach(row=>{
        const sid=row[0];
        if(!matchesVar(row[1])) return;
        const s=ST[sid]; if(!s) return;
        const cnt=row[2], used=row[3]||0;
        const cur=nm.get(sid);
        if(cur){ cur.count+=cnt; cur.usedCount+=used; }
        else nm.set(sid,{lat:s[0],lon:s[1],name:s[2],count:cnt,usedCount:used});
      });
    }
    vNodes=[...nm.values()];
    const cs=vEdges.map(e=>e.count);
    vMax=cs.length?Math.max(...cs):1;
    vMin=cs.length?Math.min(...cs):1;
    // Dünne/seltene zuerst, damit dicke/häufige oben liegen.
    vEdges.sort((p,q)=>p.count-q.count);
    vNodes.sort((p,q)=>p.count-q.count || ((p.usedCount||0)-(q.usedCount||0)));
  }
  // Gerichtete Kanten "im Rechtsverkehr": jede Richtung entlang der (nach rechts
  // zeigenden) Segment-Normalen versetzt, plus Richtungspfeil in der Mitte. In
  // Layer-Point-Koordinaten gerechnet und bei jedem Zoom neu gezeichnet, damit der
  // Versatz optisch konstant bleibt.
  const ARROW=7, GAP=1.5;
  // Kanten UND Knoten liegen in EINER LayerGroup im Standard-Canvas (kein eigenes
  // Knoten-Pane): ein zweites Canvas darüber würde als oberste Ebene sämtliche
  // Klicks abfangen, bevor sie das Kanten-Canvas darunter erreichen (Leaflet reicht
  // Klicks nicht von einem Canvas an ein anderes weiter) – dann wäre keine Linie
  // klickbar. Die Knoten werden pro Redraw ZULETZT gezeichnet, liegen dadurch
  // optisch oben und gewinnen den Hit-Test nur punktgenau; Klicks auf die Linie
  // dazwischen treffen die Kante.
  const overlay=L.layerGroup().addTo(map);
  function px(latlng){ return map.latLngToLayerPoint(latlng); }
  function ll(pt){ return map.layerPointToLatLng(pt); }
  function draw(){
    overlay.clearLayers();
    vEdges.forEach(e=>{
      const t=scale(e.count), col=color(t), weight=3+t*7;
      const pa=px(e.a), pb=px(e.b);
      const dx=pb.x-pa.x, dy=pb.y-pa.y;
      const len=Math.hypot(dx,dy)||1;
      const ux=dx/len, uy=dy/len;      // Fahrtrichtung
      const nx=-uy, ny=ux;             // Rechts-Normale (Screen-y nach unten)
      // Versatz relativ zur Linien­dicke: halbe Dicke + kleiner Spalt, damit sich
      // auch dicke (häufige) Gegenrichtungen nicht überlagern.
      const offMag=weight/2 + GAP;
      const off=L.point(nx*offMag, ny*offMag);
      const oa=pa.add(off), ob=pb.add(off);
      L.polyline([ll(oa),ll(ob)],{color:col,weight:weight,opacity:.85})
        .bindPopup(`<b>${esc(e.from)} → ${esc(e.to)}</b><br>${e.count}× befahren`)
        .addTo(overlay);
      // Pfeil-Chevron am Mittelpunkt; interactive:false, damit Klicks zur Linie
      // darunter durchgehen und die Kante klickbar bleibt.
      const m=L.point((oa.x+ob.x)/2,(oa.y+ob.y)/2);
      const tip=L.point(m.x+ux*ARROW, m.y+uy*ARROW);
      const wingL=L.point(tip.x-ux*ARROW+nx*ARROW*0.6, tip.y-uy*ARROW+ny*ARROW*0.6);
      const wingR=L.point(tip.x-ux*ARROW-nx*ARROW*0.6, tip.y-uy*ARROW-ny*ARROW*0.6);
      L.polyline([ll(wingL),ll(tip),ll(wingR)],
        {color:col,weight:2+t*3,opacity:.9,interactive:false}).addTo(overlay);
    });
    // Knoten ZULETZT -> liegen optisch oben, fangen Klicks aber nur punktgenau ab.
    vNodes.forEach(n=>{
      const used=(n.usedCount||0)>0;
      const col=used?color(scale(n.usedCount)):"#5b6b7d";
      L.circleMarker([n.lat,n.lon],{
        radius:used?5:3, color:used?"#1e293b":"#33475b", weight:used?2:1,
        fillColor:col, fillOpacity:used?.95:.8})
        .bindPopup(`<b>${esc(n.name)}</b><br>${n.count}× befahren<br>`+
          (used?`${n.usedCount}× Ein-/Ausstieg`:"nur Durchfahrt")).addTo(overlay);
    });
  }

  let legendDiv=null;
  function updateLegend(){
    if(!legendDiv) return;
    legendDiv.innerHTML=`<b>Befahrungen</b><br>
      <i style="background:${color(0)}"></i> selten (${vMin}×)<br>
      <i style="background:${color(.5)}"></i> mittel<br>
      <i style="background:${color(1)}"></i> häufig (${vMax}×)<br>
      <span class="muted">Pfeil = Fahrtrichtung<br>großer Punkt = Ein-/Ausstieg</span>`;
  }
  const lg=L.control({position:"bottomright"});
  lg.onAdd=function(){ legendDiv=L.DomUtil.create("div","legend"); updateLegend(); return legendDiv; };
  lg.addTo(map);

  // Alles neu berechnen und zeichnen (Init + bei Filterwechsel). Der erste
  // fitBounds passiert beim ersten Anzeigen des Tabs (siehe nav-Handler) auf
  // Basis der hier gefüllten mapBounds; die Ansicht bleibt beim Filtern erhalten.
  function drawAll(){
    computeVisible();
    mapBounds=[];
    vEdges.forEach(e=>{ mapBounds.push(e.a,e.b); });
    draw();
    updateLegend();
    countEl.textContent = vEdges.length + " Segmente";
  }
  drawAll();
  map.on("zoomend", draw);
  [lineEl,locEl,vehEl,catEl,opEl,yearEl,dateEl].forEach(el=>{
    el.onchange=drawAll;
    if(el===dateEl) el.oninput=drawAll;
  });
})();

// ---------- Trips table ----------
(function(){
  const tbody=document.querySelector("#tripsTable tbody");
  const table=document.getElementById("tripsTable");
  const filterEl=document.getElementById("filter");
  const dateFromEl=document.getElementById("tripDateFrom");
  const dateToEl=document.getElementById("tripDateTo");
  const copyBtn=document.getElementById("tripsCopy");
  const delayEl=document.getElementById("tripShowDelay");
  const routeEl=document.getElementById("tripShowRoute");
  const timeEl=document.getElementById("tripTimeMode");
  let rows=DATA.trips.map((t,i)=>({...t,_i:i}));
  const kpis=DATA.kpis||{};
  bindDateInput(dateFromEl, kpis.first, kpis.last);
  bindDateInput(dateToEl, kpis.first, kpis.last);
  let sortKey="date", sortAsc=false;
  let visible=[];
  let routeCacheKey="";
  let routeByI=new Map();
  const TSV_BASE=["Datum","Linie","Operator","Baureihe","Wagen","Von","Nach",
    "Ab","An","Zwischenhalte","km","Min"];

  function prefGet(k, d){ try{ const v=localStorage.getItem(k); return v==null?d:v; }catch(e){ return d; } }
  function prefSet(k, v){ try{ localStorage.setItem(k, v); }catch(e){} }
  delayEl.checked = prefGet("trwl-trips-delay","0")==="1";
  routeEl.checked = false;
  timeEl.value = prefGet("trwl-trips-times","planned")==="real" ? "real" : "planned";

  function showDelay(){ return delayEl.checked; }
  function showRoute(){ return routeEl.checked; }
  function timeMode(){ return timeEl.value==="real" ? "real" : "planned"; }
  function pickTime(planned, real){
    return timeMode()==="planned" ? (planned||real) : (real||planned);
  }
  function tripDep(t){ return pickTime(t.depPlanned, t.depReal); }
  function tripArr(t){ return pickTime(t.arrPlanned, t.arrReal); }
  function colCount(){ return 12 + (showRoute()?1:0) + (showDelay()?1:0); }

  function dash(v){ return v?esc(v):'<span class="muted">—</span>'; }
  function delayCell(d){ if(d==null) return '<span class="muted">—</span>';
    if(d>0) return `<span class="pos">+${d}</span>`;
    if(d<0) return `<span class="neg">${d}</span>`; return "0"; }
  function delayText(d){ if(d==null) return ""; if(d>0) return "+"+d; return String(d); }
  function timeText(iso){ if(!iso) return ""; const s=fmtTime(iso); return s==="—"?"":s; }

  function stopKey(s){
    if(s && s.id!=null && s.id!=="") return String(s.id);
    return "n:"+((s && s.name)||"");
  }
  function tripPath(t){
    const stops=t.stopovers||[];
    const out=[];
    const n=stops.length;
    if(!n){
      if(t.from) out.push({key:"n:"+t.from, name:t.from});
      if(t.to && (!t.from || t.to!==t.from)) out.push({key:"n:"+t.to, name:t.to});
      return out;
    }
    stops.forEach((s,i)=>{
      if(i!==0 && i!==n-1 && s.cancelled) return;
      const key=stopKey(s);
      if(out.length && out[out.length-1].key===key) return;
      out.push({key, name:s.name||""});
    });
    return out;
  }
  function firstAB(path, aKey, bKey){
    let iA=-1;
    for(let i=0;i<path.length;i++){
      if(iA<0 && path[i].key===aKey) iA=i;
      else if(iA>=0 && path[i].key===bKey) return [iA, i];
    }
    return null;
  }
  function computeRoutes(list){
    const paths=list.map(tripPath);
    const nbr=new Map(), od=new Set();
    function addUndirected(a,b){
      if(a===b) return;
      if(!nbr.has(a)) nbr.set(a,new Set());
      if(!nbr.has(b)) nbr.set(b,new Set());
      nbr.get(a).add(b);
      nbr.get(b).add(a);
    }
    paths.forEach(p=>{
      if(!p.length) return;
      od.add(p[0].key);
      od.add(p[p.length-1].key);
      for(let i=0;i<p.length-1;i++) addUndirected(p[i].key, p[i+1].key);
    });
    const anchors=new Set(od);
    paths.forEach(p=>p.forEach(s=>{
      if((nbr.get(s.key)||new Set()).size>2) anchors.add(s.key);
    }));
    function setsBetween(aKey, bKey){
      const sets=[];
      paths.forEach(p=>{
        const pair=firstAB(p, aKey, bKey);
        if(!pair) return;
        const mid=new Set();
        for(let i=pair[0]+1;i<pair[1];i++) mid.add(p[i].key);
        sets.push(mid);
      });
      return sets;
    }
    const out=new Map();
    list.forEach((t,idx)=>{
      const p=paths[idx];
      if(p.length<=1){
        out.set(t._i, p.map(s=>s.name).filter(Boolean).join(" → "));
        return;
      }
      const forced=[];
      p.forEach((s,i)=>{
        if(i===0 || i===p.length-1 || anchors.has(s.key)) forced.push(i);
      });
      const keep=new Set(forced);
      for(let f=0;f<forced.length-1;f++){
        const iA=forced[f], iB=forced[f+1];
        if(iB<=iA+1) continue;
        const sets=setsBetween(p[iA].key, p[iB].key);
        if(sets.length<2) continue;
        const sigs=new Set(sets.map(s=>[...s].sort().join("\0")));
        if(sigs.size<2) continue;
        let inter=null;
        sets.forEach(s=>{
          if(inter==null){ inter=new Set(s); return; }
          [...inter].forEach(k=>{ if(!s.has(k)) inter.delete(k); });
        });
        const uniqueIdx=[];
        for(let i=iA+1;i<iB;i++){
          if(!inter.has(p[i].key)) uniqueIdx.push(i);
        }
        if(!uniqueIdx.length) continue;
        keep.add(uniqueIdx[Math.floor((uniqueIdx.length-1)/2)]);
      }
      const parts=[];
      let lastKey=null;
      p.forEach((s,i)=>{
        if(!keep.has(i) || s.key===lastKey) return;
        lastKey=s.key;
        if(s.name) parts.push(s.name);
      });
      out.set(t._i, parts.join(" → "));
    });
    return out;
  }
  function ensureRoutes(list){
    if(!showRoute()) return;
    const key=list.map(t=>t._i).slice().sort((a,b)=>a-b).join(",");
    if(key===routeCacheKey) return;
    routeCacheKey=key;
    routeByI=computeRoutes(list);
  }
  function routeOf(t){ return routeByI.get(t._i)||""; }

  function detailHtml(t){
    const head=`<div class="stops"><div class="h">Halt</div><div class="h">An</div>
      <div class="h">Ab</div><div class="h">Gleis</div>`;
    const body=t.stopovers.map(s=>{
      const an = pickTime(s.arrivalPlanned, s.arrivalReal);
      const ab = pickTime(s.departurePlanned, s.departureReal);
      const cancel = s.cancelled? ' style="text-decoration:line-through;color:#d1242f"':'';
      return `<div${cancel}>${esc(s.name)}</div><div>${fmtTime(an)}</div>
        <div>${fmtTime(ab)}</div><div>${esc(s.platform||"—")}</div>`;
    }).join("");
    const note = t.body? `<div class="muted" style="margin-top:8px">„${esc(t.body)}"</div>`:"";
    return `<td colspan="${colCount()}">${head}${body}</div>${note}</td>`;
  }

  function filteredSorted(){
    const q=filterEl.value.toLowerCase().trim();
    const fromVal=dateFromEl.value;
    const toVal=dateToEl.value;
    // Immer Kopie: sonst sortiert list===rows die Quelle in-place und
    // rows[data-i] trifft nach dem Sort die falsche Fahrt.
    let list=rows.filter(t=>{
      if(q){
        const hay=(t.date+" "+lineName(t.line)+" "+(t.operator||"")+" "+
          t.locClass+" "+t.vehicles+" "+t.category+" "+t.from+" "+t.to)
          .toLowerCase();
        if(!hay.includes(q)) return false;
      }
      const day=(t.date||"").slice(0,10);
      if(fromVal && day<fromVal) return false;
      if(toVal && day>toVal) return false;
      return true;
    });
    if(showRoute() && sortKey==="route") ensureRoutes(list);
    list.sort((a,b)=>{
      let x=a[sortKey], y=b[sortKey];
      if(sortKey==="line"){ x=lineName(x); y=lineName(y); }
      if(sortKey==="depTime"){ x=tripDep(a); y=tripDep(b); }
      if(sortKey==="arrTime"){ x=tripArr(a); y=tripArr(b); }
      if(sortKey==="route"){ x=routeOf(a); y=routeOf(b); }
      if(x==null)x=-Infinity; if(y==null)y=-Infinity;
      if(typeof x==="string"){ const r=x.localeCompare(y); return sortAsc?r:-r; }
      return sortAsc? x-y : y-x;
    });
    return list;
  }

  function applyExtraCols(){
    table.classList.toggle("no-delay", !showDelay());
    table.classList.toggle("no-route", !showRoute());
    const hidden=(sortKey==="delay" && !showDelay()) || (sortKey==="route" && !showRoute());
    if(hidden){
      sortKey="date"; sortAsc=false;
      document.querySelectorAll("#tripsTable th").forEach(x=>x.classList.remove("sorted","asc"));
      const th=table.querySelector('th[data-k="date"]');
      if(th) th.classList.add("sorted");
    }
  }

  function render(){
    applyExtraCols();
    visible=filteredSorted();
    if(showRoute()) ensureRoutes(visible);
    else { routeCacheKey=""; routeByI=new Map(); }
    document.getElementById("tripcount").textContent=visible.length+" Fahrten";
    tbody.innerHTML=visible.map(t=>
      `<tr class="trip" data-i="${t._i}">
        <td>${fmtDate(t.date)}</td><td>${esc(lineName(t.line))}</td>
        <td>${dash(t.operator)}</td>
        <td>${dash(t.locClass)}</td>
        <td>${dash(t.vehicles)}</td>
        <td>${esc(t.from)}</td><td>${esc(t.to)}</td>
        <td>${fmtTime(tripDep(t))}</td><td>${fmtTime(tripArr(t))}</td>
        <td>${t.viaStops}</td>
        <td class="route">${esc(routeOf(t))}</td>
        <td>${t.distanceKm}</td><td>${t.durationMin}</td>
        <td class="delay">${delayCell(t.delay)}</td></tr>`).join("");
  }

  function tsvCell(s){ return String(s??"").replace(/[\t\n\r]+/g," ").trim(); }
  function copyFallback(text){
    const ta=document.createElement("textarea");
    ta.value=text; ta.setAttribute("readonly","");
    ta.style.cssText="position:fixed;left:-9999px";
    document.body.appendChild(ta); ta.select();
    let ok=false;
    try{ ok=document.execCommand("copy"); }catch(e){}
    document.body.removeChild(ta);
    return ok;
  }
  function copyFeedback(ok){
    const old=copyBtn.textContent;
    copyBtn.textContent=ok?"Kopiert":"Kopieren fehlgeschlagen";
    setTimeout(()=>{ copyBtn.textContent=old; }, ok?1500:2000);
  }
  function copyTsv(){
    const headers=TSV_BASE.slice();
    const routeOn=showRoute(), delayOn=showDelay();
    if(routeOn) headers.splice(10, 0, "Laufweg");
    if(delayOn) headers.push("Versp.");
    const lines=[headers.join("\t")];
    visible.forEach(t=>{
      const row=[fmtDate(t.date), lineName(t.line), t.operator||"", t.locClass||"",
        t.vehicles||"", t.from||"", t.to||"", timeText(tripDep(t)), timeText(tripArr(t)),
        t.viaStops];
      if(routeOn) row.push(routeOf(t));
      row.push(t.distanceKm, t.durationMin);
      if(delayOn) row.push(delayText(t.delay));
      lines.push(row.map(tsvCell).join("\t"));
    });
    const text=lines.join("\n");
    if(navigator.clipboard && window.isSecureContext){
      navigator.clipboard.writeText(text).then(()=>copyFeedback(true))
        .catch(()=>copyFeedback(copyFallback(text)));
      return;
    }
    copyFeedback(copyFallback(text));
  }

  tbody.addEventListener("click",e=>{
    const tr=e.target.closest("tr.trip"); if(!tr) return;
    const next=tr.nextElementSibling;
    if(next && next.classList.contains("detail")){ next.remove(); return; }
    document.querySelectorAll("tr.detail").forEach(d=>d.remove());
    const t=DATA.trips[+tr.dataset.i];
    const dr=document.createElement("tr"); dr.className="detail";
    dr.innerHTML=detailHtml(t); tr.after(dr);
  });

  document.querySelectorAll("#tripsTable th").forEach(th=>th.onclick=()=>{
    const k=th.dataset.k;
    if((k==="delay" && !showDelay()) || (k==="route" && !showRoute())) return;
    if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=true; }
    document.querySelectorAll("#tripsTable th").forEach(x=>x.classList.remove("sorted","asc"));
    th.classList.add("sorted"); if(sortAsc) th.classList.add("asc");
    render();
  });
  delayEl.onchange=()=>{ prefSet("trwl-trips-delay", showDelay()?"1":"0"); render(); };
  routeEl.onchange=render;
  timeEl.onchange=()=>{ prefSet("trwl-trips-times", timeMode()); render(); };
  copyBtn.onclick=copyTsv;
  filterEl.oninput=render;
  [dateFromEl,dateToEl].forEach(el=>{
    el.onchange=render;
    el.oninput=render;
  });
  render();
})();

// ---------- Vehicle matrix ----------
(function(){
  const V=DATA.vehicles||[];
  const LC=DATA.lineColors||{};
  const container=document.getElementById("vehMatrices");
  const groupEl=document.getElementById("vehGroup");
  const opEl=document.getElementById("vehOperator");
  const catEl=document.getElementById("vehCategory");
  const yearEl=document.getElementById("vehYear");
  const filterEl=document.getElementById("vehFilter");
  const countEl=document.getElementById("vehCount");
  const modal=document.getElementById("vehModal");
  const modalBody=document.getElementById("vehModalBody");

  // CAT_LABEL/catLabel sind global definiert (siehe oben).
  let sortKey="vehicleNumber", sortAsc=true;
  let cellRegistry=[];

  function fillSelect(el,opts){
    el.innerHTML='<option value="__all__">Alle</option>'+
      opts.map(o=>`<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
  }
  function uniq(arr){ return [...new Set(arr)]; }

  // Dropdowns aus den Datensätzen befüllen.
  fillSelect(opEl, uniq(V.map(r=>r.operator)).sort()
    .map(o=>({value:o,label:o||"(ohne Operator)"})));
  fillSelect(catEl, uniq(V.map(r=>r.category)).filter(Boolean).sort()
    .map(c=>({value:c,label:catLabel(c)})));
  fillSelect(yearEl, uniq(V.map(r=>(r.date||"").slice(0,4)).filter(Boolean)).sort().reverse()
    .map(y=>({value:y,label:y})));

  function cls(k){ return sortKey===k ? (sortAsc?"sorted asc":"sorted") : ""; }

  function selectedShows(){
    return [...document.querySelectorAll('input[name="vehShow"]:checked')].map(el=>el.value);
  }
  function cellText(cr, shows){
    const parts=[];
    if(shows.includes("date")){
      const latest=cr.reduce((a,b)=>a.date>=b.date?a:b);
      parts.push(fmtDate(latest.date));
    }
    if(shows.includes("count")) parts.push("×"+cr.length);
    if(shows.includes("km")){
      const km=Math.round(cr.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;
      parts.push(km+" km");
    }
    if(shows.includes("segments")){
      const seg=cr.reduce((s,r)=>s+(r.segments||0),0);
      parts.push(seg+" Seg.");
    }
    return parts.join(" · ");
  }

  function rowCmp(a,b){
    let x,y;
    if(sortKey==="first"){ x=a.first; y=b.first; }
    else if(sortKey==="last"){ x=a.last; y=b.last; }
    else {
      const nx=parseFloat(a.number), ny=parseFloat(b.number);
      if(!isNaN(nx)&&!isNaN(ny)) return sortAsc? nx-ny : ny-nx;
      x=a.number; y=b.number;
    }
    const r=String(x).localeCompare(String(y),undefined,{numeric:true});
    return sortAsc? r : -r;
  }

  /** Ganzzahlige Wagennummer oder NaN (nur reine Ziffernketten). */
  function vehInt(n){
    const s=String(n==null?"":n).trim();
    return /^\d+$/.test(s) ? +s : NaN;
  }
  /** Markiert Läufe benachbarter Zeilen mit Nummern ±1 (Start/Mitte/Ende). */
  function markStreaks(rows){
    const nums=rows.map(r=>vehInt(r.number));
    rows.forEach(r=>{ r.streak=""; });
    let i=0;
    while(i<rows.length){
      if(isNaN(nums[i])){ i++; continue; }
      let j=i+1;
      let step=null;
      while(j<rows.length && !isNaN(nums[j])){
        const d=nums[j]-nums[j-1];
        if(d!==1 && d!==-1) break;
        if(step===null) step=d;
        else if(d!==step) break;
        j++;
      }
      if(j-i>=2){
        rows[i].streak="veh-streak veh-streak-start";
        for(let k=i+1;k<j-1;k++) rows[k].streak="veh-streak veh-streak-mid";
        rows[j-1].streak="veh-streak veh-streak-end";
      }
      i=j>i ? j : i+1;
    }
  }

  function renderMatrices(){
    const groupDim=groupEl.value;
    const op=opEl.value, cat=catEl.value, yr=yearEl.value;
    const q=filterEl.value.toLowerCase().trim();
    const shows=selectedShows();
    cellRegistry=[];

    const filtered=V.filter(r=>{
      if(op!=="__all__" && r.operator!==op) return false;
      if(cat!=="__all__" && r.category!==cat) return false;
      if(yr!=="__all__" && (r.date||"").slice(0,4)!==yr) return false;
      if(q && !((r.vehicleNumber+" "+lineName(r.line)).toLowerCase().includes(q))) return false;
      return true;
    });

    if(!V.length){
      container.innerHTML='<p class="muted">Keine Fahrzeug-Tags vorhanden. Tagge Fahrten in '+
        'Träwelling mit Wagennummer/Baureihe – die Tags erscheinen beim nächsten Export.</p>';
      countEl.textContent="";
      return;
    }
    if(!filtered.length){
      container.innerHTML='<p class="muted">Keine Fahrzeuge für die gewählten Filter.</p>';
      countEl.textContent="0 Fahrzeuge";
      return;
    }

    // Nach Gruppierungs-Dimension gruppieren.
    const groups=new Map();
    filtered.forEach(r=>{
      const g=groupDim==="locClass" ? (r.locClass||"Unbekannt") : catLabel(r.category);
      if(!groups.has(g)) groups.set(g,[]);
      groups.get(g).push(r);
    });
    const groupNames=[...groups.keys()].sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
    const showClass=groupDim!=="locClass";

    let totalVeh=0, html="";
    groupNames.forEach(gname=>{
      const recs=groups.get(gname);
      // Zeilen (Fahrzeuge) und Spalten (Linien) der Gruppe sammeln.
      const rowMap=new Map();
      const lineSet=new Set();
      recs.forEach(r=>{
        const key=r.locClass+"|"+r.vehicleNumber;
        if(!rowMap.has(key)) rowMap.set(key,{number:r.vehicleNumber,locClass:r.locClass,recs:[]});
        rowMap.get(key).recs.push(r);
        if(r.line) lineSet.add(r.line);
      });
      const lines=[...lineSet].sort((a,b)=>
        lineName(a).localeCompare(lineName(b),undefined,{numeric:true})
        || String(a).localeCompare(String(b)));
      const rowArr=[...rowMap.values()];
      rowArr.forEach(row=>{
        const ds=row.recs.map(r=>r.date).filter(Boolean).sort();
        row.first=ds[0]||""; row.last=ds[ds.length-1]||"";
        row.count=row.recs.length;
        row.km=Math.round(row.recs.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;
      });
      rowArr.sort(rowCmp);
      markStreaks(rowArr);
      totalVeh+=rowArr.length;
      const groupKm=Math.round(recs.reduce((s,r)=>s+(r.distanceKm||0),0)*10)/10;

      const head=`<tr>
        <th class="sticky lbl ${cls('vehicleNumber')}" data-k="vehicleNumber">Wagen</th>
        ${showClass?'<th class="lbl">Baureihe</th>':''}
        <th class="lbl ${cls('first')}" data-k="first">zuerst</th>
        <th class="lbl ${cls('last')}" data-k="last">zuletzt</th>
        ${lines.map(l=>{
          const c=LC[l];
          const st=c?` style="background:${c[0]};color:${c[1]}"`:'';
          return `<th class="lbl lineh"><span class="line-badge"${st}>${esc(lineName(l))}</span></th>`;
        }).join("")}
      </tr>`;
      const body=rowArr.map(row=>{
        const cells=lines.map(l=>{
          const cr=row.recs.filter(x=>x.line===l);
          if(!cr.length) return "<td></td>";
          const ci=cellRegistry.push(cr)-1;
          const c=LC[l];
          const tint=c?` style="--tint:${c[0]}22"`:'';
          return `<td class="has"${tint} data-ci="${ci}">${cellText(cr,shows)}</td>`;
        }).join("");
        const streakCls=row.streak?` ${row.streak}`:"";
        return `<tr class="veh">
          <td class="sticky lbl${streakCls}">${esc(row.number)}`+
          `<span class="muted veh-stats">×${row.count} · ${row.km} km</span></td>
          ${showClass?`<td class="lbl">${esc(row.locClass||"—")}</td>`:''}
          <td class="lbl">${fmtDate(row.first)}</td>
          <td class="lbl">${fmtDate(row.last)}</td>
          ${cells}</tr>`;
      }).join("");
      html+=`<div class="matrix-wrap"><h3>${esc(gname)} · ${rowArr.length} `+
        `${rowArr.length===1?"Fahrzeug":"Fahrzeuge"} · ×${recs.length} · ${groupKm} km</h3>`+
        `<table class="matrix"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    });
    container.innerHTML=html;
    countEl.textContent=totalVeh+" Fahrzeuge · "+filtered.length+" Fahrten";
  }

  function openModal(ci){
    const recs=(cellRegistry[ci]||[]).slice()
      .sort((a,b)=>String(b.date).localeCompare(String(a.date)));
    if(!recs.length) return;
    const r0=recs[0];
    const head=`<h3>Wagen ${esc(r0.vehicleNumber)}`+
      `${r0.locClass?" · BR "+esc(r0.locClass):""} · Linie ${esc(lineName(r0.line))}</h3>`;
    const body=recs.map(r=>`<div class="ride">
      <div class="d">${fmtDate(r.date)} · ${esc(lineName(r.line))}</div>
      <div>${esc(r.from)} → ${esc(r.to)}</div>
      <div class="muted">${fmtTime(r.depTime)}–${fmtTime(r.arrTime)} · ${r.distanceKm} km · `+
      `${r.points} Punkte${r.operator?" · "+esc(r.operator):""}</div>
    </div>`).join("");
    modalBody.innerHTML=head+body;
    modal.classList.add("open");
  }
  function closeModal(){ modal.classList.remove("open"); }

  container.addEventListener("click",e=>{
    const th=e.target.closest("th[data-k]");
    if(th){ const k=th.dataset.k;
      if(sortKey===k) sortAsc=!sortAsc; else { sortKey=k; sortAsc=true; }
      renderMatrices(); return; }
    const td=e.target.closest("td.has");
    if(td) openModal(+td.dataset.ci);
  });
  document.getElementById("vehModalClose").onclick=closeModal;
  modal.addEventListener("click",e=>{ if(e.target===modal) closeModal(); });
  document.addEventListener("keydown",e=>{ if(e.key==="Escape") closeModal(); });
  [groupEl,opEl,catEl,yearEl].forEach(el=>el.onchange=renderMatrices);
  filterEl.oninput=renderMatrices;
  document.querySelectorAll('input[name="vehShow"]').forEach(el=>{
    el.addEventListener("change",()=>{
      if(!selectedShows().length){ el.checked=true; return; }
      renderMatrices();
    });
  });
  renderMatrices();
})();

// ---------- Tagesziele ----------
(function(){
  const DF=DATA.dailyFirsts||{};
  const DR=DATA.dailyRepeats||{};
  const dates=[...new Set(Object.keys(DF).concat(Object.keys(DR)))].sort();
  const dateEl=document.getElementById("dailyDate");
  const prevBtn=document.getElementById("dailyPrev");
  const nextBtn=document.getElementById("dailyNext");
  const summaryEl=document.getElementById("dailySummary");
  const bodyEl=document.getElementById("dailyBody");
  const LC=DATA.lineColors||{};
  const INITIAL=40;

  function badge(key){
    const name=lineName(key);
    const c=LC[key];
    const st=c?` style="background:${c[0]};color:${c[1]}"`:"";
    return `<span class="line-badge"${st}>${esc(name)}</span>`;
  }
  function fmtN(v){
    if(v==null||v==="") return "—";
    return Number(v).toLocaleString("de-DE");
  }

  const singles=[
    {key:"lines", title:"Linien", cols:[{k:"key", label:"Linie", line:true}]},
    {key:"locClasses", title:"Baureihen", cols:[{k:"key", label:"Baureihe", lbl:true}]},
    {key:"vehicles", title:"Fahrzeuge", cols:[
      {k:"key", label:"Wagen", lbl:true},
      {k:"locClass", label:"Baureihe", lbl:true},
    ]},
    {key:"topVehicleInClass", title:"Top-Wagen der Baureihe", cols:[
      {k:"vehicle", label:"Wagen", lbl:true},
      {k:"locClass", label:"Baureihe", lbl:true},
      {k:"prevVehicle", label:"zuvor", lbl:true},
    ]},
    {key:"edges", title:"Kanten", cols:[
      {k:"from", label:"Von", lbl:true},
      {k:"to", label:"Nach", lbl:true},
    ]},
    {key:"stationsUsed", title:"Benutzte Stationen", cols:[{k:"key", label:"Station", lbl:true}]},
    {key:"stationsThrough", title:"Durchfahrene Stationen", cols:[{k:"key", label:"Station", lbl:true}]},
  ];
  const combos=[
    {key:"lineVehicle", title:"Fahrzeug × Linie", cols:[
      {k:"vehicle", label:"Wagen", lbl:true},
      {k:"line", label:"Linie", line:true},
    ]},
    {key:"lineLocClass", title:"Baureihe × Linie", cols:[
      {k:"locClass", label:"Baureihe", lbl:true},
      {k:"line", label:"Linie", line:true},
    ]},
    {key:"vehEdge", title:"Fahrzeug × Kante", cols:[
      {k:"vehicle", label:"Wagen", lbl:true},
      {k:"from", label:"Von", lbl:true},
      {k:"to", label:"Nach", lbl:true},
    ]},
    {key:"lineEdge", title:"Linie × Kante", cols:[
      {k:"line", label:"Linie", line:true},
      {k:"from", label:"Von", lbl:true},
      {k:"to", label:"Nach", lbl:true},
    ]},
    {key:"locEdge", title:"Baureihe × Kante", cols:[
      {k:"locClass", label:"Baureihe", lbl:true},
      {k:"from", label:"Von", lbl:true},
      {k:"to", label:"Nach", lbl:true},
    ]},
    {key:"stationLine", title:"Station × Linie", cols:[
      {k:"station", label:"Station", lbl:true},
      {k:"line", label:"Linie", line:true},
    ]},
    {key:"vehEdgeLine", title:"Fahrzeug × Kante × Linie", cols:[
      {k:"vehicle", label:"Wagen", lbl:true},
      {k:"from", label:"Von", lbl:true},
      {k:"to", label:"Nach", lbl:true},
      {k:"line", label:"Linie", line:true},
    ]},
    {key:"locEdgeLine", title:"Baureihe × Kante × Linie", cols:[
      {k:"locClass", label:"Baureihe", lbl:true},
      {k:"from", label:"Von", lbl:true},
      {k:"to", label:"Nach", lbl:true},
      {k:"line", label:"Linie", line:true},
    ]},
  ];

  bindDateInput(dateEl, DATA.kpis&&DATA.kpis.first, DATA.kpis&&DATA.kpis.last);
  dateEl.value=dates.length?dates[dates.length-1]:(DATA.kpis&&DATA.kpis.last)||"";

  function idxOf(d){ return dates.indexOf(d); }

  function edgeKey(r){
    return (r&&r.from!=null&&r.to!=null)? String(r.from)+"\0"+String(r.to) : "";
  }
  function keysOf(arr, fn){
    const s=new Set();
    (arr||[]).forEach(r=>{ const k=fn(r); if(k) s.add(k); });
    return s;
  }
  function cascadeSets(buckets){
    return {
      lines: keysOf(buckets.lines, r=>r.key),
      locs: keysOf(buckets.locClasses, r=>r.key),
      vehs: keysOf(buckets.vehicles, r=>r.key),
      stationsUsed: keysOf(buckets.stationsUsed, r=>r.key),
      stationsThrough: keysOf(buckets.stationsThrough, r=>r.key),
      edges: keysOf(buckets.edges, edgeKey),
      vehLine: keysOf(buckets.lineVehicle, r=>r.vehicle+"\0"+r.line),
      locLine: keysOf(buckets.lineLocClass, r=>r.locClass+"\0"+r.line),
      vehEdge: keysOf(buckets.vehEdge, r=>r.vehicle+"\0"+r.from+"\0"+r.to),
      lineEdge: keysOf(buckets.lineEdge, r=>r.line+"\0"+r.from+"\0"+r.to),
      locEdge: keysOf(buckets.locEdge, r=>r.locClass+"\0"+r.from+"\0"+r.to),
    };
  }
  function isImplied(specKey, row, S){
    if(!S||!row) return false;
    const ek=edgeKey(row);
    if(specKey==="lineVehicle") return S.vehs.has(row.vehicle)||S.lines.has(row.line);
    if(specKey==="lineLocClass") return S.locs.has(row.locClass)||S.lines.has(row.line);
    if(specKey==="vehEdge") return S.vehs.has(row.vehicle)||S.edges.has(ek);
    if(specKey==="lineEdge") return S.lines.has(row.line)||S.edges.has(ek);
    if(specKey==="locEdge") return S.locs.has(row.locClass)||S.edges.has(ek);
    if(specKey==="stationLine") return S.stationsUsed.has(row.station)||S.stationsThrough.has(row.station)||S.lines.has(row.line);
    if(specKey==="vehEdgeLine") return S.vehs.has(row.vehicle)||S.edges.has(ek)||S.lines.has(row.line)
      ||S.vehLine.has(row.vehicle+"\0"+row.line)
      ||S.vehEdge.has(row.vehicle+"\0"+row.from+"\0"+row.to)
      ||S.lineEdge.has(row.line+"\0"+row.from+"\0"+row.to);
    if(specKey==="locEdgeLine") return S.locs.has(row.locClass)||S.edges.has(ek)||S.lines.has(row.line)
      ||S.locLine.has(row.locClass+"\0"+row.line)
      ||S.locEdge.has(row.locClass+"\0"+row.from+"\0"+row.to)
      ||S.lineEdge.has(row.line+"\0"+row.from+"\0"+row.to);
    return false;
  }
  function prepareRows(spec, rows, sets){
    const tagged=rows.map(r=>({r, implied:isImplied(spec.key, r, sets)}));
    tagged.sort((a,b)=>(a.implied?1:0)-(b.implied?1:0));
    return tagged;
  }

  function renderTable(spec, rows, sets, impliedTitle){
    if(!rows||!rows.length) return "";
    const tagged=prepareRows(spec, rows, sets);
    const impliedN=tagged.reduce((n,x)=>n+(x.implied?1:0), 0);
    const wrap=document.createElement("div");
    wrap.className="panel stats-scroll";
    wrap.style.marginBottom="16px";
    let showAll=tagged.length<=INITIAL;
    const hint=impliedTitle||"folgt aus anderem Erstvorkommen";
    function paint(){
      const data=showAll?tagged:tagged.slice(0, INITIAL);
      const remaining=tagged.length-data.length;
      const head=spec.cols.map(c=>`<th class="${c.lbl||c.line?"lbl":""}">${esc(c.label)}</th>`).join("");
      const body=data.map(({r, implied})=>{
        const cls=implied?' class="daily-implied" title="'+esc(hint)+'"':"";
        return "<tr"+cls+">"+spec.cols.map(c=>{
          const v=r[c.k];
          if(c.line) return `<td class="lbl">${badge(v)}</td>`;
          return `<td class="lbl">${esc(v==null||v===""?"—":v)}</td>`;
        }).join("")+"</tr>";
      }).join("");
      const more=remaining>0
        ? `<button type="button" class="stats-more">Mehr laden (${fmtN(remaining)} weitere, ${fmtN(tagged.length)} gesamt)</button>`
        : "";
      const count=impliedN
        ? `(${fmtN(tagged.length)}, davon ${fmtN(impliedN)} kaskadiert)`
        : `(${fmtN(tagged.length)})`;
      wrap.innerHTML=`<h3 style="margin:0 0 8px;font-size:14px">${esc(spec.title)} <span class="muted" style="font-weight:400">${count}</span></h3>
        <table class="stats"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>${more}`;
      const moreBtn=wrap.querySelector(".stats-more");
      if(moreBtn) moreBtn.onclick=()=>{ showAll=true; paint(); };
    }
    paint();
    return wrap;
  }

  function render(){
    const d=dateEl.value;
    const buckets=DF[d]||{};
    const repeatBuckets=DR[d]||{};
    const sets=cascadeSets(buckets);
    const repeatSets=cascadeSets(repeatBuckets);
    prevBtn.disabled=!dates.some(x=>x<d);
    nextBtn.disabled=!dates.some(x=>x>d);

    const chips=[];
    singles.concat(combos).forEach(s=>{
      const n=(buckets[s.key]||[]).length;
      if(n) chips.push(`<span>${esc(s.title)}: ${fmtN(n)}</span>`);
    });
    const hasRepeats=singles.concat(combos).some(s=>(repeatBuckets[s.key]||[]).length);
    summaryEl.textContent=dates.length
      ? (chips.length||hasRepeats?"":`Keine Fahrten am ${d||"—"}`)
      : "Keine Fahrten in den Daten";

    bodyEl.innerHTML="";
    if(chips.length){
      const chipRow=document.createElement("div");
      chipRow.className="daily-chips";
      chipRow.innerHTML=chips.join("");
      bodyEl.appendChild(chipRow);
    }

    function appendGroup(title, specs, srcBuckets, srcSets, impliedTitle){
      const parts=[];
      specs.forEach(s=>{
        const rows=srcBuckets[s.key]||[];
        if(!rows.length) return;
        parts.push(renderTable(s, rows, srcSets, impliedTitle));
      });
      if(!parts.length) return;
      const g=document.createElement("div");
      g.className="daily-group";
      g.innerHTML=`<h3>${esc(title)}</h3>`;
      parts.forEach(p=>g.appendChild(p));
      bodyEl.appendChild(g);
    }
    appendGroup("Neu", singles, buckets, sets);
    appendGroup("Erste Kombis", combos, buckets, sets);
    appendGroup("Wiederholungen", singles.filter(s=>s.key!=="topVehicleInClass"),
      repeatBuckets, repeatSets, "folgt aus anderer Wiederholung");
    appendGroup("Wiederholte Kombis", combos, repeatBuckets, repeatSets,
      "folgt aus anderer Wiederholung");
  }

  function step(dir){
    const d=dateEl.value;
    let i=idxOf(d);
    if(i<0){
      const cand=dir<0
        ? dates.filter(x=>x<d).pop()
        : dates.find(x=>x>d);
      if(cand){ dateEl.value=cand; render(); }
      return;
    }
    const ni=i+dir;
    if(ni>=0 && ni<dates.length){ dateEl.value=dates[ni]; render(); }
  }

  prevBtn.onclick=()=>step(-1);
  nextBtn.onclick=()=>step(1);
  dateEl.onchange=render;
  dateEl.oninput=render;
  render();
})();
</script>
</body>
</html>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"walita {__version__}",
        help="Version ausgeben und beenden.",
    )
    parser.add_argument("--statuses", default="data/statuses.json", help="Eingabe Statuses.")
    parser.add_argument("--stations", default="data/stations.json", help="Eingabe Stations-Koordinaten.")
    parser.add_argument("--output", "-o", default="data/dashboard.html", help="Ziel-HTML-Datei.")
    parser.add_argument("--open", action="store_true", help="Dashboard danach im Browser öffnen.")
    parser.add_argument(
        "--ignore-plus", action="store_true",
        help="Wagennummern-Tags nicht am '+' trennen (Doppeltraktion bleibt ein Fahrzeug).",
    )
    parser.add_argument(
        "--loc-class-families", default="loc_class_families.txt",
        help="Baureihe→Familie für den Kartenfilter "
             "(Default: loc_class_families.txt; JSON-Objekt, "
             "gleiche Baureihe darf mehrfach vorkommen).",
    )
    args = parser.parse_args(argv)

    try:
        with open(args.statuses, encoding="utf-8") as f:
            statuses = json.load(f)
    except FileNotFoundError:
        log(f"Fehler: {args.statuses} nicht gefunden. Erst download_statuses.py ausführen.")
        return 2
    except json.JSONDecodeError as e:
        # Typischer Fall: der Export wurde mitten im Schreiben abgebrochen.
        log(f"Fehler: {args.statuses} ist kein gültiges JSON ({e}). "
            f"Datei wahrscheinlich unvollständig – Export wiederholen.")
        return 2
    except OSError as e:
        log(f"Fehler: {args.statuses} nicht lesbar ({e}).")
        return 2

    try:
        with open(args.stations, encoding="utf-8") as f:
            stations = json.load(f)
    except FileNotFoundError:
        log(f"Hinweis: {args.stations} nicht gefunden – Karte bleibt leer (keine Koordinaten).")
        stations = {}
    except (json.JSONDecodeError, OSError) as e:
        log(f"Warnung: {args.stations} nicht lesbar ({e}) – Karte bleibt leer.")
        stations = {}

    try:
        loc_class_families = load_loc_class_families(args.loc_class_families)
    except ValueError as e:
        log(f"Fehler: {e}")
        return 2
    if loc_class_families:
        n = sum(len(v) if isinstance(v, list) else 1
                for v in loc_class_families.values())
        log(f"Baureihenfamilien: {n} Zuordnungen "
            f"aus {args.loc_class_families}.")
    elif os.path.isfile(args.loc_class_families):
        log(f"Baureihenfamilien: {args.loc_class_families} enthält keine Regeln.")

    data = build_data(
        statuses, stations,
        ignore_plus=args.ignore_plus,
        loc_class_families=loc_class_families,
    )
    log(f"Ausgewertet: {data['kpis']['count']} Fahrten, {len(data['edges'])} Karten-Kanten, "
        f"{len(data['segments'])} Segmente.")

    html = HTML_TEMPLATE.replace("__DATA__", json_for_script(data))
    try:
        parent = os.path.dirname(args.output)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(html)
    except OSError as e:
        log(f"Fehler: {args.output} nicht schreibbar ({e}).")
        return 1
    log(f"Geschrieben: {args.output}")

    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(args.output))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        log("Abgebrochen.")
        sys.exit(130)
