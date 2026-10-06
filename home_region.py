#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokale Heimatregion: Operator-Namen, die der Dashboard-Filter einschließt.

Kein API-Write. Die Datei (Default `data/home_region.json`) wird beim
Dashboard-Bau gelesen. Quelle `statuses.json` bleibt unangetastet.
"""

import json
import os


def operator_name(status):
    """`checkin.operator.name`, sonst leer."""
    if not isinstance(status, dict):
        return ""
    checkin = status.get("checkin") or {}
    if not isinstance(checkin, dict):
        return ""
    operator = checkin.get("operator") or {}
    if not isinstance(operator, dict):
        return ""
    name = operator.get("name")
    if not isinstance(name, str):
        return ""
    return name


def _sort_key(name):
    # Leerer Operator („ohne Operator“) steht oben.
    return (name != "", name.casefold())


def load_operators(path):
    """Liest home_region.json. Fehlende/ungültige Datei → leere Liste.

    Schlüssel mit führendem `_` werden ignoriert (nur `operators` zählt).
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("operators")
    if not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for item in raw:
        if not isinstance(item, str) or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def save_operators(path, operators):
    """Schreibt die Namensliste atomar. Gibt True bei Erfolg."""
    if not path:
        return False
    names = []
    seen = set()
    for item in operators or []:
        if not isinstance(item, str) or item in seen:
            continue
        seen.add(item)
        names.append(item)
    names.sort(key=_sort_key)
    parent = os.path.dirname(path)
    payload = {"operators": names}
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


def collect_operator_names(statuses, saved=None):
    """Alle Namen aus den Statuses plus bereits gespeicherte, sortiert."""
    names = set()
    for status in statuses or []:
        names.add(operator_name(status))
    for name in saved or []:
        if isinstance(name, str):
            names.add(name)
    return sorted(names, key=_sort_key)


def filter_statuses(statuses, operators):
    """Statuses, deren Operator in der Liste steht."""
    wanted = {n for n in (operators or []) if isinstance(n, str)}
    if not wanted:
        return []
    return [s for s in statuses or [] if operator_name(s) in wanted]
