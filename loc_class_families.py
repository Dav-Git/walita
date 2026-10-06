#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Baureihenfamilien: `data/loc_class_families.txt`.

JSON-Objekt-Syntax, in der dieselbe Baureihe mehrfach als Schlüssel stehen
darf (eine Baureihe in mehreren Familien). Gelesen als Paarliste, geschrieben
Zeile für Zeile, damit doppelte Schlüssel erhalten bleiben. `_`-Schlüssel
sind Kommentare.
"""

import json
import os


def load_document(path):
    """Liest {comments: [(k, v)], pairs: [(baureihe, familie)]} in Dateireihenfolge."""
    doc = {"comments": [], "pairs": []}
    if not path or not os.path.isfile(path):
        return doc
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f, object_pairs_hook=list)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Baureihenfamilien {path} nicht lesbar: {e}") from e
    if not isinstance(data, list):
        raise ValueError(f"Baureihenfamilien {path}: erwartet ein JSON-Objekt.")
    for key, value in data:
        if isinstance(key, str) and key.startswith("_"):
            doc["comments"].append((key, value))
        elif isinstance(key, str) and key and isinstance(value, str) and value:
            doc["pairs"].append((key, value))
        else:
            raise ValueError(
                f"Baureihenfamilien {path}: ungültiger Eintrag {key!r}: {value!r}."
            )
    return doc


def families(doc):
    """{Familie: [Baureihen]} in Dateireihenfolge, ohne Duplikate je Familie."""
    out = {}
    for loc_class, family in doc.get("pairs") or []:
        members = out.setdefault(family, [])
        if loc_class not in members:
            members.append(loc_class)
    return out


def save_document(path, doc):
    """Schreibt atomar; jedes Paar eine Zeile, doppelte Schlüssel bleiben."""
    if not path:
        return False
    items = list(doc.get("comments") or []) + list(doc.get("pairs") or [])
    lines = ["{"]
    for i, (key, value) in enumerate(items):
        sep = "," if i < len(items) - 1 else ""
        lines.append(
            "  %s: %s%s" % (
                json.dumps(key, ensure_ascii=False),
                json.dumps(value, ensure_ascii=False),
                sep,
            )
        )
    lines.append("}")
    parent = os.path.dirname(path)
    tmp = path + ".tmp"
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    return True
