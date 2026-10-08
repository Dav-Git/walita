#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokale Linien-Patches: Linienname je Fahrt und Zusammenführen von Linien.

Die Datei (Default `data/line_patches.json`) ist die Quelle der Wahrheit.
Der Editor spiegelt den effektiven Namen als Tag `walita:line` nach
Träwelling und liest ihn beim Laden zurück. Beim Dashboard-Bau gilt der Tag
nur, wenn die Datei für die Fahrt nichts festlegt. `statuses.json` bleibt
unverändert; alles wirkt auf Kopien.
"""

import json
import os

KEY_LINE = "walita:line"


def empty_patches():
    # cleared: zurückgesetzte Fahrten, deren alter walita:line-Tag nicht mehr
    # zählt, bis er bei Träwelling gelöscht ist.
    return {"overrides": {}, "merges": {}, "cleared": set(), "comments": []}


def status_id(value):
    """Normalisiert eine Status-ID zu int, sonst unverändert."""
    if isinstance(value, bool) or value is None:
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _clean(value):
    return value.strip() if isinstance(value, str) else ""


def _line_key(raw):
    """(line, operator) aus {"line", "operator"}; None ohne Liniennamen."""
    if not isinstance(raw, dict):
        return None
    line = _clean(raw.get("line"))
    if not line:
        return None
    return line, _clean(raw.get("operator"))


def load_patches(path):
    """Liest line_patches.json. Fehlende Datei → leer, ungültige → ValueError."""
    if not path or not os.path.isfile(path):
        return empty_patches()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"Linien-Patches {path} nicht lesbar: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"Linien-Patches {path}: erwartet ein JSON-Objekt.")
    out = empty_patches()
    out["comments"] = [
        (k, v) for k, v in data.items() if isinstance(k, str) and k.startswith("_")
    ]
    raw_ov = data.get("overrides") or {}
    if not isinstance(raw_ov, dict):
        raise ValueError(f"Linien-Patches {path}: 'overrides' muss ein Objekt sein.")
    for sid, name in raw_ov.items():
        if isinstance(sid, str) and sid.startswith("_"):
            continue
        name = _clean(name)
        if name:
            out["overrides"][status_id(sid)] = name
    raw_m = data.get("merges") or []
    if not isinstance(raw_m, list):
        raise ValueError(f"Linien-Patches {path}: 'merges' muss eine Liste sein.")
    for row in raw_m:
        if not isinstance(row, dict):
            continue
        src, dest = _line_key(row.get("from")), _line_key(row.get("to"))
        if src and dest and src != dest:
            out["merges"][src] = dest
    raw_c = data.get("cleared") or []
    if not isinstance(raw_c, list):
        raise ValueError(f"Linien-Patches {path}: 'cleared' muss eine Liste sein.")
    out["cleared"] = {status_id(sid) for sid in raw_c if status_id(sid) is not None}
    return out


def dumps_patches(patches):
    """Interne Form → JSON-Objekt; Kommentare zuerst, alles sortiert."""
    payload = {}
    for key, value in patches.get("comments") or ():
        payload[key] = value
    payload["overrides"] = {
        str(sid): name
        for sid, name in sorted((patches.get("overrides") or {}).items(),
                                key=lambda kv: str(kv[0]))
    }
    payload["merges"] = [
        {"from": {"line": s[0], "operator": s[1]},
         "to": {"line": d[0], "operator": d[1]}}
        for s, d in sorted((patches.get("merges") or {}).items())
    ]
    cleared = patches.get("cleared") or ()
    if cleared:
        payload["cleared"] = sorted(cleared, key=str)
    return payload


def save_patches(path, patches):
    """Schreibt atomar. Gibt True bei Erfolg."""
    if not path:
        return False
    parent = os.path.dirname(path)
    tmp = path + ".tmp"
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dumps_patches(patches), f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True


def set_override(patches, sid, name):
    sid, name = status_id(sid), _clean(name)
    if sid is None or not name:
        return False
    patches.setdefault("overrides", {})[sid] = name
    patches.setdefault("cleared", set()).discard(sid)
    return True


def clear_override(patches, sid):
    (patches.get("overrides") or {}).pop(status_id(sid), None)


def mark_cleared(patches, sid):
    """Alter walita:line-Tag dieser Fahrt zählt nicht mehr (bis er weg ist)."""
    sid = status_id(sid)
    if sid is not None:
        patches.setdefault("cleared", set()).add(sid)


def set_merge(patches, src, dest):
    src = (_clean(src[0]), _clean(src[1]))
    dest = (_clean(dest[0]), _clean(dest[1]))
    if not src[0] or not dest[0] or src == dest:
        return False
    patches.setdefault("merges", {})[src] = dest
    return True


def clear_merge(patches, src):
    (patches.get("merges") or {}).pop((_clean(src[0]), _clean(src[1])), None)


def alias_map(patches):
    """Quelle → endgültiges Ziel. Ketten flach, Zyklen verworfen."""
    raw = dict((patches or {}).get("merges") or {})
    out = {}
    for src in sorted(raw):
        seen = {src}
        cur = raw[src]
        while cur in raw:
            if cur in seen:
                cur = None
                break
            seen.add(cur)
            cur = raw[cur]
        if cur is not None and cur != src:
            out[src] = cur
    return out


def line_and_operator(status):
    checkin = status.get("checkin") if isinstance(status, dict) else None
    if not isinstance(checkin, dict):
        return "", ""
    op = checkin.get("operator")
    return (
        _clean(checkin.get("lineName")),
        _clean(op.get("name") if isinstance(op, dict) else None),
    )


def source_key(status, patches):
    """(Linie, Betreiber) nach Override, vor Merge und ohne Tag."""
    line, op = line_and_operator(status)
    sid = status_id(status.get("id")) if isinstance(status, dict) else None
    return ((patches or {}).get("overrides") or {}).get(sid, line), op


def tag_line(status):
    for t in (status or {}).get("tags") or []:
        if isinstance(t, dict) and _clean(t.get("key")) == KEY_LINE:
            return _clean(t.get("value"))
    return ""


def effective_line(status, patches, aliases=None, use_tag=True):
    """(Linie, Betreiber, Quelle) nach Override, Merge und ggf. Tag."""
    patches = patches or empty_patches()
    if aliases is None:
        aliases = alias_map(patches)
    line, op = line_and_operator(status)
    source = ""
    sid = status_id(status.get("id")) if isinstance(status, dict) else None
    ov = (patches.get("overrides") or {}).get(sid) if sid is not None else None
    if ov:
        line, source = ov, "override"
    dest = aliases.get((line, op))
    if dest:
        line, op = dest
        source = source or "merge"
    if not source and use_tag and sid not in (patches.get("cleared") or ()):
        tag = tag_line(status)
        if tag and tag != line:
            line, source = tag, "tag"
            line, op = aliases.get((line, op), (line, op))
    return line, op, source


def _with_line(status, line, op):
    item = dict(status)
    checkin = dict(item.get("checkin") or {})
    checkin["lineName"] = line
    raw_op = checkin.get("operator")
    oper = dict(raw_op) if isinstance(raw_op, dict) else {}
    if op or oper:
        oper["name"] = op
        checkin["operator"] = oper
    item["checkin"] = checkin
    return item


def apply_to_statuses(patches, statuses, use_tag=True):
    """Kopien mit effektivem Linienname/Betreiber. Gibt (liste, anzahl)."""
    aliases = alias_map(patches)
    out, n = [], 0
    for s in statuses or []:
        if not isinstance(s, dict):
            out.append(s)
            continue
        line, op, source = effective_line(s, patches, aliases, use_tag)
        if not source or (line, op) == line_and_operator(s):
            out.append(s)
            continue
        out.append(_with_line(s, line, op))
        n += 1
    return out, n


def preview_status(status, patches):
    """Anzeige-Kopie mit effektiver Linie; ohne Änderung dasselbe Objekt."""
    if not isinstance(status, dict):
        return status
    line, op, source = effective_line(status, patches)
    if not source or (line, op) == line_and_operator(status):
        return status
    return _with_line(status, line, op)


def reconcile(patches, statuses):
    """Vergleicht Datei und Tag je Fahrt; mutiert nichts.

    adopt: lokal nichts, Tag weicht vom HAFAS-Namen ab → Tag als Override.
    stage: Override ohne Tag → Tag mit effektivem Namen vormerken;
        zurückgesetzte Fahrt mit Tag → Tag löschen (Wert "").
    conflicts: Override oder Merge und Tag mit anderem Namen.
    uncleared: zurückgesetzte Fahrten ohne Tag → Eintrag in `cleared` entfällt.
    """
    aliases = alias_map(patches)
    cleared = (patches or {}).get("cleared") or set()
    adopt, stage, conflicts, uncleared = {}, {}, [], []
    for s in statuses or []:
        if not isinstance(s, dict):
            continue
        sid = status_id(s.get("id"))
        if sid is None:
            continue
        tag = tag_line(s)
        if sid in cleared:
            if tag:
                stage[sid] = ""
            else:
                uncleared.append(sid)
            continue
        hafas, _op = line_and_operator(s)
        local, _op, source = effective_line(s, patches, aliases, use_tag=False)
        if not source:
            if tag and tag != hafas:
                adopt[sid] = tag
            continue
        if not tag:
            if source == "override":
                stage[sid] = local
            continue
        if tag != local:
            conflicts.append((sid, hafas, local, tag))
    conflicts.sort(key=lambda c: str(c[0]))
    uncleared.sort(key=str)
    return {"adopt": adopt, "stage": stage, "conflicts": conflicts,
            "uncleared": uncleared}


def line_counts(statuses, patches):
    """{(Linie, Betreiber): Fahrten} nach Override, vor Merge, ohne Tag."""
    out = {}
    for s in statuses or []:
        if not isinstance(s, dict):
            continue
        line, op = source_key(s, patches)
        if line:
            out[(line, op)] = out.get((line, op), 0) + 1
    return out
