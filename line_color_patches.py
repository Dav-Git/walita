#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokale Linienfarben-Patches: routeColor je Status überschreiben.

Kein API-Write. Die Datei (Default `data/line_color_patches.json`) setzt
`checkin.routeColor` / `routeTextColor` beim Dashboard-Bau. Quelle
`statuses.json` bleibt unangetastet.
"""

import json
import os


def empty_patches():
    return {"overrides": {}}


def status_id(value):
    """Normalisiert eine Status-ID zu int, sonst unverändert."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def normalize_hex(value):
    """6-stelliger Kleinbuchstaben-Hex ohne #, sonst None."""
    if value is None:
        return None
    s = str(value).strip().lstrip("#")
    if len(s) == 3 and all(c in "0123456789abcdefABCDEF" for c in s):
        s = "".join(c * 2 for c in s)
    if len(s) != 6 or any(c not in "0123456789abcdefABCDEF" for c in s):
        return None
    return s.lower()


def contrast_text(bg):
    """Schwarz oder Weiß als Textfarbe zum Hintergrund (YIQ)."""
    n = normalize_hex(bg)
    if not n:
        return "000000"
    r, g, b = int(n[0:2], 16), int(n[2:4], 16), int(n[4:6], 16)
    y = (r * 299 + g * 587 + b * 114) / 1000
    return "000000" if y >= 150 else "ffffff"


def to_css_pair(bg, fg=None):
    """[bg, fg] mit führendem #, oder None wenn bg ungültig."""
    bg = normalize_hex(bg)
    if not bg:
        return None
    fg = normalize_hex(fg) or contrast_text(bg)
    return ["#" + bg, "#" + fg]


def load_patches(path):
    """Liest line_color_patches.json. Fehlende/ungültige Datei → leere Struktur.

    overrides: status_id -> (routeColor, routeTextColor)  (6 Hex-Ziffern)
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
        bg = normalize_hex(row.get("routeColor"))
        if sid is None or bg is None:
            continue
        fg = normalize_hex(row.get("routeTextColor")) or contrast_text(bg)
        overrides[sid] = (bg, fg)
    return {"overrides": overrides}


def dumps_patches(patches):
    """Serialisiert die interne Map-Form zurück ins JSON-Objekt."""
    overrides = []
    for sid, colors in sorted(
        (patches.get("overrides") or {}).items(), key=lambda kv: kv[0]
    ):
        bg, fg = colors
        overrides.append({
            "statusId": sid,
            "routeColor": bg,
            "routeTextColor": fg,
        })
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


def set_color(patches, sid, bg, fg=None):
    sid = status_id(sid)
    bg = normalize_hex(bg)
    if sid is None or bg is None:
        return False
    fg = normalize_hex(fg) or contrast_text(bg)
    patches.setdefault("overrides", {})[sid] = (bg, fg)
    return True


def clear_color(patches, sid):
    (patches.get("overrides") or {}).pop(status_id(sid), None)


def effective_colors(status, patches):
    """(bg, fg, patched). Hex ohne #, oder (None, None, False)."""
    if not isinstance(status, dict):
        return None, None, False
    sid = status_id(status.get("id"))
    ov = (patches or empty_patches()).get("overrides") or {}
    if sid is not None and sid in ov:
        bg, fg = ov[sid]
        return bg, fg, True
    checkin = status.get("checkin") or {}
    bg = normalize_hex(checkin.get("routeColor"))
    if not bg:
        return None, None, False
    fg = normalize_hex(checkin.get("routeTextColor")) or contrast_text(bg)
    return bg, fg, False


def apply_to_statuses(patches, statuses):
    """Kopie der Statuses mit überschriebenem checkin.routeColor. Mutiert nicht."""
    overrides = (patches or empty_patches()).get("overrides") or {}
    if not overrides or not statuses:
        return statuses
    out = []
    for status in statuses:
        if not isinstance(status, dict):
            out.append(status)
            continue
        sid = status_id(status.get("id"))
        colors = overrides.get(sid) if sid is not None else None
        if not colors:
            out.append(status)
            continue
        item = dict(status)
        checkin = dict(item.get("checkin") or {})
        checkin["routeColor"] = colors[0]
        checkin["routeTextColor"] = colors[1]
        item["checkin"] = checkin
        out.append(item)
    return out
