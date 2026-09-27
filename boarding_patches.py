#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokale Einstiegs-Patches: checkin.origin je Status auf einen anderen Halt legen.

Kein API-Write. Die Datei (Default `data/boarding_patches.json`) ersetzt beim
Dashboard-Bau den Einstieg auf einer Kopie. Quelle `statuses.json` bleibt
unangetastet. Der Ausstieg bleibt. Fahrzeit kommt aus den Zeitstempeln dieses
Laufs, Kilometer aus anderen Check-ins mit derselben Haltfolge oder bekannten
Kanten; die Luftlinie ist nur der letzte Ersatz.
"""

import copy
import json
import os
from datetime import datetime

from download_statuses import traveled_stopovers
from edge_patches import haversine_km


def empty_patches():
    return {"overrides": {}}


def empty_measures():
    return {"edges": {}, "paths": {}}


def status_id(value):
    """Normalisiert eine Status-ID zu int, sonst unverändert."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def stopover_id(value):
    """Normalisiert eine Stopover-ID zu int, sonst unverändert."""
    return status_id(value)


def load_patches(path):
    """Liest boarding_patches.json. Fehlende/ungültige Datei → leere Struktur.

    overrides: status_id -> stopover_id
    """
    if not path or not os.path.isfile(path):
        return empty_patches()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty_patches()
    if not isinstance(data, dict):
        return empty_patches()
    overrides = {}
    for row in data.get("overrides") or []:
        if not isinstance(row, dict):
            continue
        sid = status_id(row.get("statusId"))
        so = stopover_id(row.get("stopoverId"))
        if sid is None or so is None:
            continue
        overrides[sid] = so
    return {"overrides": overrides}


def dumps_patches(patches):
    """Serialisiert die interne Map-Form zurück ins JSON-Objekt."""
    overrides = []
    items = (patches.get("overrides") or {}).items()
    for sid, so in sorted(items, key=lambda kv: (isinstance(kv[0], str), str(kv[0]))):
        overrides.append({"statusId": sid, "stopoverId": so})
    return {"overrides": overrides}


def save_patches(path, patches):
    """Schreibt Patches atomar. Gibt True bei Erfolg."""
    if not path:
        return False
    parent = os.path.dirname(path)
    payload = dumps_patches(patches)
    tmp = path + ".tmp"
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def set_origin(patches, sid, so_id):
    sid = status_id(sid)
    so_id = stopover_id(so_id)
    if sid is None or so_id is None:
        return False
    patches.setdefault("overrides", {})[sid] = so_id
    return True


def clear_origin(patches, sid):
    (patches.get("overrides") or {}).pop(status_id(sid), None)


def override_stopover(patches, status):
    """Stopover-ID des Patches oder None."""
    if not isinstance(status, dict):
        return None
    sid = status_id(status.get("id"))
    if sid is None:
        return None
    return (patches or empty_patches()).get("overrides", {}).get(sid)


def _trip_stopovers(status):
    trip = status.get("trip") or {}
    stops = trip.get("stopovers") or []
    if not isinstance(stops, list):
        return []
    return [s for s in stops if isinstance(s, dict)]


def _same_stop(a, b):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    a_so, b_so = a.get("stopoverId"), b.get("stopoverId")
    if a_so is not None and b_so is not None:
        return stopover_id(a_so) == stopover_id(b_so)
    return a.get("id") is not None and a.get("id") == b.get("id")


def _end_index(stops, segment):
    """Index des letzten Slice-Halts in der vollen Haltfolge."""
    n = len(segment)
    if n == 0 or len(stops) < n:
        return None
    for i in range(len(stops) - n + 1):
        if all(_same_stop(stops[i + k], segment[k]) for k in range(n)):
            return i + n - 1
    return None


def candidate_stopovers(status):
    """Halte des Laufwegs strikt vor dem Ausstieg, mit stopoverId.

    Leere Liste, wenn der Ausstieg im Laufweg nicht auflösbar ist.
    """
    if not isinstance(status, dict):
        return []
    stops = _trip_stopovers(status)
    if len(stops) < 2:
        return []
    dest = (status.get("checkin") or {}).get("destination") or {}
    segment = traveled_stopovers(status)
    if not segment or not _same_stop(segment[-1], dest):
        return []
    i_end = _end_index(stops, segment)
    if i_end is None or i_end <= 0:
        return []
    out = []
    for stop in stops[:i_end]:
        if stop.get("id") is None or stop.get("stopoverId") is None:
            continue
        out.append(stop)
    return out


def _chosen_stop(status, so_id):
    so_id = stopover_id(so_id)
    if so_id is None:
        return None
    api = ((status.get("checkin") or {}).get("origin") or {}).get("stopoverId")
    if stopover_id(api) == so_id:
        return None
    for stop in candidate_stopovers(status):
        if stopover_id(stop.get("stopoverId")) == so_id:
            return stop
    return None


def preview_status(status, patches):
    """Kopie mit ersetztem Einstieg, ohne neue Fahrzeit oder Kilometer.

    Ohne wirksamen Patch dasselbe Objekt. Mutiert die Eingabe nicht.
    """
    if not isinstance(status, dict):
        return status
    stop = _chosen_stop(status, override_stopover(patches, status))
    if stop is None:
        return status
    return _with_origin(status, stop)


def _with_origin(status, origin_stop):
    item = dict(status)
    checkin = dict(item.get("checkin") or {})
    checkin["origin"] = copy.deepcopy(origin_stop)
    checkin["manualDeparture"] = None
    item["checkin"] = checkin
    return item


def _int_or_none(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _positive_int(value):
    n = _int_or_none(value)
    if n is None or n <= 0:
        return None
    return n


def _median_int(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return int(ordered[mid])
    return int(round((ordered[mid - 1] + ordered[mid]) / 2.0))


def _kept_stops(segment):
    return [
        s for s in (segment or [])
        if isinstance(s, dict) and not s.get("cancelled") and s.get("id") is not None
    ]


def _pairs_of(segment):
    kept = _kept_stops(segment)
    out = []
    for a, b in zip(kept, kept[1:]):
        a_id, b_id = a.get("id"), b.get("id")
        if a_id is None or b_id is None or a_id == b_id:
            continue
        out.append((a_id, b_id))
    return out


def _path_ids(segment):
    return tuple(s.get("id") for s in _kept_stops(segment))


def _stop_key(stop):
    so = stop.get("stopoverId")
    if so is not None:
        return ("so", stopover_id(so))
    return ("st", stop.get("id"))


def _split_pairs(old_segment, new_segment):
    """(wegfallendes Präfix, zusätzliche Kanten) als Stationspaare.

    Späterer Einstieg: Präfix bis zum neuen Halt. Früherer Einstieg: Kanten
    vor dem bisherigen Einstieg. Sonst zwei leere Listen.
    """
    old_kept = _kept_stops(old_segment)
    new_kept = _kept_stops(new_segment)
    old_keys = [_stop_key(s) for s in old_kept]
    new_keys = [_stop_key(s) for s in new_kept]
    if new_keys and new_keys[0] in old_keys:
        i = old_keys.index(new_keys[0])
        return _pairs_of(old_kept[: i + 1]), []
    if old_keys and old_keys[0] in new_keys:
        i = new_keys.index(old_keys[0])
        return [], _pairs_of(new_kept[: i + 1])
    return [], []


def _parse_dt(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _minutes_between(start, end):
    a, b = _parse_dt(start), _parse_dt(end)
    if not a or not b:
        return None
    minutes = int(round((b - a).total_seconds() / 60.0))
    if minutes < 0:
        return None
    return minutes


def _latlon(stop):
    station = stop.get("station") if isinstance(stop.get("station"), dict) else {}
    lat = station.get("latitude")
    lon = station.get("longitude")
    if lat is None or lon is None:
        lat = stop.get("latitude")
        lon = stop.get("longitude")
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _polyline_km(segment):
    """Luftlinie der Haltfolge in km. Entfallene Zwischenhalte zählen nicht."""
    n = len(segment or [])
    stops = []
    for i, stop in enumerate(segment or []):
        if not isinstance(stop, dict):
            continue
        if stop.get("cancelled") and 0 < i < n - 1:
            continue
        stops.append(stop)
    if len(stops) < 2:
        return None
    total = 0.0
    prev = None
    for stop in stops:
        coords = _latlon(stop)
        if coords is None:
            return None
        if prev is not None:
            total += haversine_km(prev[0], prev[1], coords[0], coords[1])
        prev = coords
    return total


def _scale_distance(old_dist, old_segment, new_segment):
    if not old_dist:
        return old_dist or 0
    old_km = _polyline_km(old_segment)
    new_km = _polyline_km(new_segment)
    if not old_km or new_km is None:
        return None
    return int(round(old_dist * new_km / old_km))


def _edge_sum(pairs, edges, part):
    """Summe von part 0 (Meter) oder 1 (Minuten), wenn jede Kante den Wert hat."""
    if not pairs:
        return None
    total = 0
    for pair in pairs:
        measure = edges.get(pair)
        if measure is None or measure[part] is None:
            return None
        total += measure[part]
    return total


def learn_edge_measures(statuses):
    """Kanten- und Haltfolge-Maße aus den API-Abschnitten.

    Eine Fahrt mit genau einer Kante setzt deren Strecke (distance > 0) und
    Fahrzeit. Danach füllt jede Mehrfach-Fahrt mit genau einer fehlenden Kante
    diese per Rest. Mehrere Werte: Median. Der Einstieg ist der der Quelle,
    nicht ein Patch.
    """
    edge_obs = {}
    path_obs = {}
    multi = []
    for status in statuses or []:
        if not isinstance(status, dict):
            continue
        checkin = status.get("checkin") or {}
        dist = _positive_int(checkin.get("distance"))
        dur = _int_or_none(checkin.get("duration"))
        segment = traveled_stopovers(status)
        pairs = _pairs_of(segment)
        path = _path_ids(segment)
        if dist is not None and len(path) >= 2:
            bucket = path_obs.setdefault(path, {"dist": [], "dur": []})
            bucket["dist"].append(dist)
            if dur is not None:
                bucket["dur"].append(dur)
        if len(pairs) == 1 and dist is not None:
            bucket = edge_obs.setdefault(pairs[0], {"dist": [], "dur": []})
            bucket["dist"].append(dist)
            if dur is not None:
                bucket["dur"].append(dur)
        elif len(pairs) >= 2 and dist is not None:
            multi.append((pairs, dist, dur))

    edges = {}
    for key, obs in edge_obs.items():
        edges[key] = (_median_int(obs["dist"]), _median_int(obs["dur"]))

    while True:
        pending = {}
        for pairs, dist, dur in multi:
            unknown = [pair for pair in pairs if pair not in edges]
            if len(unknown) != 1:
                continue
            missing = unknown[0]
            known_dist = 0
            known_dur = 0
            dur_ok = dur is not None
            for pair in pairs:
                if pair == missing:
                    continue
                known = edges.get(pair)
                if known is None:
                    dur_ok = False
                    break
                known_dist += known[0]
                if known[1] is None:
                    dur_ok = False
                elif dur_ok:
                    known_dur += known[1]
            else:
                rest = dist - known_dist
                if rest <= 0:
                    continue
                slot = pending.setdefault(missing, {"dist": [], "dur": []})
                slot["dist"].append(rest)
                if dur_ok:
                    rest_dur = dur - known_dur
                    if rest_dur >= 0:
                        slot["dur"].append(rest_dur)
        if not pending:
            break
        for key, obs in pending.items():
            edges[key] = (_median_int(obs["dist"]), _median_int(obs["dur"]))

    paths = {}
    for key, obs in path_obs.items():
        paths[key] = (_median_int(obs["dist"]), _median_int(obs["dur"]))
    return {"edges": edges, "paths": paths}


def _distance_for(old_dist, old_segment, new_segment, measures):
    edges = (measures or empty_measures()).get("edges") or {}
    paths = (measures or empty_measures()).get("paths") or {}
    new_path = _path_ids(new_segment)
    hit = paths.get(new_path)
    if hit and hit[0] is not None and len(new_path) >= 2:
        return hit[0]
    summed = _edge_sum(_pairs_of(new_segment), edges, 0)
    if summed is not None:
        return summed
    prefix, added = _split_pairs(old_segment, new_segment)
    prefix_sum = _edge_sum(prefix, edges, 0)
    if prefix_sum is not None:
        return max(0, (old_dist or 0) - prefix_sum)
    added_sum = _edge_sum(added, edges, 0)
    if added_sum is not None:
        return (old_dist or 0) + added_sum
    scaled = _scale_distance(old_dist, old_segment, new_segment)
    if scaled is None:
        return old_dist or 0
    return scaled


def _duration_for(old_dur, origin, destination, old_segment, new_segment, measures):
    timed = _minutes_between(
        origin.get("departureReal") or origin.get("departurePlanned"),
        destination.get("arrivalReal") or destination.get("arrivalPlanned"),
    )
    if timed is not None:
        return timed
    edges = (measures or empty_measures()).get("edges") or {}
    summed = _edge_sum(_pairs_of(new_segment), edges, 1)
    if summed is not None:
        return summed
    prefix, added = _split_pairs(old_segment, new_segment)
    prefix_sum = _edge_sum(prefix, edges, 1)
    if prefix_sum is not None and old_dur is not None:
        return max(0, old_dur - prefix_sum)
    added_sum = _edge_sum(added, edges, 1)
    if added_sum is not None:
        return (old_dur or 0) + added_sum
    if old_dur is None:
        return 0
    return old_dur


def _apply_one(status, so_id, measures):
    stop = _chosen_stop(status, so_id)
    if stop is None:
        return status
    old_segment = traveled_stopovers(status)
    item = _with_origin(status, stop)
    checkin = item["checkin"]
    new_segment = traveled_stopovers(item)
    old_dist = _int_or_none(checkin.get("distance")) or 0
    old_dur = _int_or_none(checkin.get("duration"))
    # _with_origin hat distance/duration mitkopiert; origin ist schon der neue Halt.
    checkin["distance"] = _distance_for(
        old_dist, old_segment, new_segment, measures
    )
    checkin["duration"] = _duration_for(
        old_dur, checkin.get("origin") or {}, checkin.get("destination") or {},
        old_segment, new_segment, measures,
    )
    return item


def apply_to_statuses(patches, statuses, measures=None):
    """Kopien mit neuem Einstieg, Fahrzeit und Kilometern. Mutiert nicht.

    `measures` kommt aus `learn_edge_measures` über alle Statuses (vor diesem
    Patch). Fehlt die Tabelle, wird sie aus `statuses` gelernt.
    """
    overrides = (patches or empty_patches()).get("overrides") or {}
    if not overrides or not statuses:
        return statuses
    if measures is None:
        measures = learn_edge_measures(statuses)
    out = []
    for status in statuses:
        if not isinstance(status, dict):
            out.append(status)
            continue
        so_id = overrides.get(status_id(status.get("id")))
        if so_id is None:
            out.append(status)
            continue
        out.append(_apply_one(status, so_id, measures))
    return out
