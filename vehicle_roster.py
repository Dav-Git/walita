#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokaler Fuhrpark: konkrete Fahrzeugnummern je Baureihe.

Kein API-Write. Die Datei (Default `data/vehicle_roster.json`) wird beim
Dashboard-Bau gelesen. Quelle `statuses.json` bleibt unangetastet.
"""

import datetime
import json
import os


RANGE_LIMIT = 5000
_DATE_FMT = "%Y-%m-%d"


def normalize_number(number):
    """Kanonische Nummer. Reine Ziffernketten ohne führende Nullen."""
    if isinstance(number, bool):
        return ""
    if isinstance(number, int):
        return str(number)
    if not isinstance(number, str):
        return ""
    text = number.strip()
    if not text:
        return ""
    if text.isdigit():
        return str(int(text))
    return text


def number_key(number):
    """Vergleichsschlüssel. Reine Ziffernketten als Integer (`228` = `0228`)."""
    text = normalize_number(number)
    if text.isdigit():
        return ("n", int(text))
    return ("s", text)


def parse_withdrawn_on(value):
    """`YYYY-MM-DD` oder None. Leer und ungültig werden verworfen."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        datetime.datetime.strptime(text, _DATE_FMT)
    except ValueError:
        return None
    return text


def _clean_entry(raw):
    if isinstance(raw, str):
        raw = {"number": raw}
    if not isinstance(raw, dict):
        return None
    number = normalize_number(raw.get("number"))
    if not number:
        return None
    withdrawn = bool(raw.get("withdrawn"))
    entry = {"number": number, "withdrawn": withdrawn}
    if withdrawn:
        withdrawn_on = parse_withdrawn_on(raw.get("withdrawnOn"))
        if withdrawn_on:
            entry["withdrawnOn"] = withdrawn_on
    return entry


def _sort_entries(entries):
    entries.sort(key=lambda entry: number_key(entry["number"]))
    return entries


def _clean_types(types):
    """`{Baureihe: [Einträge]}` ohne leere Typen, Nummern eindeutig und sortiert."""
    if not isinstance(types, dict):
        return {}
    out = {}
    for name, raw_list in types.items():
        if not isinstance(name, str) or name.startswith("_"):
            continue
        class_name = name.strip()
        if not class_name or not isinstance(raw_list, list):
            continue
        by_key = {}
        for raw in raw_list:
            entry = _clean_entry(raw)
            if entry is None:
                continue
            key = number_key(entry["number"])
            if key not in by_key:
                by_key[key] = entry
        if not by_key:
            continue
        out[class_name] = _sort_entries(list(by_key.values()))
    return out


def load_roster(path):
    """Liest vehicle_roster.json. Fehlende/ungültige Datei → `{types: {}}`.

    Schlüssel mit führendem `_` werden ignoriert (nur `types` zählt).
    `withdrawnOn` bleibt nur bei ausgemustert und gültigem Datum.
    """
    empty = {"types": {}}
    if not path or not os.path.isfile(path):
        return empty
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    return {"types": _clean_types(data.get("types"))}


def save_roster(path, roster):
    """Schreibt den Fuhrpark atomar. `roster` ist `{types: ...}` oder die Map.

    Gibt True bei Erfolg. Leere Typen fallen weg.
    """
    if not path:
        return False
    types = roster.get("types") if isinstance(roster, dict) and "types" in roster else roster
    payload = {"types": _clean_types(types)}
    parent = os.path.dirname(path)
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


def expand_range(start, end, step):
    """Inklusive Ganzzahl-Spanne. `301, 311, 2` → `301, 303, …, 311`.

    Wirft ValueError mit deutscher Meldung.
    """
    if isinstance(start, bool) or isinstance(end, bool) or isinstance(step, bool):
        raise ValueError("Von, Bis und Schrittweite müssen ganze Zahlen sein.")
    if not all(isinstance(v, int) for v in (start, end, step)):
        raise ValueError("Von, Bis und Schrittweite müssen ganze Zahlen sein.")
    if step < 1:
        raise ValueError("Schrittweite muss mindestens 1 sein.")
    if start < 0 or end < 0:
        raise ValueError("Von und Bis dürfen nicht negativ sein.")
    if start > end:
        raise ValueError("Von darf nicht größer als Bis sein.")
    count = (end - start) // step + 1
    if count > RANGE_LIMIT:
        raise ValueError(
            "Höchstens %d Nummern auf einmal (diese Spanne hätte %d)."
            % (RANGE_LIMIT, count)
        )
    return [str(n) for n in range(start, end + 1, step)]


def add_numbers(existing, numbers):
    """Hängt neue Nummern an. Vorhandene behalten `withdrawn` und `withdrawnOn`."""
    cleaned = _clean_types({"keep": list(existing or [])}).get("keep", [])
    by_key = {number_key(entry["number"]): entry for entry in cleaned}
    for number in numbers or []:
        entry = _clean_entry(number if isinstance(number, dict) else {"number": number})
        if entry is None:
            continue
        key = number_key(entry["number"])
        if key not in by_key:
            by_key[key] = {"number": entry["number"], "withdrawn": False}
    return _sort_entries(list(by_key.values()))


def loc_class_name(status):
    """`trwl:locomotive_class`, sonst leer."""
    if not isinstance(status, dict):
        return ""
    for tag in status.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        if tag.get("key") == "trwl:locomotive_class" and tag.get("value"):
            value = tag.get("value")
            if isinstance(value, str):
                return value.strip()
    return ""


def collect_loc_classes(statuses, saved=None):
    """Baureihen aus den Statuses plus bereits gespeicherte Typen, sortiert."""
    names = set()
    for status in statuses or []:
        name = loc_class_name(status)
        if name:
            names.add(name)
    for name in saved or []:
        if isinstance(name, str) and name.strip() and not name.startswith("_"):
            names.add(name.strip())
    return sorted(names, key=lambda name: name.casefold())
