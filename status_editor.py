#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokaler Tag-Editor: Tags und Status-Text nach Träwelling synchronisieren.

Die Fahrtliste zeigt Baureihe und Fahrzeugnummer direkt; Zellen werden lokal
gestagt. Checkboxen „dubi start“ und „dubi ende“ (Durchbindung) setzen die
gleichnamigen Tags: Fahrtbeginn kein Einstieg, Fahrtende kein Ausstieg.
Speichern schreibt den Diff live auf den Server. Laufweg/`trip` bleibt unangetastet. Linienfarbe, Einstieg,
Heimatregion und Fuhrpark sind lokale Overlays (kein API-Write).

Auth wie der Export, aber mit Scope `write-statuses` zusätzlich zu
`read-statuses`.

    python3 status_editor.py
    python3 status_editor.py --login
    python3 walita.py --edit
"""

import argparse
import datetime
import json
import os
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import colorchooser, font as tkfont
from tkinter import messagebox, ttk
import urllib.parse

import auth
import boarding_patches as bp
import build_dashboard
import download_statuses as dl
import edge_patches as ep
import home_region as hr
import line_color_patches as lcp
import vehicle_roster as vr
import station_patches as sp
from version import __version__

EDITOR_SCOPES = "read-statuses write-statuses"
BODY_MAX = 280

KEY_LOC = "trwl:locomotive_class"
KEY_VEH = "trwl:vehicle_number"
TABLE_TAG_KEYS = (KEY_LOC, KEY_VEH)
TABLE_TAG_SET = frozenset(TABLE_TAG_KEYS)

TRIP_COLS = ("date", "line", "origin", "dest", "loc", "veh")
TRIP_HEADINGS = {
    "date": "Datum", "line": "Linie", "origin": "Von", "dest": "Nach",
    "loc": "Baureihe", "veh": "Fahrzeugnummer",
}
EDIT_COLS = ("loc", "veh")
COL_TO_KEY = {"loc": KEY_LOC, "veh": KEY_VEH}
EDGE_KIND_LABEL = {"override": "Fahrt", "default": "Standard", "": "—"}

TAG_KEY_SUGGESTIONS = (
    "trwl:seat",
    "trwl:wagon",
    "trwl:wagon_class",
    "trwl:travel_class",
    "trwl:journey_number",
    "trwl:ticket",
    "trwl:price",
    "trwl:role",
    "trwl:passenger_rights",
    "trwl:social_status",
    KEY_VEH,
    KEY_LOC,
)

VISIBILITY_CHOICES = (
    (0, "Öffentlich"),
    (1, "Ungelistet"),
    (2, "Follower"),
    (3, "Privat"),
    (4, "Angemeldet"),
    (5, "Vertrauenswürdig"),
)
VISIBILITY_LABEL = {n: label for n, label in VISIBILITY_CHOICES}
VISIBILITY_BY_LABEL = {label: n for n, label in VISIBILITY_CHOICES}


def log(msg):
    """Fortschritt auf stderr (GUI-Fehler laufen über die Statuszeile)."""
    print(msg, file=sys.stderr, flush=True)


def _enable_dpi_awareness():
    """Schärfere Darstellung unter Windows, vor dem ersten Tk()-Aufruf."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def format_api_error(exc):
    """Macht ApiError-Bodies (Laravel-JSON) in der GUI lesbar."""
    if not isinstance(exc, dl.ApiError):
        return str(exc)
    text = exc.body or ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        parts = []
        msg = parsed.get("message") or parsed.get("error")
        if isinstance(msg, str) and msg:
            parts.append(msg)
        elif isinstance(msg, dict):
            parts.append(json.dumps(msg, ensure_ascii=False))
        errors = parsed.get("errors") or parsed.get("data")
        if isinstance(errors, dict):
            for key, val in errors.items():
                if isinstance(val, (list, tuple)):
                    val = "; ".join(str(v) for v in val)
                parts.append(f"{key}: {val}")
        elif isinstance(errors, str):
            parts.append(errors)
        if parts:
            prefix = f"HTTP {exc.code}: " if exc.code else ""
            return prefix + " | ".join(parts)
    return str(exc)


def _station_name(stop):
    if not isinstance(stop, dict):
        return "?"
    name = stop.get("name")
    if name:
        return name
    station = stop.get("station") or {}
    return station.get("name") or "?"


def _checkin_bits(status):
    checkin = status.get("checkin") or {}
    origin = checkin.get("origin") or {}
    dest = checkin.get("destination") or {}
    line = checkin.get("lineName") or checkin.get("number") or "?"
    dep = origin.get("departure") or origin.get("departurePlanned") or ""
    arr = dest.get("arrival") or dest.get("arrivalPlanned") or ""
    return {
        "line": line,
        "origin": _station_name(origin),
        "dest": _station_name(dest),
        "dep": dep,
        "arr": arr,
        "date": dl.status_date(status) or "",
    }


def _fmt_when(ts):
    """ISO-Zeit kompakt als 'YYYY-MM-DD HH:MM' (ohne Zeitzonen-Umrechnung)."""
    if not isinstance(ts, str) or len(ts) < 16:
        return ts or "—"
    return ts[:10] + " " + ts[11:16]


def _boarding_label(stop, api_so):
    """Eine Zeile im Einstiegs-Dialog: Zeit, Name, Markierung des API-Halts."""
    dep = stop.get("departureReal") or stop.get("departurePlanned") or ""
    name = _station_name(stop)
    mark = ""
    if bp.stopover_id(stop.get("stopoverId")) == api_so:
        mark = "  (Träwelling)"
    return f"{_fmt_when(dep)}  {name}{mark}"


def _trip_short(status):
    """Eine Zeile für die Übertragungsliste."""
    bits = _checkin_bits(status)
    return f"{bits['date']}  {bits['line']}  {bits['origin']} → {bits['dest']}"


def _norm_tag(tag):
    vis = tag.get("visibility")
    try:
        vis = int(vis)
    except (TypeError, ValueError):
        vis = 0
    return {
        "key": (tag.get("key") or "").strip(),
        "value": "" if tag.get("value") is None else str(tag.get("value")),
        "visibility": vis,
    }


def _tags_tuple(tags):
    return tuple(
        (t["key"], t["value"], t["visibility"])
        for t in (_norm_tag(t) for t in (tags or []))
        if t["key"]
    )


def _tag_value(status, key):
    for t in status.get("tags") or []:
        if (t.get("key") or "").strip() == key:
            return "" if t.get("value") is None else str(t.get("value"))
    return ""


def _dubi_kind(tag):
    """``start``, ``ende`` oder None. Dieselben Regeln wie ``dubi_flags``."""
    start, ende = build_dashboard.dubi_flags([tag])
    if start:
        return "start"
    if ende:
        return "ende"
    return None


def _new_dubi_tag(kind):
    """Kanonisches Tag für eine neu gesetzte Checkbox."""
    return {"key": "dubi=" + kind, "value": kind, "visibility": 0}


def apply_dubi_checks(tags, start_on, ende_on, original=None):
    """Passt dubi-Tags an die Checkboxen an.

    Bleibt eine Checkbox an, bleiben vorhandene Tags stehen — auch
    ``dubi`` mit Wert ``start``/``ende``. Ein wieder eingeschaltetes Flag
    setzt die Tags aus ``original`` zurück, sonst ``dubi=start`` bzw.
    ``dubi=ende``. Ausgeschaltete Flags verschwinden.
    """
    kept = []
    have = set()
    for t in tags or []:
        kind = _dubi_kind(t)
        if kind == "start" and not start_on:
            continue
        if kind == "ende" and not ende_on:
            continue
        kept.append(t)
        if kind:
            have.add(kind)
    for kind, on in (("start", start_on), ("ende", ende_on)):
        if not on or kind in have:
            continue
        restored = [
            _norm_tag(t) for t in (original or []) if _dubi_kind(t) == kind
        ]
        if restored:
            kept.extend(restored)
        else:
            kept.append(_new_dubi_tag(kind))
    return kept


def _order_like_original(tags, original):
    """Schlüssel wie in ``original``, danach neue Schlüssel in ``tags``-Folge."""
    by_key = {}
    seq = []
    for t in tags or []:
        n = _norm_tag(t)
        if not n["key"] or n["key"] in by_key:
            continue
        by_key[n["key"]] = n
        seq.append(n["key"])
    merged = []
    used = set()
    for t in original or []:
        key = _norm_tag(t)["key"]
        if key in by_key and key not in used:
            merged.append(by_key[key])
            used.add(key)
    for key in seq:
        if key not in used:
            merged.append(by_key[key])
            used.add(key)
    return merged


def set_table_tag(status, key, value):
    """Setzt oder entfernt einen Tabellen-Tag (leerer Wert = löschen)."""
    value = (value or "").strip()
    tags = [_norm_tag(t) for t in (status.get("tags") or []) if _norm_tag(t)["key"]]
    idx = next((i for i, t in enumerate(tags) if t["key"] == key), None)
    if not value:
        if idx is not None:
            tags.pop(idx)
    elif idx is not None:
        tags[idx]["value"] = value
    else:
        tags.append({"key": key, "value": value, "visibility": 0})
    status["tags"] = tags


def _snapshot_status(status):
    return (status.get("body") or "", _tags_tuple(status.get("tags") or []))


def _trip_values(status):
    bits = _checkin_bits(status)
    return (
        bits["date"], bits["line"], bits["origin"], bits["dest"],
        _tag_value(status, KEY_LOC), _tag_value(status, KEY_VEH),
    )


def _search_blob(status):
    bits = _checkin_bits(status)
    parts = [
        bits["date"], bits["line"], bits["origin"], bits["dest"],
        _tag_value(status, KEY_LOC), _tag_value(status, KEY_VEH),
        status.get("body") or "",
    ]
    for t in status.get("tags") or []:
        parts.append(t.get("key") or "")
        parts.append("" if t.get("value") is None else str(t.get("value")))
    return " ".join(parts).lower()


def load_local_statuses(path):
    """Liest statuses.json oder gibt None zurück (fehlt/unlesbar)."""
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log(f"Lokale Statuses {path} nicht lesbar ({e}).")
        return None
    if not isinstance(data, list):
        log(f"Lokale Statuses {path} sind kein JSON-Array.")
        return None
    return data


def merge_local_body_tags_many(path, updates):
    """Schreibt body/tags mehrerer IDs in statuses.json; trip bleibt.

    `updates` ist {status_id: (body, tags)}. Gibt die Zahl der Treffer zurück.
    Fehlt die Datei, ist das ein No-Op (0).
    """
    if not path or not os.path.isfile(path) or not updates:
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise OSError(f"{path} nicht lesbar: {e}") from e
    if not isinstance(data, list):
        raise OSError(f"{path} enthält kein JSON-Array.")
    n = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        if sid in updates:
            body, tags = updates[sid]
            item["body"] = body
            item["tags"] = tags
            n += 1
    if not n:
        return 0
    dl.ensure_parent_dir(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return n


def fetch_status(token, status_id):
    """GET /status/{id} → StatusResource (das `data`-Objekt)."""
    payload = dl.api_request("GET", f"/status/{int(status_id)}", token)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise dl.ApiError(None, f"Unerwartete Antwort für Status {status_id}")
    return data


def tag_path(status_id, key):
    encoded = urllib.parse.quote(str(key), safe="")
    return f"/status/{int(status_id)}/tags/{encoded}"


def apply_status_edits(token, status_id, new_body, new_tags):
    """Live: body per PUT, Tags per Diff (POST/PUT/DELETE). Gibt den Server-Status zurück."""
    current = fetch_status(token, status_id)
    old_body = current.get("body") or ""
    if (new_body or "") != (old_body or ""):
        payload = dl.api_request(
            "PUT", f"/status/{int(status_id)}", token,
            json_body={"body": new_body if new_body else None},
        )
        if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            current = payload["data"]

    server = {
        t["key"]: t
        for t in (_norm_tag(t) for t in (current.get("tags") or []))
        if t["key"]
    }
    form = {
        t["key"]: t
        for t in (_norm_tag(t) for t in new_tags)
        if t["key"]
    }

    for key in list(server):
        if key not in form:
            dl.api_request("DELETE", tag_path(status_id, key), token)
    for key, tag in form.items():
        body = {
            "key": tag["key"],
            "value": tag["value"],
            "visibility": tag["visibility"],
        }
        if key not in server:
            dl.api_request("POST", f"/status/{int(status_id)}/tags", token, json_body=body)
        elif (
            server[key]["value"] != tag["value"]
            or server[key]["visibility"] != tag["visibility"]
        ):
            dl.api_request("PUT", tag_path(status_id, key), token, json_body=body)

    return fetch_status(token, status_id)


class TransferWindow(tk.Toplevel):
    """Fenster, das den Server-Sync Fahrt für Fahrt anzeigt."""

    _STATE_LABEL = {
        "waiting": "Wartend",
        "running": "Übertrage …",
        "ok": "Gespeichert",
        "error": "Fehler",
        "local": "Lokal",
    }

    def __init__(self, master, items):
        super().__init__(master)
        self.title("Übertragung nach Träwelling")
        self.transient(master)
        self.resizable(True, True)
        self.minsize(560, 280)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._done = False
        self._iids = {}
        self.total = len(items)
        self.finished = 0

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self.summary = tk.StringVar(value=f"0/{self.total} übertragen")
        ttk.Label(frm, textvariable=self.summary).pack(anchor="w")
        self.bar = ttk.Progressbar(
            frm, mode="determinate", maximum=max(self.total, 1), value=0
        )
        self.bar.pack(fill="x", pady=(4, 8))

        tree_fr = ttk.Frame(frm)
        tree_fr.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_fr, columns=("fahrt", "status", "detail"),
            show="headings", selectmode="browse",
        )
        self.tree.heading("fahrt", text="Fahrt")
        self.tree.heading("status", text="Status")
        self.tree.heading("detail", text="Meldung")
        self.tree.column("fahrt", width=320, stretch=True)
        self.tree.column("status", width=110, stretch=False)
        self.tree.column("detail", width=240, stretch=True)
        scroll = ttk.Scrollbar(tree_fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.tag_configure("ok", foreground="#1a7f37")
        self.tree.tag_configure("error", foreground="#d1242f")
        self.tree.tag_configure("running", foreground="#0969da")

        for sid, label in items:
            iid = str(sid)
            self._iids[sid] = iid
            self.tree.insert(
                "", "end", iid=iid,
                values=(label, self._STATE_LABEL["waiting"], ""),
            )

        btn = ttk.Frame(frm)
        btn.pack(fill="x", pady=(8, 0))
        self.close_btn = ttk.Button(
            btn, text="Schließen", command=self.destroy, state="disabled"
        )
        self.close_btn.pack(side="right")

    def set_row(self, sid, state, detail=""):
        iid = self._iids.get(sid)
        if iid is None or not self.tree.exists(iid):
            return
        vals = list(self.tree.item(iid, "values"))
        vals[1] = self._STATE_LABEL.get(state, state)
        vals[2] = detail or ""
        self.tree.item(iid, values=vals, tags=(state,) if state in ("ok", "error", "running") else ())
        self.tree.see(iid)

    def bump(self):
        self.finished += 1
        self.bar["value"] = self.finished
        self.summary.set(f"{self.finished}/{self.total} übertragen")

    def finish(self, text):
        self._done = True
        self.bar["value"] = self.total
        self.summary.set(text)
        self.close_btn.configure(state="normal")
        self.close_btn.focus_set()

    def _on_close(self):
        if self._done:
            self.destroy()


class TagDialog(tk.Toplevel):
    """Dialog zum Anlegen oder Bearbeiten eines sonstigen Tags."""

    def __init__(self, master, title, extra_keys=None, initial=None):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        initial = initial or {}

        keys = [
            k for k in TAG_KEY_SUGGESTIONS
            if k not in TABLE_TAG_SET and _dubi_kind({"key": k, "value": ""}) is None
        ]
        for k in extra_keys or ():
            if (
                k and k not in keys and k not in TABLE_TAG_SET
                and _dubi_kind({"key": k, "value": ""}) is None
            ):
                keys.append(k)
        start_key = initial.get("key") or ""
        if start_key and start_key not in keys:
            keys.insert(0, start_key)

        frm = ttk.Frame(self, padding=12)
        frm.grid(sticky="nsew")

        ttk.Label(frm, text="Schlüssel").grid(row=0, column=0, sticky="w")
        self.key_var = tk.StringVar(value=start_key)
        self.key_box = ttk.Combobox(frm, textvariable=self.key_var, values=keys, width=36)
        self.key_box.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 8))

        ttk.Label(frm, text="Wert").grid(row=2, column=0, sticky="w")
        self.val_var = tk.StringVar(value=initial.get("value") or "")
        ttk.Entry(frm, textvariable=self.val_var, width=36).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=(0, 8)
        )

        ttk.Label(frm, text="Sichtbarkeit").grid(row=4, column=0, sticky="w")
        vis = initial.get("visibility", 0)
        try:
            vis = int(vis)
        except (TypeError, ValueError):
            vis = 0
        self.vis_var = tk.StringVar(value=VISIBILITY_LABEL.get(vis, VISIBILITY_LABEL[0]))
        ttk.Combobox(
            frm, textvariable=self.vis_var, values=[l for _, l in VISIBILITY_CHOICES],
            state="readonly", width=34,
        ).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(0, 12))

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, sticky="e")
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(side="right", padx=(8, 0))
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.key_box.focus_set()
        self.wait_window(self)

    def _ok(self):
        key = self.key_var.get().strip()
        if not key:
            messagebox.showerror("Tag", "Schlüssel darf nicht leer sein.", parent=self)
            return
        if key in TABLE_TAG_SET:
            messagebox.showerror(
                "Tag",
                "Baureihe und Fahrzeugnummer in der Tabelle links bearbeiten.",
                parent=self,
            )
            return
        if _dubi_kind({"key": key, "value": self.val_var.get()}):
            messagebox.showerror(
                "Tag",
                "dubi start und dubi ende über die Checkboxen setzen.",
                parent=self,
            )
            return
        vis = VISIBILITY_BY_LABEL.get(self.vis_var.get(), 0)
        self.result = {"key": key, "value": self.val_var.get(), "visibility": vis}
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class LineColorDialog(tk.Toplevel):
    """Hex-Eingabe plus nativer Farbwähler; Vorschau als Linien-Badge."""

    _EMPTY = "#cbd5e1"

    def __init__(self, master, line_name, initial_bg=None):
        super().__init__(master)
        self.title("Linienfarbe")
        self.resizable(False, False)
        self.transient(master)
        self.result = None
        self._line_name = line_name or "Linie"
        start = lcp.normalize_hex(initial_bg)
        self._hex_var = tk.StringVar(value=("#" + start) if start else "")

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Hintergrund (Hex)").pack(anchor="w")
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=(0, 8))
        self._entry = ttk.Entry(row, textvariable=self._hex_var, width=14)
        self._entry.pack(side="left")
        ttk.Button(row, text="Farbwähler…", command=self._pick).pack(
            side="left", padx=(8, 0)
        )

        self._preview = tk.Label(
            frm, text=self._line_name, padx=10, pady=4,
            relief="flat", font=tkfont.nametofont("TkDefaultFont"),
        )
        self._preview.pack(fill="x", pady=(0, 8))
        ttk.Label(
            frm, text="Textfarbe wird automatisch gesetzt. Nur lokal, kein Upload.",
        ).pack(anchor="w")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")

        if hasattr(self._hex_var, "trace_add"):
            self._hex_var.trace_add("write", lambda *_: self._refresh_preview())
        else:
            self._hex_var.trace("w", lambda *_: self._refresh_preview())
        self._refresh_preview()

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self._entry.focus_set()
        self._entry.selection_range(0, "end")
        self.wait_window(self)

    def _refresh_preview(self):
        pair = lcp.to_css_pair(self._hex_var.get())
        if not pair:
            self._preview.configure(
                bg=self._EMPTY, fg="#334155", text=self._line_name,
            )
            return
        self._preview.configure(bg=pair[0], fg=pair[1], text=self._line_name)

    def _pick(self):
        current = lcp.normalize_hex(self._hex_var.get())
        initial = "#" + current if current else None
        try:
            _rgb, hx = colorchooser.askcolor(
                color=initial, parent=self, title="Linienfarbe",
            )
        except tk.TclError:
            return
        if hx:
            self._hex_var.set(hx)

    def _ok(self):
        bg = lcp.normalize_hex(self._hex_var.get())
        if not bg:
            messagebox.showerror(
                "Linienfarbe",
                "Bitte eine 6-stellige Hex-Farbe angeben (z.B. #0066ad).",
                parent=self,
            )
            return
        self.result = bg
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class HomeRegionDialog(tk.Toplevel):
    """Checkbox-Liste aller Operatoren. result = Namensliste oder None."""

    def __init__(self, master, names, selected):
        super().__init__(master)
        self.title("Heimatregion")
        self.transient(master)
        self.result = None
        self.minsize(420, 480)
        selected = set(selected or [])

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm,
            text="Angehakte Operatoren gehören zur Heimatregion. "
                 "Leere Liste schaltet den Filter aus.",
            wraplength=400,
        ).pack(anchor="w")

        self.search_var = tk.StringVar()
        search = ttk.Entry(frm, textvariable=self.search_var)
        search.pack(fill="x", pady=(8, 6))
        _on_search = lambda *_: self._apply_search()
        if hasattr(self.search_var, "trace_add"):
            self.search_var.trace_add("write", _on_search)
        else:
            self.search_var.trace("w", _on_search)

        wrap = ttk.Frame(frm)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, highlightthickness=0, width=400, height=320)
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.canvas.bind("<MouseWheel>", self._on_wheel)

        self.vars = {}
        self._rows = []
        for name in names:
            var = tk.BooleanVar(value=name in selected)
            self.vars[name] = var
            label = "(ohne Operator)" if name == "" else name
            row = ttk.Checkbutton(self.inner, text=label, variable=var)
            row.pack(anchor="w", fill="x")
            self._rows.append((name, label, row))

        quick = ttk.Frame(frm)
        quick.pack(fill="x", pady=(8, 0))
        ttk.Button(quick, text="Alle", command=lambda: self._set_visible(True)).pack(
            side="left"
        )
        ttk.Button(quick, text="Keine", command=lambda: self._set_visible(False)).pack(
            side="left", padx=6
        )
        ttk.Label(quick, text="Alle/Keine gilt für die sichtbare Suche.").pack(
            side="left", padx=(8, 0)
        )

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(btns, text="Speichern", command=self._ok).pack(side="right")

        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        search.focus_set()

    def _on_wheel(self, event):
        delta = getattr(event, "delta", 0)
        if not delta:
            return
        step = int(-delta / 120) or (-1 if delta > 0 else 1)
        self.canvas.yview_scroll(step, "units")

    def _apply_search(self):
        query = self.search_var.get().casefold().strip()
        for _name, label, row in self._rows:
            if not query or query in label.casefold():
                row.pack(anchor="w", fill="x")
            else:
                row.pack_forget()

    def _set_visible(self, checked):
        for name, _label, row in self._rows:
            if row.winfo_manager():
                self.vars[name].set(checked)

    def _ok(self):
        self.result = [name for name, var in self.vars.items() if var.get()]
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class BoardingDialog(tk.Toplevel):
    """Halt vor dem Ausstieg wählen. result = stopoverId oder None."""

    def __init__(self, master, status, selected_so):
        super().__init__(master)
        self.title("Einstieg")
        self.transient(master)
        self.result = None
        self.minsize(420, 360)
        candidates = bp.candidate_stopovers(status)
        api_so = bp.stopover_id(
            ((status.get("checkin") or {}).get("origin") or {}).get("stopoverId")
        )
        self._ids = []

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm,
            text="Neuer Einstieg auf dieser Fahrt, vor dem Ausstieg. "
                 "Nur lokal, kein Upload.",
            wraplength=400,
        ).pack(anchor="w")

        wrap = ttk.Frame(frm)
        wrap.pack(fill="both", expand=True, pady=(8, 0))
        self.list = tk.Listbox(wrap, height=12, activestyle="dotbox")
        scroll = ttk.Scrollbar(wrap, orient="vertical", command=self.list.yview)
        self.list.configure(yscrollcommand=scroll.set)
        self.list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        select_at = 0
        wanted = bp.stopover_id(selected_so)
        for i, stop in enumerate(candidates):
            so = bp.stopover_id(stop.get("stopoverId"))
            self._ids.append(so)
            self.list.insert("end", _boarding_label(stop, api_so))
            if so == wanted:
                select_at = i
        if self._ids:
            self.list.selection_set(select_at)
            self.list.activate(select_at)
            self.list.see(select_at)

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right")

        self.list.bind("<Double-1>", lambda _e: self._ok())
        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.list.focus_set()
        self.wait_window(self)

    def _ok(self):
        sel = self.list.curselection()
        if not sel:
            return
        self.result = self._ids[sel[0]]
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class _WithdrawnDateDialog(tk.Toplevel):
    """Optionales Ausmusterungsdatum. result = YYYY-MM-DD, \"\" oder None."""

    def __init__(self, master, initial):
        super().__init__(master)
        self.title("Ausmusterungsdatum")
        self.transient(master)
        self.result = None
        self.resizable(False, False)

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm, text="Datum (JJJJ-MM-TT). Leer lassen, wenn es unbekannt ist.",
        ).pack(anchor="w")
        self.var = tk.StringVar(value=initial or "")
        entry = ttk.Entry(frm, textvariable=self.var, width=16)
        entry.pack(anchor="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(side="right")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="right", padx=(0, 8))

        self.bind("<Escape>", lambda _e: self._cancel())
        self.bind("<Return>", lambda _e: self._ok())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        entry.focus_set()
        entry.selection_range(0, "end")

    def _ok(self):
        text = self.var.get().strip()
        if not text:
            self.result = ""
            self.destroy()
            return
        parsed = vr.parse_withdrawn_on(text)
        if not parsed:
            messagebox.showerror(
                "Ausmusterungsdatum",
                "Bitte JJJJ-MM-TT angeben oder das Feld leer lassen.",
                parent=self,
            )
            return
        self.result = parsed
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class VehicleRosterDialog(tk.Toplevel):
    """Nummern je Baureihe. result = {Baureihe: [Einträge]} oder None."""

    def __init__(self, master, roster, class_names):
        super().__init__(master)
        self.title("Fuhrpark")
        self.transient(master)
        self.result = None
        self.minsize(560, 520)
        self.roster = {}
        types = (roster or {}).get("types") or {}
        for name, entries in types.items():
            self.roster[name] = [dict(entry) for entry in entries]
        known = list(class_names or [])
        known.extend(self.roster.keys())
        self._class_names = vr.collect_loc_classes([], known)
        self.current = None
        self._vehicles = []
        self._loading = False

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)
        ttk.Label(
            frm,
            text="Je Baureihe die konkreten Fahrzeugnummern. "
                 "Ausgemusterte zählen nicht zur Abdeckung und nicht zum Goldrand. "
                 "Das Datum ist optional.",
            wraplength=520,
        ).pack(anchor="w")

        class_row = ttk.Frame(frm)
        class_row.pack(fill="x", pady=(8, 6))
        ttk.Label(class_row, text="Baureihe").pack(side="left")
        self.class_var = tk.StringVar()
        self.class_box = ttk.Combobox(
            class_row, textvariable=self.class_var, values=self._class_names,
        )
        self.class_box.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.class_box.bind("<<ComboboxSelected>>", self._on_class)
        self.class_box.bind("<Return>", self._on_class)
        self.class_box.bind("<FocusOut>", self._on_class)

        tree_fr = ttk.Frame(frm)
        tree_fr.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_fr, columns=("number", "status", "date"), show="headings",
            selectmode="browse", height=14,
        )
        self.tree.heading("number", text="Nummer")
        self.tree.heading("status", text="Status")
        self.tree.heading("date", text="Ausgemustert am")
        self.tree.column("number", width=120)
        self.tree.column("status", width=120)
        self.tree.column("date", width=140)
        scroll = ttk.Scrollbar(tree_fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.tree.bind("<Double-1>", lambda _e: self._toggle())

        row_btns = ttk.Frame(frm)
        row_btns.pack(fill="x", pady=(6, 0))
        self.toggle_btn = ttk.Button(
            row_btns, text="Ausgemustert umschalten", command=self._toggle,
        )
        self.toggle_btn.pack(side="left")
        self.date_btn = ttk.Button(
            row_btns, text="Datum…", command=self._set_date,
        )
        self.date_btn.pack(side="left", padx=6)
        self.delete_btn = ttk.Button(
            row_btns, text="Löschen", command=self._delete,
        )
        self.delete_btn.pack(side="left")

        range_row = ttk.Frame(frm)
        range_row.pack(fill="x", pady=(10, 0))
        ttk.Label(range_row, text="Von").pack(side="left")
        self.from_var = tk.StringVar()
        ttk.Entry(range_row, textvariable=self.from_var, width=8).pack(
            side="left", padx=(4, 8)
        )
        ttk.Label(range_row, text="Bis").pack(side="left")
        self.to_var = tk.StringVar()
        ttk.Entry(range_row, textvariable=self.to_var, width=8).pack(
            side="left", padx=(4, 8)
        )
        ttk.Label(range_row, text="Schritt").pack(side="left")
        self.step_var = tk.StringVar(value="1")
        ttk.Entry(range_row, textvariable=self.step_var, width=6).pack(
            side="left", padx=(4, 8)
        )
        ttk.Button(range_row, text="Hinzufügen", command=self._add_range).pack(
            side="left"
        )

        self.info_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=self.info_var).pack(anchor="w", pady=(6, 0))

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="Abbrechen", command=self._cancel).pack(
            side="right", padx=(8, 0)
        )
        ttk.Button(btns, text="Speichern", command=self._ok).pack(side="right")

        initial = ""
        for name in self._class_names:
            if self.roster.get(name):
                initial = name
                break
        if not initial and self._class_names:
            initial = self._class_names[0]
        self._loading = True
        self.class_var.set(initial)
        self._loading = False
        self._show_class(initial, store=False)

        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.grab_set()
        self.class_box.focus_set()

    def _on_class(self, _event=None):
        if self._loading:
            return
        name = self.class_var.get().strip()
        if name == (self.current or ""):
            return
        self._show_class(name, store=True)

    def _show_class(self, name, store):
        name = (name or "").strip()
        if store:
            self._store_current()
        self.current = name or None
        if name and name not in self._class_names:
            self._class_names = vr.collect_loc_classes([], self._class_names + [name])
            self.class_box.configure(values=self._class_names)
        self._vehicles = [dict(entry) for entry in self.roster.get(name, [])] if name else []
        self._fill_tree()

    def _store_current(self):
        if not self.current:
            return
        if self._vehicles:
            self.roster[self.current] = [dict(entry) for entry in self._vehicles]
        else:
            self.roster.pop(self.current, None)

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        for index, entry in enumerate(self._vehicles):
            status = "ausgemustert" if entry.get("withdrawn") else "aktiv"
            date = entry.get("withdrawnOn") or ""
            self.tree.insert(
                "", "end", iid=str(index),
                values=(entry["number"], status, date),
            )
        self._update_buttons()

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except (TypeError, ValueError):
            return None

    def _update_buttons(self):
        index = self._selected_index()
        has = index is not None
        self.toggle_btn.configure(state="normal" if has else "disabled")
        self.delete_btn.configure(state="normal" if has else "disabled")
        withdrawn = has and self._vehicles[index].get("withdrawn")
        self.date_btn.configure(state="normal" if withdrawn else "disabled")

    def _remember_selection(self, index):
        if index is None or index >= len(self._vehicles):
            return
        iid = str(index)
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self.tree.see(iid)
        self._update_buttons()

    def _toggle(self):
        index = self._selected_index()
        if index is None or not self.current:
            return
        entry = self._vehicles[index]
        entry["withdrawn"] = not entry.get("withdrawn")
        if not entry["withdrawn"]:
            entry.pop("withdrawnOn", None)
        self._store_current()
        self._fill_tree()
        self._remember_selection(index)

    def _set_date(self):
        index = self._selected_index()
        if index is None or not self.current:
            return
        entry = self._vehicles[index]
        if not entry.get("withdrawn"):
            return
        dlg = _WithdrawnDateDialog(self, entry.get("withdrawnOn") or "")
        self.wait_window(dlg)
        if dlg.result is None:
            return
        if dlg.result:
            entry["withdrawnOn"] = dlg.result
        else:
            entry.pop("withdrawnOn", None)
        self._store_current()
        self._fill_tree()
        self._remember_selection(index)

    def _delete(self):
        index = self._selected_index()
        if index is None or not self.current:
            return
        del self._vehicles[index]
        self._store_current()
        self._fill_tree()
        if self._vehicles:
            self._remember_selection(min(index, len(self._vehicles) - 1))

    def _parse_bound(self, text, label):
        text = (text or "").strip()
        if not text.isdigit():
            raise ValueError("%s muss eine ganze Zahl ab 0 sein." % label)
        return int(text)

    def _add_range(self):
        name = self.class_var.get().strip()
        if not name:
            messagebox.showerror(
                "Fuhrpark", "Zuerst eine Baureihe angeben.", parent=self,
            )
            return
        if name != (self.current or ""):
            self._show_class(name, store=True)
        try:
            start = self._parse_bound(self.from_var.get(), "Von")
            end = self._parse_bound(self.to_var.get(), "Bis")
            step = self._parse_bound(self.step_var.get() or "1", "Schrittweite")
            numbers = vr.expand_range(start, end, step)
        except ValueError as exc:
            messagebox.showerror("Fuhrpark", str(exc), parent=self)
            return
        before = len(self._vehicles)
        self._vehicles = vr.add_numbers(self._vehicles, numbers)
        added = len(self._vehicles) - before
        self._store_current()
        self._fill_tree()
        self.info_var.set(
            "%d Nummern in der Spanne, %d neu." % (len(numbers), added)
        )

    def _ok(self):
        self._store_current()
        self.result = {
            name: entries
            for name, entries in self.roster.items()
            if entries
        }
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class EditorApp:
    def __init__(self, root, token, username, statuses, statuses_path, limit, since,
                 stations_path="data/stations.json", dashboard_path="data/dashboard.html",
                 loc_class_families="loc_class_families.txt", ignore_plus=False,
                 edge_patches_path="data/edge_patches.json",
                 station_patches_path="data/station_patches.json",
                 line_color_patches_path="data/line_color_patches.json",
                 home_region_path="data/home_region.json",
                 boarding_patches_path="data/boarding_patches.json",
                 vehicle_roster_path="data/vehicle_roster.json"):
        self.root = root
        self.token = token
        self.username = username
        self.statuses = list(statuses or [])
        self.statuses_path = statuses_path
        self.stations_path = stations_path
        self.dashboard_path = dashboard_path
        self.loc_class_families = loc_class_families
        self.ignore_plus = ignore_plus
        self.edge_patches_path = edge_patches_path
        self.station_patches_path = station_patches_path
        self.line_color_patches_path = line_color_patches_path
        self.home_region_path = home_region_path
        self.boarding_patches_path = boarding_patches_path
        self.vehicle_roster_path = vehicle_roster_path
        self.patches = ep.load_patches(edge_patches_path)
        self.station_patches = sp.load_patches(station_patches_path)
        self.line_color_patches = lcp.load_patches(line_color_patches_path)
        self.boarding_patches = bp.load_patches(boarding_patches_path)
        self._patch_server = None
        self._station_server = None
        self.limit = limit
        self.since = since
        self.filtered = []
        self.current = None
        self._busy = False
        self._ignore_dubi = False
        self._tag_rows = []
        self._edge_rows = []
        self._ignore_select = False
        self._edit_entry = None
        self._edit_iid = None
        self._edit_col = None
        self._edit_closing = False
        self._baseline = {}
        self._xfer = None
        self._sort_keys = [("date", True)]  # (Spalte, absteigend), Index 0 = primär
        self._reset_baselines()

        root.title(f"Walita – Tag-Editor ({username})")
        root.minsize(1100, 580)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build()
        self._apply_filter()
        kids = self.trips.get_children()
        if kids:
            self.trips.selection_set(kids[0])
            self.trips.focus(kids[0])
            self._on_select()
        src = (
            f" aus {statuses_path}"
            if os.path.isfile(statuses_path) else " von der API"
        )
        self._set_status(f"{len(self.statuses)} Fahrten geladen{src}.")

    def _reset_baselines(self):
        self._baseline = {
            s.get("id"): _snapshot_status(s)
            for s in self.statuses
            if s.get("id") is not None
        }
        self._original_tags = {
            s.get("id"): [
                _norm_tag(t) for t in (s.get("tags") or []) if _norm_tag(t)["key"]
            ]
            for s in self.statuses
            if s.get("id") is not None
        }

    def _status_dirty(self, status):
        sid = status.get("id")
        if sid is None:
            return False
        return _snapshot_status(status) != self._baseline.get(sid)

    def _any_dirty(self):
        self._flush_detail()
        return any(self._status_dirty(s) for s in self.statuses)

    def _build(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)

        panes = ttk.Panedwindow(outer, orient="horizontal")
        panes.pack(fill="both", expand=True)

        left = ttk.Frame(panes, padding=(0, 0, 8, 0))
        right = ttk.Frame(panes, padding=(8, 0, 0, 0))
        panes.add(left, weight=3)
        panes.add(right, weight=1)

        filt_row = ttk.Frame(left)
        filt_row.pack(fill="x")
        ttk.Label(filt_row, text="Suche").pack(side="left")
        self.filter_var = tk.StringVar()
        _on_filter = lambda *_: self._apply_filter()
        if hasattr(self.filter_var, "trace_add"):
            self.filter_var.trace_add("write", _on_filter)
        else:
            self.filter_var.trace("w", _on_filter)
        ttk.Entry(filt_row, textvariable=self.filter_var).pack(
            side="left", fill="x", expand=True, padx=6
        )
        self.reload_btn = ttk.Button(
            filt_row, text="Von API laden", command=self._reload_from_api
        )
        self.reload_btn.pack(side="right")

        list_fr = ttk.Frame(left)
        list_fr.pack(fill="both", expand=True, pady=(6, 0))
        self.trips = ttk.Treeview(
            list_fr, columns=TRIP_COLS, show="headings", selectmode="browse"
        )
        headings = TRIP_HEADINGS
        widths = {
            "date": 90, "line": 90, "origin": 140, "dest": 140,
            "loc": 210, "veh": 140,
        }
        for col in TRIP_COLS:
            self.trips.heading(col, command=lambda c=col: self._sort_by(c))
            stretch = col in ("origin", "dest", "veh")
            self.trips.column(col, width=widths[col], minwidth=60, stretch=stretch)
        self._refresh_headings()
        yscroll = ttk.Scrollbar(list_fr, orient="vertical", command=self.trips.yview)
        xscroll = ttk.Scrollbar(list_fr, orient="horizontal", command=self.trips.xview)
        self.trips.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.trips.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        list_fr.rowconfigure(0, weight=1)
        list_fr.columnconfigure(0, weight=1)

        bold = tkfont.nametofont("TkDefaultFont").copy()
        bold.configure(weight="bold")
        self.trips.tag_configure("dirty", font=bold)

        self.trips.bind("<<TreeviewSelect>>", lambda _e: self._on_select())
        self.trips.bind("<Button-1>", self._on_trip_click, add="+")
        self.trips.bind("<Double-1>", self._on_trip_click, add="+")
        self.trips.bind("<F2>", self._on_f2)
        self.trips.bind("<MouseWheel>", lambda _e: self._commit_edit())

        self.meta_var = tk.StringVar(value="Keine Fahrt gewählt.")
        ttk.Label(right, textvariable=self.meta_var, justify="left").pack(anchor="w")

        color_row = ttk.Frame(right)
        color_row.pack(fill="x", pady=(8, 0))
        ttk.Label(color_row, text="Linienfarbe").pack(side="left")
        self._color_swatch = tk.Frame(
            color_row, width=32, height=18, relief="solid", bd=1,
            highlightthickness=0, cursor="hand2",
        )
        self._color_swatch.pack(side="left", padx=(8, 6))
        self._color_swatch.pack_propagate(False)
        self._color_hex_var = tk.StringVar(value="—")
        ttk.Label(color_row, textvariable=self._color_hex_var).pack(side="left")
        self._color_src_var = tk.StringVar(value="")
        ttk.Label(color_row, textvariable=self._color_src_var).pack(
            side="left", padx=(6, 0)
        )
        self.color_pick_btn = ttk.Button(
            color_row, text="Ändern", command=self._pick_line_color
        )
        self.color_pick_btn.pack(side="right")
        self.color_reset_btn = ttk.Button(
            color_row, text="Zurücksetzen", command=self._reset_line_color
        )
        self.color_reset_btn.pack(side="right", padx=(0, 6))
        self._color_swatch.bind("<Button-1>", lambda _e: self._pick_line_color())
        self._refresh_line_color()

        board_row = ttk.Frame(right)
        board_row.pack(fill="x", pady=(8, 0))
        ttk.Label(board_row, text="Einstieg").pack(side="left")
        self._board_var = tk.StringVar(value="—")
        ttk.Label(board_row, textvariable=self._board_var).pack(
            side="left", padx=(8, 0)
        )
        self._board_src_var = tk.StringVar(value="")
        ttk.Label(board_row, textvariable=self._board_src_var).pack(
            side="left", padx=(6, 0)
        )
        self.board_pick_btn = ttk.Button(
            board_row, text="Ändern", command=self._pick_boarding
        )
        self.board_pick_btn.pack(side="right")
        self.board_reset_btn = ttk.Button(
            board_row, text="Zurücksetzen", command=self._reset_boarding
        )
        self.board_reset_btn.pack(side="right", padx=(0, 6))
        self._refresh_boarding()

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)

        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="Status-Text").pack(side="left")
        self.body_count = tk.StringVar(value="0/280")
        ttk.Label(head, textvariable=self.body_count).pack(side="right")

        self.body = tk.Text(right, height=4, wrap="word", undo=True)
        self.body.pack(fill="x")
        self.body.bind("<KeyRelease>", lambda _e: self._on_body_changed())

        self.dubi_start_var = tk.BooleanVar(value=False)
        self.dubi_ende_var = tk.BooleanVar(value=False)
        ttk.Label(right, text="Durchbindung").pack(anchor="w", pady=(10, 0))
        self.dubi_start_btn = ttk.Checkbutton(
            right, text="dubi start: Beginn kein Einstieg",
            variable=self.dubi_start_var, command=self._on_dubi_changed,
        )
        self.dubi_start_btn.pack(anchor="w")
        self.dubi_ende_btn = ttk.Checkbutton(
            right, text="dubi ende: Ende kein Ausstieg",
            variable=self.dubi_ende_var, command=self._on_dubi_changed,
        )
        self.dubi_ende_btn.pack(anchor="w")
        self._sync_dubi_buttons()

        ttk.Label(right, text="Weitere Tags").pack(anchor="w", pady=(10, 2))
        tree_fr = ttk.Frame(right)
        tree_fr.pack(fill="both", expand=True)
        cols = ("key", "value", "visibility")
        self.tree = ttk.Treeview(
            tree_fr, columns=cols, show="headings", selectmode="browse", height=6
        )
        self.tree.heading("key", text="Schlüssel")
        self.tree.heading("value", text="Wert")
        self.tree.heading("visibility", text="Sichtbarkeit")
        self.tree.column("key", width=140)
        self.tree.column("value", width=140)
        self.tree.column("visibility", width=100)
        tree_scroll = ttk.Scrollbar(tree_fr, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _e: self._edit_tag())

        tag_btns = ttk.Frame(right)
        tag_btns.pack(fill="x", pady=6)
        ttk.Button(tag_btns, text="Tag hinzufügen", command=self._add_tag).pack(side="left")
        ttk.Button(tag_btns, text="Tag bearbeiten", command=self._edit_tag).pack(
            side="left", padx=6
        )
        ttk.Button(tag_btns, text="Tag löschen", command=self._delete_tag).pack(side="left")

        ttk.Label(right, text="Kanten").pack(anchor="w", pady=(10, 2))
        edge_fr = ttk.Frame(right)
        edge_fr.pack(fill="x")
        self.edges = ttk.Treeview(
            edge_fr, columns=("origin", "dest", "patch"), show="headings",
            selectmode="browse", height=5,
        )
        self.edges.heading("origin", text="Von")
        self.edges.heading("dest", text="Nach")
        self.edges.heading("patch", text="Patch")
        self.edges.column("origin", width=110)
        self.edges.column("dest", width=110)
        self.edges.column("patch", width=80)
        edge_scroll = ttk.Scrollbar(edge_fr, orient="vertical", command=self.edges.yview)
        self.edges.configure(yscrollcommand=edge_scroll.set)
        self.edges.pack(side="left", fill="x", expand=True)
        edge_scroll.pack(side="right", fill="y")
        self.edges.bind("<Double-1>", lambda _e: self._open_edge_map())

        edge_btns = ttk.Frame(right)
        edge_btns.pack(fill="x", pady=6)
        self.edge_map_btn = ttk.Button(
            edge_btns, text="Auf Karte anreichern", command=self._open_edge_map
        )
        self.edge_map_btn.pack(side="left")
        self.edge_clr_def_btn = ttk.Button(
            edge_btns, text="Standard löschen", command=self._clear_edge_default
        )
        self.edge_clr_def_btn.pack(side="left", padx=6)
        self.edge_clr_ov_btn = ttk.Button(
            edge_btns, text="Fahrt-Override löschen", command=self._clear_edge_override
        )
        self.edge_clr_ov_btn.pack(side="left")

        act = ttk.Frame(outer)
        act.pack(fill="x", pady=(8, 0))
        self.save_btn = ttk.Button(act, text="Speichern", command=self._save)
        self.save_btn.pack(side="left")
        self.dash_btn = ttk.Button(
            act, text="Dashboard neu bauen", command=self._on_rebuild_dashboard
        )
        self.dash_btn.pack(side="left", padx=8)
        self.station_map_btn = ttk.Button(
            act, text="Stationen anpassen", command=self._open_station_map
        )
        self.station_map_btn.pack(side="left", padx=8)
        self.home_btn = ttk.Button(
            act, text="Heimatregion…", command=self._open_home_region
        )
        self.home_btn.pack(side="left", padx=8)
        self.roster_btn = ttk.Button(
            act, text="Fuhrpark…", command=self._open_vehicle_roster
        )
        self.roster_btn.pack(side="left", padx=8)
        ttk.Label(
            act, text="Speichern sendet gestagte Änderungen nach Träwelling"
        ).pack(side="left")

        self.status_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status_var, relief="sunken", anchor="w").pack(
            fill="x", pady=(8, 0)
        )

        self.root.bind("<Control-s>", lambda _e: self._save())
        self.root.bind("<Control-S>", lambda _e: self._save())

    def _set_status(self, msg):
        self.status_var.set(msg)
        log(msg)

    def _set_busy(self, busy, msg=None):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.save_btn.configure(state=state)
        self.reload_btn.configure(state=state)
        self.dash_btn.configure(state=state)
        if hasattr(self, "edge_map_btn"):
            self.edge_map_btn.configure(state=state)
            self.edge_clr_def_btn.configure(state=state)
            self.edge_clr_ov_btn.configure(state=state)
        if hasattr(self, "station_map_btn"):
            self.station_map_btn.configure(state=state)
        if hasattr(self, "home_btn"):
            self.home_btn.configure(state=state)
        if hasattr(self, "roster_btn"):
            self.roster_btn.configure(state=state)
        self._sync_dubi_buttons()
        if hasattr(self, "board_pick_btn"):
            if busy:
                self.board_pick_btn.configure(state="disabled")
                self.board_reset_btn.configure(state="disabled")
            else:
                self._refresh_boarding()
        if msg:
            self.status_var.set(msg)

    def _run_async(self, work, on_ok, busy_msg="Bitte warten …"):
        if self._busy:
            return
        self._set_busy(True, busy_msg)

        def worker():
            try:
                result = work()
            except Exception as e:
                self.root.after(0, lambda err=e: self._finish_err(err))
                return
            self.root.after(0, lambda r=result: self._finish_ok(on_ok, r))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_ok(self, on_ok, result):
        self._set_busy(False)
        on_ok(result)

    def _finish_err(self, err):
        self._set_busy(False)
        msg = format_api_error(err)
        self._set_status(msg)
        messagebox.showerror("Fehler", msg, parent=self.root)

    def _status_by_iid(self, iid):
        try:
            sid = int(iid)
        except (TypeError, ValueError):
            sid = iid
        for s in self.statuses:
            if s.get("id") == sid:
                return s
        return None

    def _apply_filter(self):
        if not hasattr(self, "trips"):
            return
        self._commit_edit()
        self._flush_detail()
        q = (self.filter_var.get() or "").strip().lower()
        selected_id = self.current.get("id") if self.current else None
        if not q:
            self.filtered = list(self.statuses)
        else:
            self.filtered = [s for s in self.statuses if self._matches_filter(s, q)]
        self._apply_sort()
        self._ignore_select = True
        self.trips.delete(*self.trips.get_children())
        restore = None
        for s in self.filtered:
            sid = s.get("id")
            if sid is None:
                continue
            iid = str(sid)
            tags = ("dirty",) if self._status_dirty(s) else ()
            self.trips.insert(
                "", "end", iid=iid, values=_trip_values(self._shown(s)), tags=tags
            )
            if selected_id is not None and sid == selected_id:
                restore = iid
        self._ignore_select = False
        if restore is not None:
            self.trips.selection_set(restore)
            self.trips.see(restore)
        elif self.current is not None:
            self.current = None
            self._clear_detail()

    def _col_sort_key(self, status, col):
        idx = TRIP_COLS.index(col)
        text = (_trip_values(self._shown(status))[idx] or "").strip()
        return (text == "", text.casefold())

    def _apply_sort(self):
        """Stabil mehrstufig: zuletzt geklickte Spalte zuerst, ältere als Tie-Breaker."""
        for col, desc in reversed(self._sort_keys):
            self.filtered.sort(
                key=lambda s, c=col: self._col_sort_key(s, c),
                reverse=desc,
            )

    def _refresh_headings(self):
        rank = {col: i + 1 for i, (col, _desc) in enumerate(self._sort_keys)}
        desc_of = {col: desc for col, desc in self._sort_keys}
        multi = len(self._sort_keys) > 1
        for col in TRIP_COLS:
            label = TRIP_HEADINGS[col]
            if col in rank:
                arrow = "▼" if desc_of[col] else "▲"
                if multi:
                    label = f"{label} {arrow}{rank[col]}"
                else:
                    label = f"{label} {arrow}"
            self.trips.heading(col, text=label)

    def _sort_by(self, col):
        if self._busy or col not in TRIP_COLS:
            return
        if self._sort_keys and self._sort_keys[0][0] == col:
            old_col, old_desc = self._sort_keys[0]
            self._sort_keys[0] = (old_col, not old_desc)
        else:
            self._sort_keys = [(c, d) for c, d in self._sort_keys if c != col]
            self._sort_keys.insert(0, (col, False))
        self._refresh_headings()
        self._apply_filter()

    def _refresh_trip_row(self, status):
        sid = status.get("id")
        if sid is None:
            return
        iid = str(sid)
        if not self.trips.exists(iid):
            return
        self.trips.item(
            iid, values=_trip_values(self._shown(status)),
            tags=("dirty",) if self._status_dirty(status) else (),
        )

    def _selected_status(self):
        sel = self.trips.selection()
        if not sel:
            return None
        return self._status_by_iid(sel[0])

    def _merged_detail_tags(self):
        """Tabellen-Tags aus dem Status, übrige aus ``_tag_rows``.

        Die Reihenfolge folgt den Tags vom Laden. Neue Schlüssel hängen an.
        So bleibt eine unangetastete Fahrt beim Wechsel ungestagt.
        """
        pieces = []
        seen = set()
        for t in self.current.get("tags") or []:
            n = _norm_tag(t)
            if n["key"] in TABLE_TAG_SET and n["key"] not in seen:
                pieces.append(n)
                seen.add(n["key"])
        for t in self._tag_rows:
            n = _norm_tag(t)
            if n["key"] and n["key"] not in TABLE_TAG_SET and n["key"] not in seen:
                pieces.append(n)
                seen.add(n["key"])
        original = self._original_tags.get(self.current.get("id")) or []
        return _order_like_original(pieces, original)

    def _flush_detail(self):
        if self.current is None or not hasattr(self, "body"):
            return
        new_body = self.body.get("1.0", "end-1c")
        merged = self._merged_detail_tags()
        if _snapshot_status(self.current) == (new_body or "", _tags_tuple(merged)):
            self._refresh_trip_row(self.current)
            return
        self.current["body"] = new_body
        self.current["tags"] = merged
        self._refresh_trip_row(self.current)

    def _clear_detail(self):
        self.meta_var.set("Keine Fahrt gewählt.")
        self.body.delete("1.0", "end")
        self._update_body_count()
        self._tag_rows = []
        self._show_dubi_checks()
        self._refresh_extra_tree()
        self._refresh_edges()
        self._refresh_line_color()
        self._refresh_boarding()
        self._sync_dubi_buttons()

    def _on_select(self):
        if self._busy or self._ignore_select:
            return
        nxt = self._selected_status()
        if nxt is None:
            return
        if self.current is not None and nxt.get("id") == self.current.get("id"):
            return
        self._flush_detail()
        self._load_status(nxt)

    def _select_id(self, status_id):
        iid = str(status_id)
        if not self.trips.exists(iid):
            return None
        self.trips.selection_set(iid)
        self.trips.see(iid)
        self.trips.focus(iid)
        return self._status_by_iid(iid)

    def _load_status(self, status):
        self.current = status
        self._apply_meta(status)
        body = status.get("body") or ""
        self.body.delete("1.0", "end")
        self.body.insert("1.0", body)
        self._update_body_count()
        self._tag_rows = [
            _norm_tag(t) for t in (status.get("tags") or [])
            if _norm_tag(t)["key"] and _norm_tag(t)["key"] not in TABLE_TAG_SET
        ]
        self._show_dubi_checks()
        self._refresh_extra_tree()
        self._sync_dubi_buttons()
        self._refresh_edges()
        self._refresh_line_color()
        self._refresh_boarding()

    def _shown(self, status):
        """Anzeige-Kopie mit lokalem Einstieg. Ohne Patch dasselbe Objekt."""
        if status is None:
            return None
        return bp.preview_status(status, self.boarding_patches)

    def _matches_filter(self, status, query):
        blob = _search_blob(self._shown(status))
        api = _station_name((status.get("checkin") or {}).get("origin") or {})
        return query in blob or query in api.lower()

    def _apply_meta(self, status):
        bits = _checkin_bits(self._shown(status))
        self.meta_var.set(
            f"{bits['line']}\n"
            f"{bits['origin']} → {bits['dest']}\n"
            f"{_fmt_when(bits['dep'])}  →  {_fmt_when(bits['arr'])}"
        )

    def _on_body_changed(self):
        self._update_body_count()
        self._flush_detail()

    def _update_body_count(self):
        n = len(self.body.get("1.0", "end-1c"))
        self.body_count.set(f"{n}/{BODY_MAX}")

    def _show_dubi_checks(self):
        """Checkboxen aus den Tags, ohne sie umzuschreiben."""
        if not hasattr(self, "dubi_start_var"):
            return
        start, ende = build_dashboard.dubi_flags(self._tag_rows)
        self._ignore_dubi = True
        try:
            self.dubi_start_var.set(bool(start))
            self.dubi_ende_var.set(bool(ende))
        finally:
            self._ignore_dubi = False

    def _sync_dubi_buttons(self):
        if not hasattr(self, "dubi_start_btn"):
            return
        state = "normal" if self.current is not None and not self._busy else "disabled"
        self.dubi_start_btn.configure(state=state)
        self.dubi_ende_btn.configure(state=state)

    def _on_dubi_changed(self):
        if self._ignore_dubi or self.current is None or self._busy:
            return
        original = self._original_tags.get(self.current.get("id")) or []
        self._tag_rows = apply_dubi_checks(
            self._tag_rows,
            bool(self.dubi_start_var.get()),
            bool(self.dubi_ende_var.get()),
            original=original,
        )
        self._refresh_extra_tree()
        self._flush_detail()

    def _refresh_extra_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(self._tag_rows):
            if _dubi_kind(t):
                continue
            self.tree.insert(
                "", "end", iid=str(i),
                values=(t["key"], t["value"], VISIBILITY_LABEL.get(t["visibility"], t["visibility"])),
            )

    def _suggestion_keys(self):
        extra = []
        seen = set()

        def add(k):
            if (
                k and k not in seen and k not in TABLE_TAG_SET
                and _dubi_kind({"key": k, "value": ""}) is None
            ):
                extra.append(k)
                seen.add(k)

        for t in self._tag_rows:
            add(t.get("key") or "")
        for s in self.statuses:
            for t in s.get("tags") or []:
                add((t.get("key") or "").strip())
        return extra

    def _add_tag(self):
        if self.current is None:
            return
        dlg = TagDialog(self.root, "Tag hinzufügen", extra_keys=self._suggestion_keys())
        if not dlg.result:
            return
        key = dlg.result["key"]
        if any(t["key"] == key for t in self._tag_rows):
            messagebox.showerror(
                "Tag", f"Schlüssel {key!r} ist bereits vergeben.", parent=self.root
            )
            return
        self._tag_rows.append(dlg.result)
        self._refresh_extra_tree()
        self._flush_detail()

    def _edit_tag(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        dlg = TagDialog(
            self.root, "Tag bearbeiten",
            extra_keys=self._suggestion_keys(),
            initial=self._tag_rows[idx],
        )
        if not dlg.result:
            return
        new_key = dlg.result["key"]
        for i, t in enumerate(self._tag_rows):
            if i != idx and t["key"] == new_key:
                messagebox.showerror(
                    "Tag", f"Schlüssel {new_key!r} ist bereits vergeben.", parent=self.root
                )
                return
        self._tag_rows[idx] = dlg.result
        self._refresh_extra_tree()
        self._flush_detail()

    def _delete_tag(self):
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        del self._tag_rows[idx]
        self._refresh_extra_tree()
        self._flush_detail()

    def _refresh_line_color(self):
        if not hasattr(self, "_color_swatch"):
            return
        empty = LineColorDialog._EMPTY
        bg, _fg, patched = lcp.effective_colors(
            self.current, self.line_color_patches
        )
        has = self.current is not None
        if has and bg:
            self._color_swatch.configure(bg="#" + bg)
            self._color_hex_var.set("#" + bg)
            self._color_src_var.set("lokal" if patched else "Träwelling")
        else:
            self._color_swatch.configure(bg=empty)
            self._color_hex_var.set("—")
            self._color_src_var.set("" if not has else "keine")
        self.color_pick_btn.configure(state="normal" if has else "disabled")
        self.color_reset_btn.configure(
            state="normal" if has and patched else "disabled"
        )

    def _pick_line_color(self):
        if self.current is None:
            return
        bg, _fg, _patched = lcp.effective_colors(
            self.current, self.line_color_patches
        )
        bits = _checkin_bits(self.current)
        dlg = LineColorDialog(self.root, bits["line"], initial_bg=bg)
        if not dlg.result:
            return
        sid = self.current.get("id")
        if not lcp.set_color(self.line_color_patches, sid, dlg.result):
            messagebox.showerror(
                "Linienfarbe", "Ungültige Farbe.", parent=self.root
            )
            return
        if not lcp.save_patches(
            self.line_color_patches_path, self.line_color_patches
        ):
            messagebox.showerror(
                "Linienfarbe",
                "line_color_patches.json nicht schreibbar.",
                parent=self.root,
            )
            return
        self._refresh_line_color()
        self._set_status("Linienfarbe gespeichert (lokal).")

    def _reset_line_color(self):
        if self.current is None:
            return
        lcp.clear_color(self.line_color_patches, self.current.get("id"))
        if not lcp.save_patches(
            self.line_color_patches_path, self.line_color_patches
        ):
            messagebox.showerror(
                "Linienfarbe",
                "line_color_patches.json nicht schreibbar.",
                parent=self.root,
            )
            return
        self._refresh_line_color()
        self._set_status("Linienfarbe zurückgesetzt (lokal).")

    def _refresh_boarding(self):
        if not hasattr(self, "_board_var"):
            return
        status = self.current
        if status is None:
            self._board_var.set("—")
            self._board_src_var.set("")
            if hasattr(self, "board_pick_btn"):
                self.board_pick_btn.configure(state="disabled")
                self.board_reset_btn.configure(state="disabled")
            return
        shown = self._shown(status)
        name = _station_name((shown.get("checkin") or {}).get("origin") or {})
        api = _station_name((status.get("checkin") or {}).get("origin") or {})
        patched = shown is not status
        self._board_var.set(name)
        if patched:
            self._board_src_var.set(f"lokal (Träwelling: {api})")
        else:
            self._board_src_var.set("Träwelling")
        if not hasattr(self, "board_pick_btn") or self._busy:
            return
        can = bool(bp.candidate_stopovers(status))
        self.board_pick_btn.configure(state="normal" if can else "disabled")
        self.board_reset_btn.configure(
            state="normal" if patched else "disabled"
        )

    def _save_boarding(self):
        if bp.save_patches(self.boarding_patches_path, self.boarding_patches):
            return True
        messagebox.showerror(
            "Einstieg",
            "boarding_patches.json nicht schreibbar.",
            parent=self.root,
        )
        return False

    def _after_boarding_change(self, msg):
        if self.current is not None:
            self._refresh_trip_row(self.current)
            self._apply_meta(self.current)
            self._refresh_edges()
        self._refresh_boarding()
        self._set_status(msg)

    def _pick_boarding(self):
        if self.current is None or self._busy:
            return
        if not bp.candidate_stopovers(self.current):
            messagebox.showinfo(
                "Einstieg",
                "Für diese Fahrt lässt sich der Einstieg nicht ändern.",
                parent=self.root,
            )
            return
        api_so = bp.stopover_id(
            ((self.current.get("checkin") or {}).get("origin") or {}).get("stopoverId")
        )
        selected = bp.override_stopover(self.boarding_patches, self.current)
        if selected is None:
            selected = api_so
        dlg = BoardingDialog(self.root, self.current, selected)
        if dlg.result is None:
            return
        sid = self.current.get("id")
        if bp.stopover_id(dlg.result) == api_so:
            bp.clear_origin(self.boarding_patches, sid)
            saved_msg = "Einstieg zurückgesetzt (lokal)."
        elif not bp.set_origin(self.boarding_patches, sid, dlg.result):
            messagebox.showerror(
                "Einstieg", "Ungültiger Halt.", parent=self.root
            )
            return
        else:
            saved_msg = "Einstieg gespeichert (lokal)."
        if not self._save_boarding():
            return
        self._after_boarding_change(saved_msg)

    def _reset_boarding(self):
        if self.current is None or self._busy:
            return
        bp.clear_origin(self.boarding_patches, self.current.get("id"))
        if not self._save_boarding():
            return
        self._after_boarding_change("Einstieg zurückgesetzt (lokal).")

    def _refresh_edges(self):
        if not hasattr(self, "edges"):
            return
        self.edges.delete(*self.edges.get_children())
        self._edge_rows = []
        if not self.current:
            return
        sid = self.current.get("id")
        for a, b in ep.consecutive_pairs(
            dl.traveled_stopovers(self._shown(self.current))
        ):
            a_id, b_id = a.get("id"), b.get("id")
            kind = ep.patch_kind(self.patches, sid, a_id, b_id)
            self._edge_rows.append({
                "from_id": a_id,
                "to_id": b_id,
                "from_name": a.get("name") or ep.station_name(
                    {}, a_id, _station_name(a)
                ),
                "to_name": b.get("name") or ep.station_name(
                    {}, b_id, _station_name(b)
                ),
                "kind": kind,
            })
        for i, row in enumerate(self._edge_rows):
            self.edges.insert(
                "", "end", iid=str(i),
                values=(
                    row["from_name"], row["to_name"],
                    EDGE_KIND_LABEL.get(row["kind"], row["kind"] or "—"),
                ),
            )
        kids = self.edges.get_children()
        if kids:
            self.edges.selection_set(kids[0])
            self.edges.focus(kids[0])

    def _selected_edge(self):
        sel = self.edges.selection() if hasattr(self, "edges") else ()
        if not sel:
            return None
        try:
            idx = int(sel[0])
        except (TypeError, ValueError):
            return None
        if idx < 0 or idx >= len(self._edge_rows):
            return None
        return self._edge_rows[idx]

    def _load_stations_file(self):
        try:
            with open(self.stations_path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            messagebox.showerror(
                "Stationen",
                f"{self.stations_path} nicht lesbar: {e}",
                parent=self.root,
            )
            return None
        return data if isinstance(data, dict) else {}

    def _on_patches_saved(self, patches):
        def apply():
            self.patches = patches
            self._refresh_edges()
            self._set_status("Kanten-Patch gespeichert (lokal).")
        try:
            self.root.after(0, apply)
        except tk.TclError:
            pass

    def _on_station_patches_saved(self, patches):
        def apply():
            self.station_patches = patches
            self._set_status("Stations-Patch gespeichert (lokal).")
        try:
            self.root.after(0, apply)
        except tk.TclError:
            pass

    def _open_station_map(self):
        stations = self._load_stations_file()
        if stations is None:
            messagebox.showerror(
                "Stationen",
                f"Keine stations.json unter {self.stations_path}. "
                "Erst einen Export mit Stationen machen.",
                parent=self.root,
            )
            return
        self.station_patches = sp.load_patches(self.station_patches_path)
        self.patches = ep.load_patches(self.edge_patches_path)
        if self._station_server is None:
            self._station_server = sp.StationMapService(
                self.station_patches_path, on_saved=self._on_station_patches_saved
            )
            if not self._station_server.start():
                self._station_server = None
                messagebox.showerror(
                    "Stationen",
                    f"Port {sp.PATCH_MAP_PORT} ist belegt. "
                    "Anderen Prozess beenden und erneut versuchen.",
                    parent=self.root,
                )
                return
        self._station_server.set_data(
            stations=stations,
            statuses=self.statuses,
            edge_patches=self.patches,
            patches=self.station_patches,
        )
        webbrowser.open(
            "%s?t=%s" % (
                self._station_server.url().rstrip("/"),
                int(datetime.datetime.now().timestamp() * 1000),
            )
        )
        self._set_status("Stations-Karte im Browser geöffnet.")

    def _open_edge_map(self):
        if self.current is None:
            return
        row = self._selected_edge()
        if row is None:
            messagebox.showinfo(
                "Kanten", "Bitte zuerst eine Kante in der Liste wählen.",
                parent=self.root,
            )
            return
        stations = self._load_stations_file()
        if stations is None:
            messagebox.showerror(
                "Kanten",
                f"Keine stations.json unter {self.stations_path}. "
                "Erst einen Export mit Stationen machen.",
                parent=self.root,
            )
            return
        self.station_patches = sp.load_patches(self.station_patches_path)
        stations = sp.apply_to_stations(self.station_patches, stations)
        self.patches = ep.load_patches(self.edge_patches_path)
        if self._patch_server is None:
            self._patch_server = ep.PatchMapService(
                self.edge_patches_path, on_saved=self._on_patches_saved
            )
            if not self._patch_server.start():
                self._patch_server = None
                messagebox.showerror(
                    "Kanten",
                    f"Port {ep.PATCH_MAP_PORT} ist belegt. "
                    "Anderen Prozess beenden und erneut versuchen.",
                    parent=self.root,
                )
                return
        served = ep.served_station_ids(dl.traveled_stopovers(self.current))
        self._patch_server.set_edge(
            status_id=self.current.get("id"),
            from_id=row["from_id"],
            to_id=row["to_id"],
            from_name=row["from_name"],
            to_name=row["to_name"],
            served_ids=served,
            stations=stations,
            patches=self.patches,
        )
        webbrowser.open(
            "%s?from=%s&to=%s&t=%s" % (
                self._patch_server.url().rstrip("/"),
                row["from_id"], row["to_id"],
                int(datetime.datetime.now().timestamp() * 1000),
            )
        )
        self._set_status("Patch-Karte im Browser geöffnet.")

    def _clear_edge_default(self):
        row = self._selected_edge()
        if row is None:
            return
        ep.clear_default(self.patches, row["from_id"], row["to_id"])
        if not ep.save_patches(self.edge_patches_path, self.patches):
            messagebox.showerror(
                "Kanten", "edge_patches.json nicht schreibbar.", parent=self.root
            )
            return
        self._refresh_edges()
        self._set_status("Standard-Patch gelöscht.")

    def _clear_edge_override(self):
        if self.current is None:
            return
        row = self._selected_edge()
        if row is None:
            return
        ep.clear_override(
            self.patches, self.current.get("id"), row["from_id"], row["to_id"]
        )
        if not ep.save_patches(self.edge_patches_path, self.patches):
            messagebox.showerror(
                "Kanten", "edge_patches.json nicht schreibbar.", parent=self.root
            )
            return
        self._refresh_edges()
        self._set_status("Fahrt-Override gelöscht.")

    def _col_at(self, event):
        region = self.trips.identify("region", event.x, event.y)
        if region != "cell":
            return None, None
        row = self.trips.identify_row(event.y)
        col_id = self.trips.identify_column(event.x)
        if not row or not col_id:
            return None, None
        try:
            idx = int(col_id.replace("#", "")) - 1
        except ValueError:
            return None, None
        if idx < 0 or idx >= len(TRIP_COLS):
            return None, None
        return row, TRIP_COLS[idx]

    def _on_trip_click(self, event):
        if self._busy:
            return
        row, col = self._col_at(event)
        if col in EDIT_COLS and row:
            self.root.after_idle(lambda r=row, c=col: self._begin_edit(r, c))

    def _on_f2(self, _event):
        sel = self.trips.selection()
        if not sel:
            return "break"
        self._begin_edit(sel[0], "loc")
        return "break"

    def _begin_edit(self, iid, col):
        if self._busy or col not in EDIT_COLS:
            return
        if self._edit_entry is not None:
            if self._edit_iid == iid and self._edit_col == col:
                return
            self._commit_edit()
        if not self.trips.exists(iid):
            return
        self.trips.see(iid)
        self.trips.selection_set(iid)
        bbox = self.trips.bbox(iid, col)
        if not bbox:
            return
        x, y, w, h = bbox
        value = self.trips.set(iid, col)
        entry = tk.Entry(self.trips, font=tkfont.nametofont("TkDefaultFont"))
        entry.place(x=x, y=y, width=max(w, 40), height=h)
        entry.insert(0, value)
        entry.select_range(0, "end")
        entry.focus_set()
        self._edit_entry = entry
        self._edit_iid = iid
        self._edit_col = col
        entry.bind("<Return>", lambda _e: self._commit_edit(move="down") or "break")
        entry.bind("<KP_Enter>", lambda _e: self._commit_edit(move="down") or "break")
        entry.bind("<Tab>", lambda _e: self._commit_edit(move="tab") or "break")
        entry.bind("<Shift-Tab>", lambda _e: self._commit_edit(move="back") or "break")
        entry.bind("<ISO_Left_Tab>", lambda _e: self._commit_edit(move="back") or "break")
        entry.bind("<Escape>", lambda _e: self._cancel_edit() or "break")
        entry.bind("<FocusOut>", self._on_edit_focus_out)

    def _on_edit_focus_out(self, _event):
        if self._edit_closing:
            return
        self.root.after_idle(self._commit_edit)

    def _cancel_edit(self):
        if self._edit_entry is None:
            return
        self._edit_closing = True
        self._edit_entry.destroy()
        self._edit_entry = None
        self._edit_iid = None
        self._edit_col = None
        self._edit_closing = False
        self.trips.focus_set()

    def _commit_edit(self, move=None):
        if self._edit_entry is None or self._edit_closing:
            return
        self._edit_closing = True
        value = self._edit_entry.get()
        iid = self._edit_iid
        col = self._edit_col
        self._edit_entry.destroy()
        self._edit_entry = None
        self._edit_iid = None
        self._edit_col = None
        self._edit_closing = False

        status = self._status_by_iid(iid)
        if status is not None and col in COL_TO_KEY:
            set_table_tag(status, COL_TO_KEY[col], value)
            if self.current is not None and self.current.get("id") == status.get("id"):
                # Tabelle ist Quelle für BR/Nummer; Detail-Tags bleiben die übrigen.
                pass
            self._refresh_trip_row(status)

        if move:
            self.root.after_idle(lambda: self._move_edit(iid, col, move))

    def _move_edit(self, iid, col, move):
        kids = list(self.trips.get_children())
        if not kids or iid not in kids:
            return
        idx = kids.index(iid)
        next_iid, next_col = iid, col
        if move == "down":
            if idx + 1 < len(kids):
                next_iid = kids[idx + 1]
        elif move == "tab":
            if col == "loc":
                next_col = "veh"
            elif idx + 1 < len(kids):
                next_iid = kids[idx + 1]
                next_col = "loc"
            else:
                return
        elif move == "back":
            if col == "veh":
                next_col = "loc"
            elif idx > 0:
                next_iid = kids[idx - 1]
                next_col = "veh"
            else:
                return
        else:
            return
        self._begin_edit(next_iid, next_col)

    def _reload_from_api(self):
        self._commit_edit()
        if self._any_dirty():
            if not messagebox.askyesno(
                "Von API laden",
                "Ungespeicherte Änderungen gehen verloren. Fortfahren?",
                parent=self.root,
            ):
                return

        def work():
            return list(dl.iter_statuses(
                self.username, self.token, limit=self.limit, since=self.since or None
            ))

        def done(statuses):
            self.statuses = statuses
            self.current = None
            self._reset_baselines()
            self._apply_filter()
            kids = self.trips.get_children()
            if kids:
                self.trips.selection_set(kids[0])
                self._on_select()
            self._set_status(f"{len(statuses)} Fahrten von der API geladen.")

        self._run_async(work, done, busy_msg="Lade Fahrten von der API …")

    def _dirty_statuses(self):
        self._commit_edit()
        self._flush_detail()
        return [s for s in self.statuses if self._status_dirty(s)]

    def _save(self, then=None):
        if self._busy:
            return
        dirty = self._dirty_statuses()
        if not dirty:
            self._set_status("Nichts zu speichern.")
            if then:
                then()
            return
        for s in dirty:
            body = s.get("body") or ""
            if len(body) > BODY_MAX:
                messagebox.showerror(
                    "Status-Text",
                    f"Fahrt {s.get('id')}: Text ist {len(body)} Zeichen "
                    f"(Maximum {BODY_MAX}).",
                    parent=self.root,
                )
                return

        xfer = TransferWindow(
            self.root,
            [(s.get("id"), _trip_short(s)) for s in dirty],
        )
        self._xfer = xfer

        def work():
            ok = []
            errors = []
            for s in dirty:
                sid = s.get("id")
                self.root.after(0, lambda sid=sid: xfer.set_row(sid, "running"))
                try:
                    updated = apply_status_edits(
                        self.token, sid, s.get("body") or "", s.get("tags") or [],
                    )
                    ok.append((sid, updated))
                    self.root.after(
                        0, lambda sid=sid, u=updated: self._apply_saved_status(sid, u, xfer)
                    )
                except Exception as e:
                    errors.append((sid, e))
                    msg = format_api_error(e)
                    self.root.after(
                        0,
                        lambda sid=sid, msg=msg: self._xfer_fail(xfer, sid, msg),
                    )
            return ok, errors

        def done(result):
            ok, errors = result
            merges = {}
            for sid, updated in ok:
                new_body = updated.get("body") or ""
                new_tags = [
                    _norm_tag(t) for t in (updated.get("tags") or []) if t.get("key")
                ]
                merges[sid] = (new_body, new_tags)
            local_n = 0
            local_err = None
            if merges:
                try:
                    local_n = merge_local_body_tags_many(self.statuses_path, merges)
                except OSError as e:
                    local_err = e
                    local_n = -1
                    for sid, _ in ok:
                        xfer.set_row(sid, "local", f"Server ok, Datei nicht: {e}")
            bits = [f"{len(ok)}/{len(dirty)} Fahrten gespeichert"]
            if local_n > 0:
                bits.append(f"lokal {local_n} in {self.statuses_path}")
            if local_err:
                bits.append(f"lokal nicht geschrieben ({local_err})")
            if errors:
                bits.append(f"{len(errors)} Fehler")
            summary = "; ".join(bits) + "."
            self._set_status(summary)
            if xfer.winfo_exists():
                xfer.finish(summary)
            self._xfer = None
            if then and not errors:
                then()

        self._run_async(work, done, busy_msg="Speichere nach Träwelling …")

    def _apply_saved_status(self, sid, updated, xfer):
        """Übernimmt eine erfolgreich gesendete Fahrt in die Tabelle (UI-Thread)."""
        new_body = updated.get("body") or ""
        new_tags = [
            _norm_tag(t) for t in (updated.get("tags") or []) if t.get("key")
        ]
        for s in self.statuses:
            if s.get("id") == sid:
                s["body"] = new_body
                s["tags"] = new_tags
                self._baseline[sid] = _snapshot_status(s)
                self._original_tags[sid] = list(new_tags)
                self._refresh_trip_row(s)
                if self.current is not None and self.current.get("id") == sid:
                    self._load_status(s)
                break
        if xfer.winfo_exists():
            xfer.set_row(sid, "ok")
            xfer.bump()

    def _xfer_fail(self, xfer, sid, msg):
        if xfer.winfo_exists():
            xfer.set_row(sid, "error", msg)
            xfer.bump()

    def _open_home_region(self):
        saved = hr.load_operators(self.home_region_path)
        names = hr.collect_operator_names(self.statuses, saved)
        dlg = HomeRegionDialog(self.root, names, saved)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        if not hr.save_operators(self.home_region_path, dlg.result):
            messagebox.showerror(
                "Heimatregion",
                "home_region.json nicht schreibbar.",
                parent=self.root,
            )
            return
        n = len(dlg.result)
        self._set_status(
            f"Heimatregion gespeichert ({n} Operatoren, lokal). "
            "Dashboard neu bauen, damit der Filter sie nutzt."
        )

    def _open_vehicle_roster(self):
        roster = vr.load_roster(self.vehicle_roster_path)
        names = vr.collect_loc_classes(self.statuses, (roster.get("types") or {}).keys())
        dlg = VehicleRosterDialog(self.root, roster, names)
        self.root.wait_window(dlg)
        if dlg.result is None:
            return
        if not vr.save_roster(self.vehicle_roster_path, {"types": dlg.result}):
            messagebox.showerror(
                "Fuhrpark",
                "vehicle_roster.json nicht schreibbar.",
                parent=self.root,
            )
            return
        n_types = len(dlg.result)
        n_nums = sum(len(entries) for entries in dlg.result.values())
        self._set_status(
            f"Fuhrpark gespeichert ({n_nums} Nummern in {n_types} Baureihen, lokal). "
            "Dashboard neu bauen, damit die Fahrzeuge-Seite ihn nutzt."
        )

    def _on_rebuild_dashboard(self):
        self._commit_edit()
        if self._any_dirty():
            ans = messagebox.askyesnocancel(
                "Dashboard neu bauen",
                "Es gibt ungespeicherte Änderungen, die nicht in der Datei stehen.\n\n"
                "Ja: zuerst nach Träwelling speichern, dann Dashboard bauen.\n"
                "Nein: Dashboard aus der vorhandenen Datei bauen.\n"
                "Abbrechen: nichts tun.",
                parent=self.root,
            )
            if ans is None:
                return
            if ans:
                self._save(then=self._build_dashboard)
                return
        self._build_dashboard()

    def _build_dashboard(self):
        if not os.path.isfile(self.statuses_path):
            messagebox.showerror(
                "Dashboard",
                f"Keine Status-Datei {self.statuses_path}. "
                "Erst speichern oder einen Export machen.",
                parent=self.root,
            )
            return
        argv = [
            "--statuses", self.statuses_path,
            "--stations", self.stations_path,
            "-o", self.dashboard_path,
            "--open",
            "--loc-class-families", self.loc_class_families,
            "--edge-patches", self.edge_patches_path,
            "--station-patches", self.station_patches_path,
            "--line-color-patches", self.line_color_patches_path,
            "--home-region", self.home_region_path,
            "--boarding-patches", self.boarding_patches_path,
            "--vehicle-roster", self.vehicle_roster_path,
        ]
        if self.ignore_plus:
            argv.append("--ignore-plus")

        def work():
            rc = build_dashboard.main(argv)
            return rc

        def done(rc):
            if rc:
                self._set_status(f"Dashboard-Bau fehlgeschlagen (Exit {rc}).")
                messagebox.showerror(
                    "Dashboard",
                    f"build_dashboard endete mit Code {rc}.",
                    parent=self.root,
                )
            else:
                self._set_status(f"Dashboard geschrieben: {self.dashboard_path}")

        self._run_async(work, done, busy_msg="Baue Dashboard …")

    def _on_close(self):
        if self._busy:
            return
        self._commit_edit()
        if self._any_dirty():
            ans = messagebox.askyesnocancel(
                "Beenden",
                "Ungespeicherte Änderungen. Jetzt nach Träwelling speichern?",
                parent=self.root,
            )
            if ans is None:
                return
            if ans:
                self._save(then=self.root.destroy)
                return
        self.root.destroy()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"walita {__version__}",
        help="Version ausgeben und beenden.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TRWL_TOKEN"),
        help="Personal Access Token (sonst Env TRWL_TOKEN). Braucht write-statuses.",
    )
    parser.add_argument(
        "--login", action="store_true",
        help="OAuth-Login im Browser erzwingen (Scopes read-statuses write-statuses).",
    )
    parser.add_argument(
        "--logout", action="store_true",
        help="Gespeichertes OAuth-Token löschen und beenden.",
    )
    parser.add_argument(
        "--client-id", default=os.environ.get("TRWL_CLIENT_ID") or auth.DEFAULT_CLIENT_ID,
        help="OAuth-Client-ID (sonst Env TRWL_CLIENT_ID bzw. auth.DEFAULT_CLIENT_ID).",
    )
    parser.add_argument(
        "--redirect-uri", default=os.environ.get("TRWL_REDIRECT_URI") or auth.DEFAULT_REDIRECT,
        help="OAuth-Redirect-URL, muss exakt zum registrierten Client passen.",
    )
    parser.add_argument(
        "--manual", action="store_true",
        help="OAuth-Code manuell einfügen statt lokalem Callback-Server.",
    )
    parser.add_argument(
        "--oauth-token-file", default="data/oauth_token.json",
        help="Ablageort des OAuth-Tokens (Default: data/oauth_token.json).",
    )
    parser.add_argument(
        "--scopes", default=EDITOR_SCOPES,
        help=f"OAuth-Scopes (Default: {EDITOR_SCOPES}).",
    )
    parser.add_argument(
        "--statuses", default="data/statuses.json",
        help="Lokale Status-Datei zum Anzeigen/Mergen (Default: data/statuses.json).",
    )
    parser.add_argument(
        "--stations", default="data/stations.json",
        help="stations.json fürs Dashboard-Neu-Bauen (Default: data/stations.json).",
    )
    parser.add_argument(
        "--dashboard-output", default="data/dashboard.html",
        help="Zieldatei fürs Dashboard (Default: data/dashboard.html).",
    )
    parser.add_argument(
        "--loc-class-families", default="loc_class_families.txt",
        help="Baureihe→Familie fürs Dashboard (Default: loc_class_families.txt).",
    )
    parser.add_argument(
        "--ignore-plus", action="store_true",
        help="Wagennummern-Tags beim Dashboard-Bau nicht am '+' trennen.",
    )
    parser.add_argument(
        "--edge-patches", default="data/edge_patches.json",
        help="Lokale Kanten-Patches (Default: data/edge_patches.json).",
    )
    parser.add_argument(
        "--station-patches", default="data/station_patches.json",
        help="Lokale Stations-Patches (Default: data/station_patches.json).",
    )
    parser.add_argument(
        "--line-color-patches", default="data/line_color_patches.json",
        help="Lokale Linienfarben-Patches (Default: data/line_color_patches.json).",
    )
    parser.add_argument(
        "--home-region", default="data/home_region.json",
        help="Lokale Operator-Liste der Heimatregion (Default: data/home_region.json).",
    )
    parser.add_argument(
        "--boarding-patches", default="data/boarding_patches.json",
        help="Lokale Einstiegs-Patches (Default: data/boarding_patches.json).",
    )
    parser.add_argument(
        "--vehicle-roster", default="data/vehicle_roster.json",
        help="Lokaler Fuhrpark je Baureihe (Default: data/vehicle_roster.json).",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max. Anzahl Statuses beim Laden von der API.",
    )
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", default="",
        help="Nur Statuses mit Abfahrt strikt nach diesem Tag (API-Laden).",
    )
    return parser.parse_args(argv)


def resolve_token(args):
    """PAT oder OAuth; gleicher Ablauf wie download_statuses.main."""
    client_secret = os.environ.get("TRWL_CLIENT_SECRET")
    if args.token and not args.login:
        return args.token
    return auth.get_access_token(
        args.oauth_token_file, args.client_id, redirect=args.redirect_uri,
        scopes=args.scopes, secret=client_secret, force_login=args.login,
        manual=args.manual,
    )


def main(argv=None):
    args = parse_args(argv)

    if args.logout:
        return dl.main(["--logout", "--oauth-token-file", args.oauth_token_file])

    if args.since:
        try:
            datetime.datetime.strptime(args.since, "%Y-%m-%d")
        except ValueError:
            log("Fehler: --since erwartet ein Datum im Format YYYY-MM-DD.")
            return 2

    try:
        token = resolve_token(args)
    except auth.AuthError as e:
        log(f"OAuth-Login fehlgeschlagen: {e}")
        return 1
    if not token:
        log("Fehler: Kein Token. Setze TRWL_TOKEN, nutze --token oder --login.")
        return 2

    try:
        username = dl.get_username(token)
    except dl.ApiError as e:
        log(f"Authentifizierung fehlgeschlagen: {e}")
        return 1
    log(f"Angemeldet als: {username}")

    statuses = load_local_statuses(args.statuses)
    if statuses is None:
        log("Keine lokale statuses.json – lade Liste von der API …")
        try:
            statuses = list(dl.iter_statuses(
                username, token, limit=args.limit, since=args.since or None
            ))
        except dl.ApiError as e:
            log(f"Abruf der Statuses fehlgeschlagen: {e}")
            return 1
        log(f"{len(statuses)} Statuses von der API geladen.")
    else:
        log(f"{len(statuses)} Statuses aus {args.statuses} geladen.")

    _enable_dpi_awareness()
    root = tk.Tk()
    EditorApp(
        root, token, username, statuses, args.statuses,
        limit=args.limit, since=args.since,
        stations_path=args.stations,
        dashboard_path=args.dashboard_output,
        loc_class_families=args.loc_class_families,
        ignore_plus=args.ignore_plus,
        edge_patches_path=args.edge_patches,
        station_patches_path=args.station_patches,
        line_color_patches_path=args.line_color_patches,
        home_region_path=args.home_region,
        boarding_patches_path=args.boarding_patches,
        vehicle_roster_path=args.vehicle_roster,
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Abgebrochen.", file=sys.stderr, flush=True)
        sys.exit(130)
