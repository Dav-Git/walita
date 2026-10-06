# Walita – Wagen-Linien-Tabellen für Träwelling

[![Lizenz: GPL v3](https://img.shields.io/badge/Lizenz-GPLv3-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![Keine Abhängigkeiten](https://img.shields.io/badge/Abh%C3%A4ngigkeiten-keine-brightgreen.svg)](#voraussetzungen)

Träwelling Status Export & Statistik-Dashboard

`walita.py` ist der Einstiegspunkt: Es lädt die **eigenen Statuses** von
[Träwelling](https://traewelling.de/api/documentation) (inkl. Zwischenhalte),
schreibt sie unter `data/` und erzeugt daraus ein in sich geschlossenes
**HTML-Dashboard**. Daneben gibt es einen **Tag-Editor** (`--edit`), der Tags
und den Status-Text live auf Träwelling ändert.

![Karte des Dashboards mit Befahrungs-Heatmap](docs/screenshots/02-karte-berlin.jpg)

Die beiden Stufen können auch einzeln genutzt werden:

| Skript | Aufgabe |
| --- | --- |
| `walita.py` | Export + Dashboard (empfohlen); `--edit` startet den Tag-Editor |
| `download_statuses.py` | Stufe 1: API → `statuses.json` / `stations.json` / `trips.json` |
| `build_dashboard.py` | Stufe 2: JSON → `dashboard.html` |
| `status_editor.py` | Tags und Status-Text live auf Träwelling ändern; Kanten-, Stations-, Linienfarben- und Einstiegs-Patches, Heimatregion, Fuhrpark |
| `edge_patches.py` | Lokale Via-Patches (Default/Override) für grobe Kanten |
| `station_patches.py` | Lokale Stations-Patches (Koordinaten verschieben, IDs mergen) |
| `line_color_patches.py` | Lokale Linienfarben-Patches je Status |
| `boarding_patches.py` | Lokale Einstiegs-Patches je Status |
| `home_region.py` | Lokale Operator-Liste der Heimatregion |
| `vehicle_roster.py` | Lokaler Fuhrpark: Fahrzeugnummern je Baureihe |
| `operator_line_patches.py` | Operator einer Linie überschreiben (Sonderfälle, Datei) |
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
├── walita.py              # Einstiegspunkt: Export + Dashboard (+ --edit)
├── download_statuses.py   # Stufe 1: Export von der Träwelling-API
├── status_editor.py       # Tag-Editor (tkinter): Tags + Text live ändern
├── editor_settings.py     # Einstellungsseiten des Tag-Editors (je Konfig-Datei)
├── edge_patches.py        # Lokale Via-Patches für grobe Kanten
├── station_patches.py     # Lokale Stations-Patches (Koordinaten / Merges)
├── line_color_patches.py  # Lokale Linienfarben-Patches je Status
├── boarding_patches.py    # Lokale Einstiegs-Patches je Status
├── home_region.py         # Lokale Operator-Liste der Heimatregion
├── vehicle_roster.py      # Lokaler Fuhrpark: Fahrzeugnummern je Baureihe
├── operator_line_patches.py  # Operator einer Linie (Sonderfälle)
├── operator_replacements.py  # Betreibernamen laden/speichern (Editor)
├── loc_class_families.py  # Baureihenfamilien laden/speichern (Editor)
├── auth.py                # OAuth-Login (PKCE)
├── build_dashboard.py     # Stufe 2: Dashboard aus den JSON-Dateien
├── dashboard/             # HTML/CSS/JS-Quellen (werden in eine HTML-Datei gepackt)
│   ├── template.html
│   ├── style.css
│   ├── edge_patch.html    # Leaflet-Karte zum Setzen von Via-Patches
│   ├── station_patch.html # Leaflet-Karte: Koordinaten verschieben / mergen
│   └── js/
├── version.py             # Version + User-Agent
├── examples/              # Demo-Dataset (eingecheckt, siehe Demo)
│   ├── statuses.json
│   └── stations.json
├── docs/screenshots/      # Bilder fürs README
├── data/                  # generierte Artefakte – nicht im Repo (.gitignore)
├── README.md · LICENSE · .gitignore · .gitattributes
```

> **Hinweis:** Eigene Reisedaten unter `data/` (`statuses.json`, `stations.json`,
> `trips.json`, `dashboard.html`, `oauth_token.json`, `edge_patches.json`,
> `station_patches.json`, `line_color_patches.json`, `boarding_patches.json`, `home_region.json`, `vehicle_roster.json`, `editor_state.json`,
> `operator_replacements.json`, `operator_line_patches.json`, `loc_class_families.txt`) sind persönlich und
> gitignored. Zum Ausprobieren ohne Token: [Demo](#demo).

## Voraussetzungen

- Python 3.8+ (nur Standardbibliothek, keine Installation nötig).
- Zugang zur Träwelling-API auf **einem** von zwei Wegen:
  - **Personal Access Token** (unter <https://traewelling.de/settings/applications>)
    mit `read-statuses` für den Export, zusätzlich `write-statuses` für den
    Tag-Editor, oder
  - **OAuth-Login** (`walita.py --login`) – Export fordert `read-statuses` an,
    der Tag-Editor (`walita.py --edit`) `read-statuses write-statuses`.
    Es ist bereits ein öffentlicher Client im Repo hinterlegt; **eigene App in
    Träwelling registrieren ist nicht nötig.** Falls der Login `write-statuses`
    nicht vergibt, einen PAT mit diesem Scope nutzen.

  Export und Dashboard **lesen** nur: die eigene Status-Liste und deren
  Zwischenhalte. Der Tag-Editor **schreibt** Tags und den Status-Text.

## Schnellstart

```bash
python3 walita.py --login             # einmalig: OAuth, dann Export + Dashboard
python3 walita.py                     # Update: Token/Caches nutzen, Dashboard öffnen
python3 walita.py --since 2026-01-01  # nur Fahrten ab dem 02.01.2026
python3 walita.py --full              # alle Statuses laden (für Änderungen an älteren Fahrten)
python3 walita.py --edit              # Tag-Editor (Tags + Text live ändern)
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
(`read-statuses`, beim Editor zusätzlich `write-statuses`), wird automatisch
neu eingeloggt.

### Export

| Option | Beschreibung |
| --- | --- |
| `--limit N` | Maximal N Statuses laden (Tests) |
| `--full` | Alle Statuses laden; ohne Flag nur neue und die letzten 2 Tage |
| `--since YYYY-MM-DD` | Nur Statuses mit Abfahrt **strikt nach** diesem Tag |
| `--skip-trips` | Keine Zwischenhalte nachladen (kein `/stopovers`) |
| `--refresh-trips` | `trips.json`-Cache ignorieren, alle Trips neu von der API |
| `--refresh-stations` | `stations.json`-Cache ignorieren, Koordinaten und Identifier neu auflösen |
| `--no-stations` | Keine `stations.json` schreiben (Karte ohne Koordinaten) |
| `--operator-replacements PFAD` | JSON mit Operator-Namen-Ersetzungen (Default `data/operator_replacements.json`) |

### Dashboard

| Option | Beschreibung |
| --- | --- |
| `--open` | Dashboard im Browser öffnen (**Default**) |
| `--no-open` | Nicht im Browser öffnen |
| `--ignore-plus` | Wagennummern-Tags nicht am `+` trennen (Doppeltraktion = ein Fahrzeug) |
| `--loc-class-families PFAD` | Baureihe→Familie für den Kartenfilter (Default `data/loc_class_families.txt`; JSON-Objekt, gleiche Baureihe darf mehrfach vorkommen) |

### Modi

| Option | Beschreibung |
| --- | --- |
| `--demo` | Nur Dashboard aus `examples/` bauen – kein API-Download, kein Token |
| `--dashboard-only` | Export überspringen, Dashboard aus vorhandener `data/` neu bauen |
| `--edit` | Tag-Editor statt Export + Dashboard (siehe [Tag-Editor](#tag-editor)) |

`--demo`, `--dashboard-only` und `--edit` schließen sich gegenseitig aus.
`--demo` und `--dashboard-only` laden nichts von der API; zusammen mit einem
Anmelde- oder Export-Flag (`--login`, `--since`, …) bricht `walita.py` mit
einer Meldung ab, statt das Flag stillschweigend zu ignorieren.

> **Achtung:** `--demo` schreibt nach `data/dashboard.html` und überschreibt damit
> ein dort liegendes Dashboard aus echten Daten. Mit `--dashboard-only` neu bauen.

Weitere Details und Pfad-Overrides: `python3 download_statuses.py --help`,
`python3 build_dashboard.py --help` bzw. `python3 status_editor.py --help`.
Version: `python3 walita.py --version`.

## Was der Export erzeugt

Unter `data/` (Ordner wird bei Bedarf angelegt):

| Datei | Inhalt |
| --- | --- |
| `statuses.json` | Alle (gefilterten) Statuses; bei erfolgreichem Trip-Nachladen inkl. `trip.stopovers` |
| `stations.json` | `station_id → {name, lat, lon, identifiers}` für die Karte; Identifier (IBNR, DHID/IFOPT, MOTIS, …) per `GET /station/{id}?withIdentifiers=true` |
| `trips.json` | Persistenter Cache der Zwischenhalte, je Trip-ID |
| `dashboard.html` | Selbstständiges Dashboard (von `walita` / `build_dashboard`) |
| `edge_patches.json` | Lokale Via-Patches für grobe Kanten (Tag-Editor / Karte; kein API-Write) |
| `station_patches.json` | Lokale Stations-Patches: Koordinaten und Merges (Tag-Editor / Karte; kein API-Write) |
| `line_color_patches.json` | Lokale Linienfarben je Status (Tag-Editor; kein API-Write) |
| `boarding_patches.json` | Lokaler Einstieg je Status (Tag-Editor; kein API-Write) |
| `home_region.json` | Operatoren der Heimatregion (Tag-Editor; kein API-Write) |
| `vehicle_roster.json` | Fuhrpark: Fahrzeugnummern je Baureihe (Tag-Editor; kein API-Write) |
| `oauth_token.json` | OAuth Access-/Refresh-Token (gitignored) |

### Ablauf (Stufe 1)

1. `GET /auth/user` → Benutzername.
2. `GET /user/{username}/statuses?withIdentifiers=true`, paginiert über `links.next`.
   Pro Status nur Start- und Zielhalt (Identifier, falls die API sie an diesem
   Endpoint mitgibt). Die Liste ist nach Abfahrt absteigend sortiert. Liegt
   `statuses.json` schon vor, wird nur geblättert, bis die Seite vor dem
   neuesten bekannten vergangenen Status minus 2 Tage endet (meist 1–2 Seiten).
   Was davor liegt, bleibt aus der Datei; Statuses im neu geladenen Bereich,
   die die API nicht liefert, gelten als gelöscht. Wer eine ältere Fahrt
   nachträglich ändert oder eincheckt, lädt mit `--full` alles neu. `--limit`
   lädt ebenfalls ohne Abgleich mit der Datei.
3. `GET /stopovers/{tripIds}?withIdentifiers=true` → Zwischenhalte, als Feld
   `trip.stopovers` am Status.
   Die Trip-ID steht schon als `checkin.trip` im Status, es sind also bis zu 50
   Fahrten pro Anfrage. Cache pro Trip-ID im Lauf und in `trips.json`; fehlende
   Einträge werden nachgeladen, Fehler (`trip: null`, `trip_error`) **nicht**
   persistiert und beim nächsten Lauf erneut versucht. `--refresh-trips` leert
   den Cache. Fehlt `trips.json`, werden die Stopovers aus vorhandener
   `statuses.json` übernommen.
4. Stations-Koordinaten kommen aus dem `station`-Objekt jedes Stopovers. Fehlen
   Koordinaten oder Identifier, wird `GET /station/{id}?withIdentifiers=true`
   nachgeladen → `stations.json`. Identifier stehen nur dort; `statuses.json`
   und `trips.json` enthalten an den Halten keine Identifier. Ein
   vorhandener Cache ohne Identifier wird einmalig nachgezogen;
   `--refresh-stations` holt alles neu.
5. Vor dem Schreiben von `statuses.json` werden Operator-Namen anhand von
   `data/operator_replacements.json` vereinheitlicht
   (`checkin.operator.name`: Rohname → kanonischer Name). Bearbeitbar im
   Tag-Editor unter **Betreiber → Namen**; Schlüssel mit führendem `_`
   (Kommentare) werden ignoriert.
   Fehlt die Datei, bleibt alles unverändert.

### Operator je Linie

Steht eine Linie unter dem falschen Operator, setzt
`data/operator_line_patches.json` den Namen beim
Dashboard-Bau auf einer Kopie um. `statuses.json` bleibt unverändert.
Bearbeitbar im Tag-Editor unter **Betreiber → Je Linie**. Die Heimatregion
filtert danach, die Linie zählt also unter dem neuen Operator.

```json
{
  "rules": [
    {
      "line": "S2",
      "operator": "Verkehrsbetriebe Karlsruhe",
      "name": "Albtal-Verkehrs-Gesellschaft"
    }
  ]
}
```

- `line` ist `checkin.lineName`, `operator` der Name nach den
  Operator-Ersetzungen. Die erste passende Regel gilt.
- Fehlende Datei = keine Änderung. Pfad: `--operator-line-patches`
  (Default `data/operator_line_patches.json`).

Baureihenfamilien für den Kartenfilter stehen in
`data/loc_class_families.txt` (JSON-Objekt
Baureihe → Familie; dieselbe Baureihe darf mehrfach vorkommen und steht
dann in mehreren Familien). Schlüssel mit führendem `_` werden ignoriert,
fehlende Datei = keine Familien. Die Datei wird erst beim Dashboard-Bau
gelesen, nicht beim Export. Bearbeitbar im Tag-Editor unter
**Fahrzeuge → Baureihenfamilien**. Die Endung `.txt` verhindert die
Duplicate-Key-Warnung von Text-Editoren; der Loader liest alle Paare.

### Kanten-Patches (physische Via-Stationen)

Träwelling listet nur **bediente** Halte. Eine Kante wie Karlsruhe Hbf → Bruchsal
ist deshalb oft zu grob. In `data/edge_patches.json` (gitignored) lassen sich
gerichtete Kanten um bekannte Zwischenstationen anreichern – **nur lokal**,
kein Upload. Auswertung und Karte zählen danach die topologischen Teilstücke.

```json
{
  "defaults": [
    { "from": 8001, "to": 8002, "via": [8100, 8101] }
  ],
  "overrides": [
    { "statusId": 42, "from": 8001, "to": 8002, "via": [8102] },
    { "statusId": 99, "from": 8001, "to": 8002, "via": [] }
  ]
}
```

- **Default** gilt für alle Fahrten mit genau diesem `(from, to)` (aufeinanderfolgende
  nicht-cancelled IDs in `traveled_stopovers`).
- **Override** nur für `statusId`; `via: []` schaltet den Default für diese Fahrt aus.
- Via-IDs nur aus `stations.json` mit Koordinaten; `from`/`to` und auf der Fahrt
  schon bediente IDs werden verworfen. Entfällt-Zwischenhalte gelten nicht als
  bedient und können Via sein. Der nächste Export überschreibt `trip.stopovers`
  – Patches leben deshalb **nicht** in `statuses.json`.
- Fehlende Datei = keine Expansion. Pfad: `--edge-patches` (Default `data/edge_patches.json`).
- Nach dem Speichern im Tag-Editor das Dashboard neu bauen, damit Stats und Karte
  die Teilstücke zeigen.

Im Tag-Editor listet die Sektion **Kanten** die Folge-Kanten der gewählten Fahrt
(Standard / Fahrt / —). **Auf Karte anreichern** startet einen lokalen Server
(`http://127.0.0.1:8711/`) und öffnet Leaflet. Auf der Karte liegen Stationen
im 20-km-Korridor um die Luftlinie; weitere Stationen (außerhalb) per Name oder
ID suchen und übernehmen. Speichern als Standard oder nur diese Fahrt.
Internet nur für Kacheln.

Stations-Rollen im Dashboard: **Ein-/Ausstieg** (Origin/Destination), **gehalten**
(Träwelling-Zwischenhalt, sitzegeblieben), **physische Durchfahrt** (Via aus dem Patch).
Zwischenhalte mit `cancelled` („Entfällt“) zählen weder als gehalten noch als
Durchfahrt und fehlen im Knotenmodell, bis sie als Via im Patch stehen.
Tag `dubi=start` bzw. `dubi=ende` (Durchbindung): der Fahrtbeginn zählt nicht als Einstieg, das Fahrtende nicht als Ausstieg.

### Stations-Patches (Koordinaten und Merges)

Träwelling-Koordinaten sitzen manchmal neben der Strecke, und dieselbe physische
Station kann unter zwei IDs vorkommen. In `data/station_patches.json` (gitignored)
lassen sich Marker **verschieben** und IDs **zusammenführen** – **nur lokal**,
kein Upload. Das Overlay liegt **vor** den Kanten-Patches: Merges schreiben
Stopover-IDs auf den Survivor um, Moves überschreiben Lat/Lon. Der nächste Export
lässt `stations.json` / `statuses.json` unverändert.

```json
{
  "moves": [
    { "id": 8001, "latitude": 49.01, "longitude": 8.40 }
  ],
  "merges": [
    { "from": 8002, "to": 8001 }
  ]
}
```

- **Move** gilt global für diese Stations-ID.
- **Merge** `from → to`: `to` behält Name, Koordinaten und ID. Ketten werden
  flach aufgelöst (`A→B`, `B→C` → `A→C`); Zyklen werden verworfen. Ein Move auf
  die verschwindende `from`-ID gilt nicht für den Survivor.
- `edge_patches.json` bleibt bei Original-IDs (Unmerge bleibt möglich); beim
  Dashboard-Bau werden Keys und Vias im Speicher umgeschrieben.
- Fehlende Datei = keine Änderung. Pfad: `--station-patches`
  (Default `data/station_patches.json`).
- Nach dem Speichern im Tag-Editor das Dashboard neu bauen.

Im Tag-Editor listet **Karte → Stationen** alle Verschiebungen (mit Abstand in
Metern) und Zusammenlegungen; einzelne Einträge lassen sich dort löschen.
**Stationskarte öffnen** startet einen lokalen Server
(`http://127.0.0.1:8712/`) mit Leaflet. Marker der befahrenen Stationen sind
ziehbar (sofort gespeichert). Zwei Stationen wählen, **Wird aufgelöst** /
**Bleibt**, dann **Zusammenführen**. Listen in der Sidebar setzen Moves und
Merges zurück. Weitere Stationen per Name oder ID suchen. Internet nur für Kacheln.

### Linienfarben-Patches

Träwelling liefert `checkin.routeColor` manchmal unvollständig oder abweichend von
der offiziellen Linienfarbe. In `data/line_color_patches.json` (gitignored)
lässt sich die Farbe **je Status** setzen – **nur lokal**, kein Upload. Beim
Dashboard-Bau überschreibt ein Patch `routeColor` / `routeTextColor` dieser
Fahrt; die Linien-Badges nutzen die gepatchte Farbe vorrangig vor der ersten
HAFAS-Farbe derselben Linie. Der nächste Export lässt `statuses.json`
unverändert.

```json
{
  "overrides": [
    { "statusId": 42, "routeColor": "0066ad", "routeTextColor": "ffffff" }
  ]
}
```

- Fehlende Datei = keine Änderung. Pfad: `--line-color-patches`
  (Default `data/line_color_patches.json`).
- Nach dem Speichern im Tag-Editor das Dashboard neu bauen.

Im Tag-Editor zeigt **Linienfarbe** rechts zur gewählten Fahrt die aktuelle
Farbe (HAFAS / lokal / keine). **Ändern** öffnet Hex-Eingabe und
Farbwähler; die Textfarbe wird aus dem Kontrast gesetzt. **Zurücksetzen**
entfernt nur den lokalen Patch.

**Darstellung → Linienfarben** listet alle lokalen Linienfarben, gruppiert
nach Linie und Operator, jede Zeile in ihrer Farbe. **Bearbeiten** (oder
Doppelklick) auf einer Linie ändert alle ihre Einträge, auf einer Fahrt nur
diese; **Löschen** entfernt entsprechend. Jede Änderung schreibt sofort
`data/line_color_patches.json`. Neue Farben entstehen über die Fahrt.

### Einstiegs-Patches

Träwelling legt den Einstieg in `checkin.origin` fest. Liegt der tatsächliche
Einstieg früher oder später auf derselben Fahrt, kann er in
`data/boarding_patches.json` (gitignored) **je Status** umgelegt werden –
**nur lokal**, kein Upload. Der Ausstieg bleibt. Halte hinter dem Ausstieg
sind nicht wählbar. Beim Dashboard-Bau ersetzt der Patch den Einstieg auf
einer Kopie, bevor der befahrene Abschnitt geschnitten wird. Der nächste
Export lässt `statuses.json` unverändert. Fehlt der Halt im neuen Export,
gilt der Patch für diese Fahrt nicht.

```json
{
  "overrides": [
    { "statusId": 42, "stopoverId": 1093842227 }
  ]
}
```

Fahrzeit und Kilometer der Kopie folgen dem neuen Abschnitt. Die Fahrzeit
kommt aus Abfahrt am neuen Halt und Ankunft am Ausstieg. Die Kilometer kommen
aus anderen Fahrten: zuerst dieselbe Haltfolge, sonst die Summe bekannter
Einzelkanten, sonst beim späteren Einstieg die API-Strecke minus das
bekannte Präfix (beim früheren Einstieg plus die zusätzlichen Kanten). Eine
Kante ist bekannt, wenn eine andere Fahrt genau dieses Stationspaar
abdeckt; fehlt in einer längeren Fahrt nur noch eine Kante, bekommt sie die
Reststrecke. Die Luftlinie ist nur der Ersatz, wenn keine dieser Quellen
reicht. Punkte bleiben die Träwelling-Punkte. Die Kanten-Maße werden einmal
aus allen Fahrten gelernt und gelten auch im Heimat-Lauf.

- Fehlende Datei = keine Änderung. Pfad: `--boarding-patches`
  (Default `data/boarding_patches.json`).
- Nach dem Speichern im Tag-Editor das Dashboard neu bauen.

Im Tag-Editor zeigt **Einstieg** (Gruppe *Fahrtverlauf*) zur gewählten Fahrt
den wirksamen Halt (laut Träwelling oder lokal inkl. Träwelling-Name).
**Ändern** listet die Halte vor dem Ausstieg. **Zurücksetzen** entfernt nur
den lokalen Patch. **Speichern** schickt den Einstieg nicht nach Träwelling.
**Darstellung → Einstiege** listet alle lokalen Einstiege mit **Zur Fahrt**.

### Heimatregion

Der Schalter **Heimat** in der Sidebar filtert alle Ansichten (Übersicht,
Statistiken, Karte, Linien, Fahrten, Fahrzeuge, Tagesziele) auf eine
selbst gewählte Operator-Liste. Intern ist das nur `checkin.operator.name`
(nach den [Operator-Ersetzungen](#ablauf-stufe-1)). Ein leerer Name steht in
der Liste als `""` und im Editor als „(ohne Operator)“.

```json
{
  "operators": ["DB Regio AG", "S-Bahn Berlin GmbH"]
}
```

- Die Liste liegt in `data/home_region.json` (gitignored). Fehlende oder leere
  Datei = kein Schalter. Pfad: `--home-region` (Default `data/home_region.json`).
- Beim Dashboard-Bau entsteht ein zweiter Aggregat-Block (`DATA.home`), weil
  Übersicht, Statistiken und Tagesziele schon fertig zusammengefasst sind.
  Kanten-, Stations-, Linienfarben- und Einstiegs-Patches gelten in beiden Läufen.
  Die Kilometer bekannter Kanten stammen aus allen Fahrten, nicht nur aus der
  Heimat-Teilmenge.
- Der Schalter merkt sich An/Aus in `localStorage` (`trwl-home`), Standard aus.
- Auf schmalen Viewports (unter 761px) ersetzt er den Hell/Dunkel-Knopf. Das
  Theme folgt dort weiter der gespeicherten Wahl oder der Systemeinstellung.
  Ab 761px stehen beide Knöpfe untereinander.
- Nach dem Speichern im Tag-Editor das Dashboard neu bauen.

Im Tag-Editor zeigt **Betreiber → Heimatregion** alle Operatoren aus den
geladenen Fahrten (mit Fahrtenanzahl) plus Namen, die schon in der Datei
stehen. Klick auf ✓ oder Leertaste schaltet um, **nur angehakte** filtert.
Jede Änderung schreibt sofort die lokale Datei, nicht nach Träwelling.

### Fuhrpark

Je Baureihe kann optional eine Liste konkreter Fahrzeugnummern hinterlegt werden.
Die Fahrzeuge-Seite zeigt dann jede Nummer als eigene Zeile, auch ohne Fahrt, und
im Gruppenkopf die Abdeckung (`8/10 (80%)`): gefahrene nicht ausgemusterte
Nummern im aktuellen Filter, geteilt durch alle nicht ausgemusterten Nummern.
Ausgemusterte Wagen haben einen schwarzen Hintergrund. Ist ein Datum bekannt,
steht es an der Nummer (`ausgem. 12.06.2019`). Für den Goldrand gelten sie als
nicht vorhanden: 228 und 230 bleiben ein Block, wenn 229 ausgemustert ist; der
Goldstrich läuft durch die schwarze Zeile. Eine aktive, ungefahrene Nummer
dazwischen unterbricht den Block. Ohne Fuhrpark bleibt der Goldrand bei
benachbarten Zeilen mit Differenz ±1.

```json
{
  "types": {
    "425": [
      {"number": "228", "withdrawn": false},
      {"number": "229", "withdrawn": true, "withdrawnOn": "2019-06-12"}
    ]
  }
}
```

- Die Datei liegt in `data/vehicle_roster.json` (gitignored). Fehlende Datei =
  kein Fuhrpark. Pfad: `--vehicle-roster` (Default `data/vehicle_roster.json`).
- Anlegen im Tag-Editor per Nummernbereich mit Schrittweite (`301–311`,
  Schritt `2` ergibt nur die ungeraden). Jede Nummer lässt sich einzeln
  ausmustern, datieren oder löschen. `withdrawnOn` wird nur gespeichert, wenn
  das Fahrzeug ausgemustert ist und das Datum bekannt ist.
- Die Abdeckung und die Leerzeilen gelten bei Gruppierung nach Baureihe.
  **Nicht benutzte ausblenden** nimmt Zeilen ohne Fahrt aus der Tabelle; die
  Abdeckung im Kopf zählt sie weiter. Nach Produktkategorie bleiben nur
  gefahrene Wagen; ausgemusterte davon trotzdem schwarz, der Goldrand
  überspringt sie.
- Nach dem Speichern im Tag-Editor das Dashboard neu bauen.

Im Tag-Editor zeigt **Fahrzeuge → Fuhrpark** links die Baureihen aus den
geladenen Fahrten plus schon gespeicherte Typen, rechts deren Nummern mit
Status, Ausmusterungsdatum und Anzahl der Fahrten. Jede Änderung schreibt
sofort die lokale Datei, nicht nach Träwelling.

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

Eine selbstständige HTML-Datei mit Sidebar, Hell/Dunkel-Umschalter
(System-Default, Auswahl in `localStorage`) und optionalem Schalter
**Heimat** (siehe [Heimatregion](#heimatregion)). Unten links in der Sidebar
stehen der Zeitraum der Check-ins und der Zeitpunkt des letzten Builds.
Neben jedem Datumsfeld steht ein ×, das genau dieses Feld leert.
Die Quellen liegen unter `dashboard/` und werden beim Erzeugen inline
zusammengefügt. Sieben Ansichten:

- **Übersicht** – Kennzahlen (Check-ins, km, Reisezeit, Punkte, Stationen/Linien,
  Zeitraum) und meistbefahrene Segmente.

  ![Übersicht](docs/screenshots/01-uebersicht.png)

- **Karte** – Heatmap gerichteter Kanten entlang der tatsächlich befahrenen
  Zwischenhalte; Filter nach Linie, Baureihe, Kategorie, Operator, Jahr und
  Datumsbereich (Von/Bis). Die Überschrift nennt die gerade gesetzten Filter.
  Gemappte Baureihenfamilien erscheinen zusätzlich im Dropdown „Baureihe“
  (Auswahl der Familie zeigt alle Mitglieds-Baureihen).
  Dicke und Farbe zeigen die Anzahl der Fahrten über ein Segment, der Pfeil die
  Richtung. Das Dropdown **Darstellung** schaltet auf **Fahrzeuge**: je
  Fahrzeug eine eigene Farbe. Der Farbkreis umfasst nur die Fahrzeuge des
  aktuellen Filters und wird beim Filterwechsel neu vergeben. Die Farben
  liegen in gleichen wahrgenommenen Abständen (OKLCH), nicht in gleichen
  Farbwinkeln, sodass Grün und Blau nicht überwiegen und Gelb, Türkis und
  Orange genauso oft vorkommen und Pink nicht überwiegt; jede Farbe ist so
  kräftig, wie der Bildschirm-Farbraum es zulässt. Der Ring beginnt bei
  sattem Gelb (#ffd500). Fahrzeuge, die auf der
  Karte nah beieinanderliegen (gleiche Kante, auch in Gegenrichtung,
  gemeinsame Station, gleiche Region), bekommen möglichst weit
  auseinanderliegende Töne; weit entfernte Fahrzeuge dürfen ähnliche Farben
  haben. Die Wagennummer legt die Farbe nicht fest. Die Linien bleiben gerichtet und im Rechtsverkehr
  versetzt, mit einer sichtbaren Lücke zwischen den beiden Richtungen.
  Mehrere Fahrzeuge auf derselben gerichteten Kante stapeln sich nach
  außen, die Lücke bleibt frei. Im Stapel stehen nur Fahrzeuge, die der
  aktuelle Filter noch zeigt. Nur in diesem Modus werden Kanten desselben Fahrzeugs über
  Zwischenstationen mit einer Kurve verbunden, und zwar nur dort, wo eine
  Fahrt unter dem aktuellen Filter tatsächlich von der einen Kante auf die
  andere weiterfährt. Die häufigste Folge setzt den Pfad fort, jede
  weitere gefahrene Folge an der Station wird als Abzweig ebenfalls mit einer
  Kurve angebunden (als eigener Pfad). Ein reiner Ein- oder Ausstieg
  bekommt keine Kurve. Eine Durchbindung (`dubi=ende`, als nächster Check-in
  nach Check-in-Zeit `dubi=start` ab derselben Station) gilt als durchgehende
  Fahrt: bei Fahrzeugen, die auf beiden getaggt sind, im Linienmodus bei
  gleicher Linie, im Baureihenmodus bei gleicher Baureihe. **Linien** zeichnet genauso, nur je Linie statt je
  Fahrzeug: Farbe ist die Linienfarbe aus den Check-ins (inklusive
  Linienfarben-Patch), Linien ohne Farbe bekommen den Farbkreis, das Badge
  zeigt den Liniennamen. **Baureihen** gruppiert ebenso nach Baureihe
  (Farbkreis, Badge = Baureihe). Mit Fahrzeugfilter zählen nur dessen
  Fahrten. In allen drei Modi gilt: Badges überlappen sich nie. Jeder
  sichtbare Pfad bekommt ein Badge, auch auf einem kurzen Abschnitt, möglichst
  in seiner Mitte. Liegt dort schon ein anderes, rückt es entlang des Pfads
  auf die nächste freie Stelle; ohne freie Stelle entfällt es. Weitere
  Badges (höchstens drei pro Fahrzeug bzw. Linie) gibt es nur auf Pfaden, die im
  Ausschnitt lang genug sind: zwischen zwei Badges desselben Pfads liegt
  mindestens die halbe kürzere Seite der Karte. Badges liegen ganz in der
  Karte, mit Abstand zum Rand. Gehaltene Stationen ohne Ein-/Ausstieg erscheinen als weißer Kreis
  mit schwarzem Rand, reine physische Durchfahrten als kleiner, gedämpfter Punkt.
  Die Checkbox **Entdeckte Kanten** (Standard aus) legt in Grau
  gerichtete Stopover-Paare darüber, die unter dem aktuellen Filter nicht
  eingecheckt sind (z.B. auf dieser Linie nur im Laufweg gesehen, auf einer
  anderen Linie aber befahren).   Die übrigen Kartenfilter gelten analog.
  **Vollbild** (Schaltfläche oben links auf der Karte, unter dem Zoom) schaltet
  nur die Karte selbst in den echten Vollbildmodus des Browsers. Filter und
  Seitenleiste bleiben draußen. Esc oder dieselbe Schaltfläche beendet ihn.
  Die Ansicht bleibt dabei erhalten.
  Braucht Internet (Leaflet + Kacheln); der Rest läuft offline.

  ![Karte](docs/screenshots/02-karte-berlin.jpg)

- **Linien** – alle Linien gruppiert nach Operator; je Linie aggregierte gerichtete
  Laufwege (Perlschnur) und die Anteile der Baureihen (nach Check-ins).

- **Statistiken** – Rankings zu Linien, Baureihen, Fahrzeugen und Stationen
  (Ein-/Ausstieg/gehalten/physische Durchfahrt, kombinierbare Filter), Kanten, Wiederholungen,
  Kreuztabellen. Datumsbereich (Von/Bis) rechnet diese Tabellen für die
  Check-ins in dem Zeitraum neu; ohne Von/Bis bleibt die voraggregierte
  Gesamtstatistik.

  ![Statistiken](docs/screenshots/04-statistiken.png)

- **Fahrten** – durchsuch-/sortierbare Tabelle mit Datumsbereich (Von/Bis);
  optionale Spalte „Laufweg“ (komprimierte Stationenkette der sichtbaren Fahrten);
  Klick zeigt Stopovers mit Zeiten, Gleis und Verspätung.

  ![Fahrten](docs/screenshots/06-fahrten.png)

- **Fahrzeuge** – Matrix getaggter Wagennummern (`trwl:vehicle_number`) je Linie,
  gruppiert nach Baureihe/Kategorie. Linienspalten nach Name oder nach Anzahl
  Fahrten in der Tabelle. Mit Fuhrpark je Baureihe jede definierte
  Nummer, die Abdeckung und ausgemusterte Wagen schwarz (Goldrand läuft durch).

  ![Fahrzeuge](docs/screenshots/05-fahrzeuge.png)

- **Tagesziele** – Erstvorkommen und Wiederholungen an einem gewählten Tag
  (Linien, Fahrzeuge, Top-Wagen der Baureihe, Kanten, benutzte vs. gehaltene vs.
  physisch durchfahrene Stationen und Kombis).

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
python3 build_dashboard.py --edge-patches data/edge_patches.json --open
python3 build_dashboard.py --station-patches data/station_patches.json --open
python3 build_dashboard.py --line-color-patches data/line_color_patches.json --open
python3 build_dashboard.py --boarding-patches data/boarding_patches.json --open
python3 build_dashboard.py --home-region data/home_region.json --open
python3 build_dashboard.py --vehicle-roster data/vehicle_roster.json --open
python3 build_dashboard.py --operator-line-patches data/operator_line_patches.json --open
```

## Tag-Editor

Lokales tkinter-Fenster mit drei Bereichen:

- **Werkzeugleiste**: *Von API laden* (Strg+R), *Speichern (n)* (Strg+S) mit der
  Anzahl ungespeicherter Fahrten, *Dashboard bauen*.
- **Navigation** links: *Fahrten* mit den Ansichten *Alle*, *Ohne Baureihe* und
  *Geändert*, darunter je Konfig-Datei eine Einstellungsseite.
- **Inhalt** der gewählten Seite.

**Fahrten.** Filter über Suche (Strg+F), Zeitraum, Betreiber und die Häkchen
*ohne Baureihe*, *ohne Nummer*, *mit lokalem Patch*, *geändert*. Spalten:
Datum, Linie, Von → Nach, Betreiber, Baureihe, Nummer und ◆ mit den lokalen
Patches der Fahrt (E Einstieg, F Farbe, K Kante, D Durchbindung). Baureihe und
Nummer sind direkt in der Liste editierbar (Klick oder F2; Enter = nächste
Zeile, Tab = nächste Spalte). Geänderte Fahrten sind fett und mit ● markiert.

Rechts stehen die Details der gewählten Fahrt in einklappbaren Gruppen:

| Gruppe | Inhalt | Ziel |
| --- | --- | --- |
| Fahrzeug | Baureihe (Auswahl oder frei), Nummer, Hinweis ob im Fuhrpark | Träwelling |
| Fahrtverlauf | Einstieg, Durchbindung (`dubi=start` / `dubi=ende`), Kanten mit **Auf Karte anreichern** und **Patch entfernen ▾** | lokal (Durchbindung: Träwelling) |
| Darstellung | Linienfarbe | lokal |
| Text & Tags | Status-Text, weitere Tags (Sitz, Wagen, …) | Träwelling |

**Speichern** öffnet ein Übertragungsfenster (Fahrt für Fahrt: Wartend /
Übertrage / Gespeichert / Fehler) und schreibt den Diff nach Träwelling
(`PUT /status/{id}` nur für `body`; Tags über
`POST`/`PUT`/`DELETE /status/{id}/tags`). Ziel, Sichtbarkeit, Event und der
Laufweg bleiben unberührt. In `data/statuses.json` werden danach nur `body`
und `tags` aktualisiert. **Dashboard bauen** erzeugt `data/dashboard.html` aus
der Datei (nicht aus ungespeichertem Staging).

**Einstellungen.** Jede Seite zeigt den kompletten Inhalt ihrer Datei als
Tabelle mit Suche, Sortierung, **Hinzufügen / Bearbeiten / Löschen** und
schreibt sofort beim Bestätigen. Einträge ohne Bezug in den geladenen Daten
sind grau, **Verwaiste entfernen** löscht sie.

| Seite | Datei | wirkt |
| --- | --- | --- |
| Fahrzeuge → Fuhrpark | `data/vehicle_roster.json` | beim Dashboard-Bau |
| Fahrzeuge → Baureihenfamilien | `data/loc_class_families.txt` | beim Dashboard-Bau |
| Betreiber → Heimatregion | `data/home_region.json` | beim Dashboard-Bau |
| Betreiber → Namen | `data/operator_replacements.json` | **beim nächsten Export** |
| Betreiber → Je Linie | `data/operator_line_patches.json` | beim Dashboard-Bau |
| Karte → Kanten | `data/edge_patches.json` | beim Dashboard-Bau |
| Karte → Stationen | `data/station_patches.json` | beim Dashboard-Bau |
| Darstellung → Linienfarben | `data/line_color_patches.json` | beim Dashboard-Bau |
| Darstellung → Einstiege | `data/boarding_patches.json` | beim Dashboard-Bau |

Ist eine Datei kein gültiges JSON, zeigt die Seite den Fehler und bleibt
schreibgeschützt; die Datei wird nicht überschrieben. Nach dem Korrigieren die
Seite erneut wählen. Sortierung, Spaltenbreiten, eingeklappte Gruppen und die
zuletzt gewählte Seite stehen in `data/editor_state.json`.

```bash
python3 walita.py --edit
python3 walita.py --edit --login    # OAuth mit read-statuses write-statuses
python3 status_editor.py            # direkt, gleiche Auth-Flags wie der Export
```

Die Liste kommt aus `data/statuses.json`, falls vorhanden, sonst von der API.
„Von API laden“ holt die aktuelle Liste. Tippen in der Tabelle geht nicht
sofort an den Server; **Speichern** schreibt zuerst nach Träwelling – schlägt
das fehl, bleibt die lokale Datei unverändert.

Braucht Scope **`write-statuses`**. PAT unter
<https://traewelling.de/settings/applications> entsprechend ausstellen.
Beim OAuth-Client 360 muss der Scope erlaubt sein – sonst PAT nutzen.

## Stufen getrennt nutzen

```bash
export TRWL_TOKEN="dein-token"

python3 download_statuses.py                 # -> data/statuses.json (+ trips/stations)
python3 download_statuses.py --limit 3
python3 download_statuses.py --skip-trips
python3 download_statuses.py --since 2026-01-01
python3 download_statuses.py --full                 # alle Statuses laden
python3 download_statuses.py --refresh-trips
python3 download_statuses.py -o export.json
python3 status_editor.py                         # Tag-Editor
python3 build_dashboard.py --open
```

## Hinweise

- **Fehlende Zwischenhalte:** kennt die API eine Trip-ID nicht, bekommt der Status
  `trip: null` + `trip_error`; Start und Ziel bleiben im `checkin` erhalten und die
  Fahrt zählt weiter mit, nur ohne Zwischenhalte.
- **HTTP 403 bei Trips:** meist fehlender Scope `read-statuses` → erneut
  `walita.py --login` bzw. PAT mit diesem Scope neu ausstellen.
- **HTTP 403 beim Tag-Editor:** meist fehlender Scope `write-statuses` → PAT
  mit diesem Scope ausstellen oder `--edit --login` (OAuth-Client muss den
  Scope erlauben).
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
