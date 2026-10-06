#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Betreibernamen vereinheitlichen: `data/operator_replacements.json`.

Ein JSON-Objekt Rohname → kanonischer Name. `download_statuses.py` wendet es
beim Export auf `checkin.operator.name` an. Schlüssel mit führendem `_` sind
Kommentare; der Editor liest und schreibt sie unverändert mit.
"""

import json
import os


def load_document(path):
    """Liest die Datei als {comments: [(k, v)], pairs: [(roh, kanonisch)]}.

    Fehlende Datei → leere Listen. Ungültiges JSON oder falsche Struktur →
    ValueError.
    """
    doc = {"comments": [], "pairs": []}
    if not path or not os.path.isfile(path):
        return doc
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f, object_pairs_hook=list)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Operator-Ersetzungen {path} nicht lesbar: {e}") from e
    if not isinstance(data, list):
        raise ValueError(f"Operator-Ersetzungen {path}: erwartet ein JSON-Objekt.")
    for key, value in data:
        if isinstance(key, str) and key.startswith("_"):
            doc["comments"].append((key, value))
        elif isinstance(key, str) and isinstance(value, str) and value:
            doc["pairs"].append((key, value))
        else:
            raise ValueError(
                f"Operator-Ersetzungen {path}: Wert für {key!r} muss ein "
                "nicht-leerer String sein."
            )
    return doc


def save_document(path, doc):
    """Schreibt Kommentare und Paare atomar. Gibt True bei Erfolg."""
    if not path:
        return False
    payload = {}
    for key, value in doc.get("comments") or []:
        payload[key] = value
    for raw, canonical in doc.get("pairs") or []:
        payload[raw] = canonical
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
