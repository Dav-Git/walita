#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""Lokaler Tag-Editor: Tags und Status-Text nach Träwelling synchronisieren.

Die Fahrtliste zeigt Baureihe und Fahrzeugnummer direkt; Zellen werden lokal
gestagt. Speichern schreibt den Diff live auf den Server. Laufweg/`trip`
bleibt unangetastet.

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
from tkinter import font as tkfont
from tkinter import messagebox, ttk
import urllib.parse

import auth
import build_dashboard
import download_statuses as dl
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

        keys = [k for k in TAG_KEY_SUGGESTIONS if k not in TABLE_TAG_SET]
        for k in extra_keys or ():
            if k and k not in keys and k not in TABLE_TAG_SET:
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
        vis = VISIBILITY_BY_LABEL.get(self.vis_var.get(), 0)
        self.result = {"key": key, "value": self.val_var.get(), "visibility": vis}
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class EditorApp:
    def __init__(self, root, token, username, statuses, statuses_path, limit, since,
                 stations_path="data/stations.json", dashboard_path="data/dashboard.html",
                 loc_class_families="loc_class_families.txt", ignore_plus=False):
        self.root = root
        self.token = token
        self.username = username
        self.statuses = list(statuses or [])
        self.statuses_path = statuses_path
        self.stations_path = stations_path
        self.dashboard_path = dashboard_path
        self.loc_class_families = loc_class_families
        self.ignore_plus = ignore_plus
        self.limit = limit
        self.since = since
        self.filtered = []
        self.current = None
        self._busy = False
        self._tag_rows = []
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
            "loc": 80, "veh": 140,
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

        ttk.Separator(right, orient="horizontal").pack(fill="x", pady=8)

        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text="Status-Text").pack(side="left")
        self.body_count = tk.StringVar(value="0/280")
        ttk.Label(head, textvariable=self.body_count).pack(side="right")

        self.body = tk.Text(right, height=4, wrap="word", undo=True)
        self.body.pack(fill="x")
        self.body.bind("<KeyRelease>", lambda _e: self._on_body_changed())

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

        act = ttk.Frame(outer)
        act.pack(fill="x", pady=(8, 0))
        self.save_btn = ttk.Button(act, text="Speichern", command=self._save)
        self.save_btn.pack(side="left")
        self.dash_btn = ttk.Button(
            act, text="Dashboard neu bauen", command=self._on_rebuild_dashboard
        )
        self.dash_btn.pack(side="left", padx=8)
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
            self.filtered = [s for s in self.statuses if q in _search_blob(s)]
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
            self.trips.insert("", "end", iid=iid, values=_trip_values(s), tags=tags)
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
        text = (_trip_values(status)[idx] or "").strip()
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
            iid, values=_trip_values(status),
            tags=("dirty",) if self._status_dirty(status) else (),
        )

    def _selected_status(self):
        sel = self.trips.selection()
        if not sel:
            return None
        return self._status_by_iid(sel[0])

    def _flush_detail(self):
        if self.current is None or not hasattr(self, "body"):
            return
        self.current["body"] = self.body.get("1.0", "end-1c")
        table_tags = [
            _norm_tag(t) for t in (self.current.get("tags") or [])
            if _norm_tag(t)["key"] in TABLE_TAG_SET
        ]
        other = [t for t in self._tag_rows if t.get("key") not in TABLE_TAG_SET]
        self.current["tags"] = table_tags + other
        self._refresh_trip_row(self.current)

    def _clear_detail(self):
        self.meta_var.set("Keine Fahrt gewählt.")
        self.body.delete("1.0", "end")
        self._update_body_count()
        self._tag_rows = []
        self._refresh_extra_tree()

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
        bits = _checkin_bits(status)
        self.meta_var.set(
            f"{bits['line']}\n"
            f"{bits['origin']} → {bits['dest']}\n"
            f"{_fmt_when(bits['dep'])}  →  {_fmt_when(bits['arr'])}"
        )
        body = status.get("body") or ""
        self.body.delete("1.0", "end")
        self.body.insert("1.0", body)
        self._update_body_count()
        self._tag_rows = [
            _norm_tag(t) for t in (status.get("tags") or [])
            if _norm_tag(t)["key"] and _norm_tag(t)["key"] not in TABLE_TAG_SET
        ]
        self._refresh_extra_tree()

    def _on_body_changed(self):
        self._update_body_count()
        self._flush_detail()

    def _update_body_count(self):
        n = len(self.body.get("1.0", "end-1c"))
        self.body_count.set(f"{n}/{BODY_MAX}")

    def _refresh_extra_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, t in enumerate(self._tag_rows):
            self.tree.insert(
                "", "end", iid=str(i),
                values=(t["key"], t["value"], VISIBILITY_LABEL.get(t["visibility"], t["visibility"])),
            )

    def _suggestion_keys(self):
        extra = [t["key"] for t in self._tag_rows]
        seen = set(extra)
        for s in self.statuses:
            for t in s.get("tags") or []:
                k = (t.get("key") or "").strip()
                if k and k not in seen and k not in TABLE_TAG_SET:
                    extra.append(k)
                    seen.add(k)
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
    )
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("Abgebrochen.", file=sys.stderr, flush=True)
        sys.exit(130)
