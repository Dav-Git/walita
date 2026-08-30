#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 besuka97
"""OAuth-2.0-Login (Authorization Code + PKCE) für die Träwelling-API.

Gleichwertiger Auth-Weg neben dem Personal Access Token: holt im Browser ein
Zugangstoken und legt es lokal ab (Default `data/oauth_token.json`), sodass
folgende Läufe es wiederverwenden und bei Ablauf per Refresh-Token erneuern.

Träwelling nutzt Laravel Passport mit den Standard-Endpoints unter
`https://traewelling.de/oauth/{authorize,token}`. Für einen CLI-Client wird der
Authorization-Code-Flow mit PKCE (öffentlicher Client, ohne Secret) verwendet; ein
optionales Client-Secret wird nur mitgeschickt, falls ausdrücklich gesetzt.

Nur Standardbibliothek. Fortschritt/Logs gehen auf stderr, damit stdout sauber bleibt.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

from version import USER_AGENT

AUTHORIZE_URL = "https://traewelling.de/oauth/authorize"
TOKEN_URL = "https://traewelling.de/oauth/token"

# Öffentlicher PKCE-Client dieses Projekts – Nutzer müssen keine eigene App in
# Träwelling anlegen; --login reicht. Überschreibbar via --client-id / TRWL_CLIENT_ID.
DEFAULT_CLIENT_ID = "360"
# Muss exakt so beim registrierten Client hinterlegt sein (Passport prüft strikt).
# http-Loopback -> automatischer lokaler Callback-Server, kein Kopieren nötig.
DEFAULT_REDIRECT = "http://127.0.0.1:8710/callback"
# Deckt beide gelesenen Endpoints ab: die Status-Liste und /stopovers.
DEFAULT_SCOPES = "read-statuses"


def log(msg):
    """Fortschrittsausgabe auf stderr."""
    print(msg, file=sys.stderr, flush=True)


class AuthError(Exception):
    """Fehler im OAuth-Ablauf (Registrierung, Token-Austausch, Refresh)."""


# --------------------------------------------------------------------------- PKCE
def _b64url(raw):
    """base64url ohne Padding (RFC 7636)."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _pkce_pair():
    """(code_verifier, code_challenge) für PKCE mit S256."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ------------------------------------------------------------------- Token-Cache
def load_token(path):
    """Liest die Token-Datei oder gibt {} zurück (fehlt/unlesbar/falscher Typ)."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        log(f"Token-Cache {path} nicht lesbar ({e}).")
        return {}
    if not isinstance(data, dict):
        log(f"Token-Cache {path} enthält kein JSON-Objekt "
            f"({type(data).__name__}) – wird ignoriert.")
        return {}
    return data


def save_token(path, data):
    """Schreibt die Token-Datei über eine temporäre Datei mit Rechten 0600.

    Der abschließende `os.replace` ist atomar: die Zieldatei ist entweder komplett
    oder unangetastet. Schreibfehler werden geloggt, nicht geworfen – das Token des
    laufenden Aufrufs bleibt nutzbar. Gibt True zurück, wenn geschrieben wurde.
    """
    parent = os.path.dirname(path)
    tmp = path + ".tmp"
    try:
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        log(f"Warnung: Token-Cache {path} nicht schreibbar ({e}).")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # z.B. auf Windows/Netzlaufwerken nicht immer möglich
    return True


def token_valid(data):
    """True, wenn ein Access-Token vorliegt und (mit 60s Puffer) nicht abgelaufen ist."""
    if not isinstance(data, dict) or not data.get("access_token"):
        return False
    exp = data.get("expires_at")
    if exp is None:
        return True  # keine Ablaufzeit bekannt -> optimistisch als gültig behandeln
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        log(f"Token-Cache: expires_at ist kein Zahlenwert ({exp!r}) – "
            f"Ablaufzeit wird ignoriert.")
        return True
    return time.time() + 60 < exp


# ------------------------------------------------------------- Token-Endpoint
def _post_token(fields):
    """POST (form-urlencoded) an den Token-Endpoint, gibt das angereicherte Dict zurück."""
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_URL,
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise AuthError(f"Token-Endpoint HTTP {e.code}: {detail[:300]}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AuthError(f"Netzwerkfehler beim Token-Austausch: {e}") from e

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise AuthError(f"Token-Endpoint lieferte kein gültiges JSON ({e}).") from e
    if not isinstance(payload, dict):
        raise AuthError(
            f"Token-Endpoint lieferte kein JSON-Objekt ({type(payload).__name__})."
        )

    if not payload.get("access_token"):
        # Nur die Feldnamen loggen: eine unvollständige Antwort könnte ein
        # refresh_token enthalten, das nicht in Logs oder Tracebacks gehört.
        raise AuthError(
            f"Antwort ohne access_token (Felder: {sorted(payload)}), "
            f"error={payload.get('error')!r}"
        )
    # Ablaufzeitpunkt aus expires_in ableiten (Sekunden ab jetzt).
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)):
        payload["expires_at"] = int(time.time() + expires_in)
    return payload


def exchange_code(client_id, redirect, code, verifier, secret=None):
    """Tauscht den Authorization-Code gegen ein Token (PKCE)."""
    fields = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "code": code,
        "code_verifier": verifier,
    }
    if secret:
        fields["client_secret"] = secret
    return _post_token(fields)


def refresh(client_id, refresh_token, scopes=None, secret=None):
    """Erneuert das Token per refresh_token."""
    if not client_id:
        raise AuthError(
            "Keine client_id für den Refresh bekannt (weder --client-id / "
            "TRWL_CLIENT_ID noch im Token-Cache)."
        )
    if not refresh_token:
        raise AuthError("Kein refresh_token vorhanden.")
    fields = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "refresh_token": refresh_token,
    }
    if scopes:
        fields["scope"] = scopes
    if secret:
        fields["client_secret"] = secret
    return _post_token(fields)


# ------------------------------------------------------- Interaktiver Login
class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Nimmt den Redirect an und legt die Query-Parameter in server.result."""

    def do_GET(self):  # noqa: N802 (von BaseHTTPRequestHandler vorgegeben)
        parsed = urllib.parse.urlparse(self.path)
        # Nur der Callback-Pfad zählt; /favicon.ico & Co. sollen server.result nicht
        # setzen, sonst gilt der Login als beantwortet, ohne dass ein Code da ist.
        expected = getattr(self.server, "callback_path", "/callback")
        params = urllib.parse.parse_qs(parsed.query)
        if parsed.path not in (expected, "/") or not (
            "code" in params or "error" in params
        ):
            self.send_response(404)
            self.end_headers()
            return
        self.server.result = {k: v[0] for k, v in params.items()}
        ok = "code" in self.server.result
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        msg = ("Login erfolgreich – du kannst dieses Fenster schließen."
               if ok else "Login fehlgeschlagen – bitte im Terminal weiterlesen.")
        try:
            self.wfile.write(
                f"<!doctype html><meta charset='utf-8'><title>Träwelling</title>"
                f"<body style='font:16px system-ui;padding:2rem'>{msg}</body>".encode("utf-8")
            )
        except OSError:
            pass  # Client schon weg – der Code steckt bereits in server.result

    def log_message(self, *args):
        pass  # Standard-Logging des Servers unterdrücken


def _prompt(msg):
    """Frage auf stderr ausgeben und eine Zeile von stdin lesen (stdout bleibt sauber)."""
    print(msg, file=sys.stderr, flush=True)
    try:
        return input().strip()
    except EOFError:
        return ""


CALLBACK_TIMEOUT = 180  # Sekunden, die auf den Browser-Callback gewartet wird


def _bind_callback_server(host, port, callback_path):
    """Bindet den lokalen Callback-Server; gibt den Server zurück oder None."""
    try:
        server = http.server.HTTPServer((host, port), _CallbackHandler)
    except OSError as e:
        log(f"Lokaler Callback-Server nicht startbar auf {host}:{port} ({e}).")
        return None
    server.result = None
    server.callback_path = callback_path or "/callback"
    return server


def _wait_for_callback(server):
    """Bedient Anfragen bis zum Callback, längstens `CALLBACK_TIMEOUT` Sekunden.

    Der Browser fragt vor dem Redirect oft noch anderes an (favicon, Prefetch);
    solche Anfragen werden mitbedient, bis der Callback kommt. Gibt die
    Query-Parameter zurück oder None – dann übernimmt der manuelle Paste-Weg.
    """
    deadline = time.monotonic() + CALLBACK_TIMEOUT
    try:
        while server.result is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            server.timeout = remaining
            server.handle_request()  # kehrt bei Timeout ohne Ergebnis zurück
    finally:
        server.server_close()
    if server.result is None:
        log(f"Kein Callback innerhalb von {CALLBACK_TIMEOUT}s empfangen.")
    return server.result


def _manual_capture(redirect):
    """Nutzer fügt die weitergeleitete URL (oder nur den code) von Hand ein.

    Fallback wenn der lokale Callback-Server nicht greift (z.B. WSL/headless) oder
    `--manual` gesetzt ist. Die Zielseite muss nicht laden – der Code steht in der
    Adresszeile.
    """
    log("Melde dich im Browser an. Danach wirst du weitergeleitet – die Zielseite muss")
    log("NICHT laden. Kopiere die komplette Adresse aus der Browserzeile (beginnt mit")
    log(f"{redirect} und enthält ?code=…) und füge sie hier ein (oder nur den code-Wert):")
    pasted = _prompt("URL/Code:")
    if not pasted:
        return {}
    if "?" in pasted or pasted.lower().startswith("http"):
        query = urllib.parse.urlparse(pasted).query
        return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}
    return {"code": pasted}


def interactive_login(client_id, redirect, scopes, secret=None, manual=False):
    """Führt den Browser-Login (Authorization Code + PKCE) aus und gibt ein Token-Dict zurück.

    Fängt den Redirect über einen lokalen Callback-Server ab (Default:
    http-Loopback) oder – bei `manual=True`, nicht-http-Loopback-Redirects oder
    wenn der Server nicht startet – über manuelles Einfügen der Weiterleitungs-URL.
    """
    if not client_id:
        raise AuthError(
            "Keine client_id gesetzt. Normalerweise greift auth.DEFAULT_CLIENT_ID "
            f"(Redirect {redirect}). Sonst --client-id / TRWL_CLIENT_ID setzen."
        )

    parts = urllib.parse.urlparse(redirect)
    host = parts.hostname or "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    # Automatischer Callback nur für http-Loopback; sonst manuelles Einfügen.
    is_loopback_http = parts.scheme == "http" and host in ("127.0.0.1", "localhost", "::1")

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })

    # Server binden, bevor der Browser startet: sonst kann der Redirect den Port
    # erreichen, während dort noch nichts lauscht.
    server = None
    if not manual and is_loopback_http:
        server = _bind_callback_server(host, port, parts.path)
        if server is None:
            log("Wechsle auf manuelles Einfügen der Weiterleitungs-URL.")

    log("Öffne den Browser für den Träwelling-Login …")
    log("Falls sich nichts öffnet, diese URL manuell aufrufen:")
    log("  " + auth_url)
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass  # z.B. headless/WSL ohne Browser -> Nutzer öffnet die URL manuell

    result = None
    from_server = False
    if server is not None:
        result = _wait_for_callback(server)
        if result is None:
            log("Wechsle auf manuelles Einfügen der Weiterleitungs-URL.")
        else:
            from_server = True
    if result is None:
        result = _manual_capture(redirect)
    result = result or {}

    if result.get("error"):
        raise AuthError(
            f"Login abgelehnt/fehlgeschlagen: {result.get('error')} "
            f"{result.get('error_description', '')}".strip()
        )
    # Beim manuellen Einfügen darf state fehlen (wer nur den Code einsetzt, hat keins);
    # aus dem Callback kommt es immer mit, dort ist ein Fehlen also ein Fehler.
    got_state = result.get("state")
    if from_server and got_state is None:
        raise AuthError("Callback ohne state erhalten (möglicher CSRF) – Login abgebrochen.")
    if got_state is not None and got_state != state:
        raise AuthError("State stimmt nicht überein (möglicher CSRF) – Login abgebrochen.")
    code = result.get("code")
    if not code:
        raise AuthError("Kein Authorization-Code erhalten.")

    log("Code erhalten, tausche gegen Zugangstoken …")
    return exchange_code(client_id, redirect, code, verifier, secret=secret)


# ----------------------------------------------------------- High-Level-Einstieg
def _token_has_scopes(data, required):
    """True, wenn das Token alle in `required` genannten Scopes trägt.

    Fehlt das `scope`-Feld (oder ist es kein String), gilt das Token als
    ausreichend – der API-Call entscheidet dann.
    """
    granted = data.get("scope")
    if not isinstance(granted, str):
        return True
    return set((required or "").split()) <= set(granted.split())


def get_access_token(path, client_id, redirect=DEFAULT_REDIRECT, scopes=DEFAULT_SCOPES,
                     secret=None, force_login=False, manual=False):
    """Liefert ein gültiges Access-Token (String) und pflegt den Token-Cache in `path`.

    Ablauf: gültiges gecachtes Token nutzen → sonst per refresh_token erneuern →
    sonst (oder bei force_login) interaktiven Browser-Login starten. Das Ergebnis wird
    jeweils in `path` gespeichert. Fehlen am Cache die angeforderten Scopes, wird neu
    eingeloggt – Refresh erweitert Scopes nicht.
    """
    data = {} if force_login else load_token(path)
    has_scopes = _token_has_scopes(data, scopes)

    if not force_login and token_valid(data) and has_scopes:
        return data["access_token"]

    # Ein Refresh erweitert die Scopes nicht; fehlen sie, hilft nur ein Login.
    if not force_login and data.get("access_token") and not has_scopes:
        log(
            f"Gespeichertes Token hat nicht die benötigten Scopes "
            f"({data.get('scope')!r}, braucht {scopes!r}) – neuer Login …"
        )
    # Abgelaufen, aber Refresh möglich: ohne Browser erneuern.
    elif not force_login and data.get("refresh_token"):
        refresh_client_id = client_id or data.get("client_id")
        try:
            log("Zugangstoken abgelaufen – erneuere per Refresh-Token …")
            new = refresh(refresh_client_id, data["refresh_token"],
                          scopes=scopes, secret=secret)
            # Passport schickt nur manchmal ein neues refresh_token mit; fehlt es,
            # gilt das gespeicherte weiter.
            new.setdefault("refresh_token", data["refresh_token"])
            new.setdefault("client_id", refresh_client_id)
            save_token(path, new)
            if _token_has_scopes(new, scopes):
                return new["access_token"]
            log(
                f"Refresh lieferte unzureichende Scopes "
                f"({new.get('scope')!r}, braucht {scopes!r}) – neuer Login …"
            )
        except AuthError as e:
            log(f"Refresh fehlgeschlagen ({e}) – interaktiver Login nötig.")

    # Interaktiver Login als Fallback / bei force_login / fehlenden Scopes.
    token = interactive_login(client_id, redirect, scopes, secret=secret, manual=manual)
    token.setdefault("client_id", client_id)
    save_token(path, token)
    log(f"Zugangstoken gespeichert: {path}")
    return token["access_token"]
