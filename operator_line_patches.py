#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokale Operator-Patches je Linie.

Sonderfälle, in denen eine Linie unter dem falschen Operator steht. Die
Regeln liegen in `data/operator_line_patches.json`, der Editor bearbeitet sie auf
der Seite „Betreiber je Linie“. Sie gelten beim Dashboard-Bau nur auf Kopien;
`statuses.json` bleibt unverändert.
"""

import json
import os


def load_rules(path):
    """Liest die Regelliste. Fehlende Datei → [].

    Jede Regel ist (line, operator, name): `checkin.lineName` und der
    aktuelle Operator-Name werden durch `name` ersetzt.
    """
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(
            f"Operator-Linien-Patches {path} nicht lesbar: {e}"
        ) from e
    if not isinstance(data, dict):
        raise ValueError(
            f"Operator-Linien-Patches {path}: erwartet ein JSON-Objekt."
        )
    raw = data.get("rules")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(
            f"Operator-Linien-Patches {path}: 'rules' muss eine Liste sein."
        )
    rules = []
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(
                f"Operator-Linien-Patches {path}: Regel {index + 1} "
                f"ist kein Objekt."
            )
        line = row.get("line")
        operator = row.get("operator")
        name = row.get("name")
        if (
            not isinstance(line, str) or not line.strip()
            or not isinstance(operator, str) or not operator.strip()
            or not isinstance(name, str) or not name.strip()
        ):
            raise ValueError(
                f"Operator-Linien-Patches {path}: Regel {index + 1} braucht "
                f"nicht-leere Strings line, operator und name."
            )
        rules.append((line.strip(), operator.strip(), name.strip()))
    return rules


def _line_and_operator(status):
    if not isinstance(status, dict):
        return "", ""
    checkin = status.get("checkin")
    if not isinstance(checkin, dict):
        return "", ""
    line = checkin.get("lineName")
    if not isinstance(line, str):
        line = ""
    operator = checkin.get("operator")
    name = ""
    if isinstance(operator, dict):
        raw = operator.get("name")
        if isinstance(raw, str):
            name = raw
    return line.strip(), name.strip()


def apply_to_statuses(rules, statuses):
    """Kopien mit ersetztem Operator, wenn Linie und alter Name passen.

    Mutiert die übergebenen Statuses nicht. Gibt (liste, anzahl) zurück.
    Die erste passende Regel gewinnt.
    """
    rules = list(rules or [])
    if not rules or not statuses:
        return list(statuses or []), 0
    out = []
    changed = 0
    for status in statuses:
        line, operator = _line_and_operator(status)
        new_name = None
        for rule_line, rule_op, rule_name in rules:
            if line == rule_line and operator == rule_op and operator != rule_name:
                new_name = rule_name
                break
        if not new_name:
            out.append(status)
            continue
        item = dict(status)
        checkin = dict(item.get("checkin") or {})
        op = dict(checkin.get("operator") or {})
        op["name"] = new_name
        checkin["operator"] = op
        item["checkin"] = checkin
        out.append(item)
        changed += 1
    return out, changed


def load_document(path):
    """{comments: [(k, v)], rules: [(line, operator, name)]} für den Editor."""
    rules = load_rules(path)
    comments = []
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        comments = [
            (k, v) for k, v in data.items()
            if isinstance(k, str) and k.startswith("_")
        ]
    return {"comments": comments, "rules": rules}


def save_rules(path, rules, comments=()):
    """Schreibt Kommentare und Regeln atomar. Gibt True bei Erfolg."""
    if not path:
        return False
    payload = {}
    for key, value in comments or ():
        payload[key] = value
    payload["rules"] = [
        {"line": line, "operator": operator, "name": name}
        for line, operator, name in rules
    ]
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
