# Walita – Wagen-Linien-Tabellen für Träwelling

[![Lizenz: GPL v3](https://img.shields.io/badge/Lizenz-GPLv3-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![Keine Abhängigkeiten](https://img.shields.io/badge/Abh%C3%A4ngigkeiten-keine-brightgreen.svg)](#voraussetzungen)

Träwelling Status Export & Statistik-Dashboard

`walita.py` ist der Einstiegspunkt: Es lädt die **eigenen Statuses** von
[Träwelling](https://traewelling.de/api/documentation) (inkl. Zwischenhalte),
schreibt sie unter `data/` und erzeugt daraus ein in sich geschlossenes
**HTML-Dashboard**.

![Karte des Dashboards mit Befahrungs-Heatmap](docs/screenshots/02-karte-berlin.jpg)

Die beiden Stufen können auch einzeln genutzt werden:

| Skript | Aufgabe |
| --- | --- |
| `walita.py` | Export + Dashboard (empfohlen) |
| `download_statuses.py` | Stufe 1: API → `statuses.json` / `stations.json` / `trips.json` |
| `build_dashboard.py` | Stufe 2: JSON → `dashboard.html` |
| `auth.py` | OAuth (Authorization Code + PKCE), von den anderen Skripten genutzt |

## Installation

Es gibt keinen Installationsschritt – Repo klonen und starten:

```bash
git clone https://github.com/besuka97/walita.git
cd walita
python3 walita.py --demo    # ohne Token ausprobieren
```

## Projektstruktur

```
.
├── walita.py              # Einstiegspunkt: Export + Dashboard
├── download_statuses.py   # Stufe 1: Export von der Träwelling-API
├── auth.py                # OAuth-Login (PKCE)
├── build_dashboard.py     # Stufe 2: Dashboard aus den JSON-Dateien
├── version.py             # Version + User-Agent
├── operator_replacements.json  # manuelle Operator-Namen-Ersetzungen
├── examples/              # Demo-Dataset (eingecheckt, siehe Demo)
│   ├── statuses.json
│   └── stations.json
├── docs/screenshots/      # Bilder fürs README
├── data/                  # generierte Artefakte – nicht im Repo (.gitignore)
├── README.md · LICENSE · .gitignore · .gitattributes
```

> **Hinweis:** Eigene Reisedaten unter `data/` (`statuses.json`, `stations.json`,
> `trips.json`, `dashboard.html`, `oauth_token.json`) sind persönlich und
> gitignored. Zum Ausprobieren ohne Token: [Demo](#demo).

## Voraussetzungen

- Python 3.8+ (nur Standardbibliothek, keine Installation nötig).
- Zugang zur Träwelling-API auf **einem** von zwei Wegen:
  - **Personal Access Token** mit dem Scope `read-statuses`
    (unter <https://traewelling.de/settings/applications>), oder
  - **OAuth-Login** (`walita.py --login`) – fordert denselben Scope an.
    Es ist bereits ein öffentlicher Client im Repo hinterlegt; **eigene App in
    Träwelling registrieren ist nicht nötig.**

  Walita liest ausschließlich: die eigene Status-Liste und deren Zwischenhalte.
  Schreibrechte werden nicht angefordert.

## Schnellstart

```bash
python3 walita.py --login             # einmalig: OAuth, dann Export + Dashboard
python3 walita.py                     # Update: Token/Caches nutzen, Dashboard öffnen
python3 walita.py --since 2026-01-01  # nur Fahrten ab dem 02.01.2026
python3 walita.py --demo              # Demo ohne Token
```

Mit Personal Access Token statt OAuth:

```bash
export TRWL_TOKEN="dein-token"
python3 walita.py
```

> Nutze für den PAT möglichst `TRWL_TOKEN` und nicht `--token`: ein Token auf der
> Kommandozeile landet in der Shell-History und ist während des Laufs in der
> Prozessliste sichtbar. `--token` existiert für Sonderfälle.

## Funktionen von `walita.py`

`walita.py` verkettet Export und Dashboard-Bau. Schlägt der Export fehl, wird
kein Dashboard geschrieben. Fortschritt geht auf **stderr**.

### Anmeldung

| Option | Beschreibung |
| --- | --- |
| `--token TOKEN` | Personal Access Token (besser: Env `TRWL_TOKEN`, siehe oben) |
| `--login` | OAuth-Login im Browser erzwingen; speichert Token unter `data/oauth_token.json` |
| `--logout` | Gespeichertes OAuth-Token löschen und beenden |
| `--client-id ID` | Optional: eigene OAuth-Client-ID (Default steht schon in `auth.py`) |
| `--redirect-uri URL` | Optional: eigener Redirect (Default: Loopback-Callback) |
| `--manual` | Auth-Code manuell aus der Browserzeile einfügen (z.B. WSL) |
| `--oauth-token-file PFAD` | Ablageort des OAuth-Tokens (Default `data/oauth_token.json`) |

Auth-Reihenfolge: `--token` / `TRWL_TOKEN` → gültiges OAuth-Cache-Token →
Refresh → interaktiver Login. Fehlen am Cache die benötigten Scopes
(`read-statuses`), wird automatisch neu eingeloggt.

### Export

| Option | Beschreibung |
| --- | --- |
| `--limit N` | Maximal N Statuses laden (Tests) |
| `--since YYYY-MM-DD` | Nur Statuses mit Abfahrt **strikt nach** diesem Tag |
| `--skip-trips` | Keine Zwischenhalte nachladen (kein `/stopovers`) |
| `--refresh-trips` | `trips.json`-Cache ignorieren, alle Trips neu von der API |
| `--refresh-stations` | `stations.json`-Cache ignorieren, Koordinaten neu auflösen |
| `--no-stations` | Keine `stations.json` schreiben (Karte ohne Koordinaten) |
| `--operator-replacements PFAD` | JSON mit Operator-Namen-Ersetzungen (Default `operator_replacements.json`) |

### Dashboard

| Option | Beschreibung |
| --- | --- |
| `--open` | Dashboard im Browser öffnen (**Default**) |
| `--no-open` | Nicht im Browser öffnen |
| `--ignore-plus` | Wagennummern-Tags nicht am `+` trennen (Doppeltraktion = ein Fahrzeug) |

### Modi

| Option | Beschreibung |
| --- | --- |
| `--demo` | Nur Dashboard aus `examples/` bauen – kein API-Download, kein Token |
| `--dashboard-only` | Export überspringen, Dashboard aus vorhandener `data/` neu bauen |

`--demo` und `--dashboard-only` schließen sich gegenseitig aus. Beide laden nichts
von der API; zusammen mit einem Anmelde- oder Export-Flag (`--login`, `--since`, …)
bricht `walita.py` mit einer Meldung ab, statt das Flag stillschweigend zu ignorieren.

> **Achtung:** `--demo` schreibt nach `data/dashboard.html` und überschreibt damit
> ein dort liegendes Dashboard aus echten Daten. Mit `--dashboard-only` neu bauen.

Weitere Details und Pfad-Overrides: `python3 download_statuses.py --help` bzw.
`python3 build_dashboard.py --help`. Version: `python3 walita.py --version`.

## Was der Export erzeugt

Unter `data/` (Ordner wird bei Bedarf angelegt):

| Datei | Inhalt |
| --- | --- |
| `statuses.json` | Alle (gefilterten) Statuses; bei erfolgreichem Trip-Nachladen inkl. `trip.stopovers` |
| `stations.json` | `station_id → {name, lat, lon}` für die Karte |
| `trips.json` | Persistenter Cache der Zwischenhalte, je Trip-ID |
| `dashboard.html` | Selbstständiges Dashboard (von `walita` / `build_dashboard`) |
| `oauth_token.json` | OAuth Access-/Refresh-Token (gitignored) |

### Ablauf (Stufe 1)

1. `GET /auth/user` → Benutzername.
2. `GET /user/{username}/statuses`, paginiert über `links.next`. Pro Status nur
   Start- und Zielhalt.
3. `GET /stopovers/{tripIds}` → Zwischenhalte, als Feld `trip.stopovers` am Status.
   Die Trip-ID steht schon als `checkin.trip` im Status, es sind also bis zu 50
   Fahrten pro Anfrage. Cache pro Trip-ID im Lauf und in `trips.json`; fehlende
   Einträge werden nachgeladen, Fehler (`trip: null`, `trip_error`) **nicht**
   persistiert und beim nächsten Lauf erneut versucht. `--refresh-trips` leert
   den Cache. Fehlt `trips.json`, werden die Stopovers aus vorhandener
   `statuses.json` übernommen.
4. Stations-Koordinaten kommen aus dem `station`-Objekt jedes Stopovers; nur
   Stationen ohne Stopover-Deckung werden per `GET /station/{id}` nachgeladen
   → `stations.json`.
5. Vor dem Schreiben von `statuses.json` werden Operator-Namen anhand von
   [`operator_replacements.json`](operator_replacements.json) vereinheitlicht
   (`checkin.operator.name`: Rohname → kanonischer Name). Die Datei ist manuell
   zu pflegen; Schlüssel mit führendem `_` (Kommentare) werden ignoriert.
   Fehlt die Datei, bleibt alles unverändert.

### OAuth-Login

Eine eigene Träwelling-Anwendung musst du **nicht** anlegen. Client-ID und
Redirect (`http://127.0.0.1:8710/callback`) sind im Repo vorkonfiguriert
(`auth.DEFAULT_CLIENT_ID` / `DEFAULT_REDIRECT`).

```bash
python3 walita.py --login
```

Im Browser bei Träwelling anmelden und der App zustimmen. Ein lokaler
Callback-Server fängt den Code ab; das Token landet in `data/oauth_token.json`
und wird später per Refresh erneuert. Mit `--manual` (z.B. unter WSL) die URL
aus der Browserzeile (`?code=…`) ins Terminal einfügen – die Seite muss nicht
laden.

Wer bewusst einen eigenen Client nutzen will: `--client-id` /
`TRWL_CLIENT_ID` und passendes `--redirect-uri` / `TRWL_REDIRECT_URI`
(Redirect muss exakt zum Client passen). Optional `TRWL_CLIENT_SECRET`
(wird nicht gespeichert).

## Dashboard (GUI)

Eine selbstständige HTML-Datei mit Sidebar und Hell/Dunkel-Umschalter
(System-Default, Auswahl in `localStorage`). Sechs Ansichten:

- **Übersicht** – Kennzahlen (Check-ins, km, Reisezeit, Punkte, Stationen/Linien,
  Zeitraum) und meistbefahrene Segmente.

  ![Übersicht](docs/screenshots/01-uebersicht.png)

- **Karte** – Heatmap gerichteter Kanten entlang der tatsächlich befahrenen
  Zwischenhalte; Filter nach Linie, Baureihe, Kategorie, Operator, Jahr.
  Dicke und Farbe zeigen, wie oft ein Segment befahren wurde, der Pfeil die
  Richtung. Braucht Internet (Leaflet + Kacheln); der Rest läuft offline.

  ![Karte](docs/screenshots/02-karte-berlin.jpg)

- **Statistiken** – Rankings zu Linien, Baureihen, Fahrzeugen und Stationen
  (Ein-/Ausstieg/Durchfahrt, kombinierbare Filter), Kanten, Wiederholungen,
  Kreuztabellen.

  ![Statistiken](docs/screenshots/04-statistiken.png)

- **Fahrten** – durchsuch-/sortierbare Tabelle; Klick zeigt Stopovers mit Zeiten,
  Gleis und Verspätung.

  ![Fahrten](docs/screenshots/06-fahrten.png)

- **Fahrzeuge** – Matrix getaggter Wagennummern (`trwl:vehicle_number`) je Linie,
  gruppiert nach Baureihe/Kategorie.

  ![Fahrzeuge](docs/screenshots/05-fahrzeuge.png)

- **Tagesziele** – Erstvorkommen an einem gewählten Tag (Linien, Fahrzeuge,
  Kanten, Stationen und Kombis).

  ![Tagesziele](docs/screenshots/07-tagesziele.png)

Alle Screenshots stammen aus dem [Demo-Datensatz](#demo) (`walita.py --demo`) –
weitere Ansichten liegen unter [`docs/screenshots/`](docs/screenshots), darunter
die Karte im Dunkelmodus und deutschlandweit herausgezoomt.

Wagennummern: Trennung immer an `,` / `;`; standardmäßig auch an `+`
(`463001+463501` → zwei Fahrzeuge). Mit `--ignore-plus` bleibt Doppeltraktion
ein Eintrag.

Nur Dashboard neu bauen:

```bash
python3 walita.py --dashboard-only
python3 build_dashboard.py --open
python3 build_dashboard.py --statuses x.json --stations y.json -o out.html
```

## Stufen getrennt nutzen

```bash
export TRWL_TOKEN="dein-token"

python3 download_statuses.py                 # -> data/statuses.json (+ trips/stations)
python3 download_statuses.py --limit 3
python3 download_statuses.py --skip-trips
python3 download_statuses.py --since 2026-01-01
python3 download_statuses.py --refresh-trips
python3 download_statuses.py -o export.json
python3 build_dashboard.py --open
```

## Hinweise

- **Fehlende Zwischenhalte:** kennt die API eine Trip-ID nicht, bekommt der Status
  `trip: null` + `trip_error`; Start und Ziel bleiben im `checkin` erhalten und die
  Fahrt zählt weiter mit, nur ohne Zwischenhalte.
- **HTTP 403 bei Trips:** meist fehlender Scope `read-statuses` → erneut
  `walita.py --login` bzw. PAT mit diesem Scope neu ausstellen.
- **Rate-Limits:** automatisches Warten bei `429` (Retry-After), leichte Drossel
  zwischen den Anfragen.
- **Abbruch mit Ctrl-C** ist unbedenklich: der Trip-Cache (`trips.json`) wird auch
  dann geschrieben, bereits geladene Trips gehen also nicht verloren. Der nächste
  Lauf macht dort weiter.
- Logs auf **stderr**, Daten in Dateien – stdout bleibt frei.

## Demo

```bash
python3 walita.py --demo
# oder:
python3 build_dashboard.py \
  --statuses examples/statuses.json \
  --stations examples/stations.json \
  -o data/dashboard.html --open
```

Das Demo-Dataset umfasst **100 erfundene Fahrten in Berlin** über drei Monate:
S-Bahn, U-Bahn und Tram, dazu Regionalverkehr ins Umland und einzelne ICE-Fahrten ab
Berlin. Jede Fahrt berührt Berlin. 39 Linien, 216 Stationen – damit zeigen Karte,
Rankings und Fahrzeug-Matrix etwas Substanzielles.

Es gehört **niemandem**: Linien, Routen, Zwischenhalte und Fahrzeiten stammen als
echte Fahrplandaten aus der öffentlichen Träwelling-API, die Check-ins darum herum
sind generiert. Die Teilstücke streuen um ein paar häufig bediente Stationen herum,
mit wechselnder Länge und Richtung – es sind also keine sauberen Hin-/Rückfahrt-Paare,
sondern 97 verschiedene Start/Ziel-Kombinationen bei 100 Fahrten. Die Dichte auf der
Karte entsteht aus überlappenden Strecken, nicht aus Wiederholung.

Die meisten Check-ins belegen nur ein Teilstück ihrer Linie, so wie beim echten Ein-
und Aussteigen; `trip.stopovers` enthält jeweils die komplette Route. Die Fahrzeug-Tags
(`trwl:locomotive_class` / `trwl:vehicle_number`) sind plausibel erfunden, mit einer
festen Flotte je Linie, damit sich Nummern wiederholen – inklusive einiger
Doppeltraktionen für `--ignore-plus`.

## Lizenz

**GNU General Public License v3.0** – siehe [`LICENSE`](LICENSE).

    Copyright (C) 2026 besuka97

    Dieses Programm ist freie Software: Sie können es weitergeben und/oder
    modifizieren unter den Bedingungen der GNU General Public License, wie von
    der Free Software Foundation veröffentlicht, entweder Version 3 der Lizenz
    oder (nach Ihrer Wahl) jeder späteren Version.
