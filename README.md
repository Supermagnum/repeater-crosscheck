# repeater-crosscheck

Cross-check Norwegian amateur radio repeater positions and write a merged CSV
plus a JOSM review file.

Sources used here are public data. Before uploading any changes to OSM, check that
tags are not broken and that the data matches local knowledge.

## Source data

Primary inputs and comparison sources:

- **[NRRL](https://nrrl.no/)** — official Norwegian repeater list (CSV:
  `Kallesignal`, `Type`, `QTH`, frequencies, `Gruppe`, `Lokator`, `Info`,
  `status`), plus [group pages](https://nrrl.no/grupper/) for club websites
- **[RepeaterBook](https://www.repeaterbook.com/)** — rest-of-world export API
  (Norway), when an approved API token is configured
- **AnyTone Norge** (Facebook group) — community codeplug / channel knowledge
  used together with optional AnyTone CPS exports (`channel.csv` / `zone.csv`)
  and local corrections in `overrides.toml`

Additional online sources used for position cross-checks:

- **OpenStreetMap** via the Overpass API (amateur-radio tags, callsigns, and
  mast/tower/peak landmarks)
- **[radioid.net](https://radioid.net/api/)** — Norwegian DMR repeater map data
- **[Mapterhorn](https://mapterhorn.com/)** — ground elevation (metres ASL) at the
  best site position, written only to `repeaters_merged.csv` as `elevation_m`
  (Terrarium DEM tiles). Suitable as site altitude for RF path simulations with
  [SPLAT!](https://github.com/hoche/splat).

Published review artefacts in this repository:

- [`output/repeaters_merged.csv`](output/repeaters_merged.csv)
- [`output/repeaters_review.osm`](output/repeaters_review.osm) (JOSM review layer)
- [`output/repeaters.joz`](output/repeaters.joz) (JOSM session bundle)

## OSM coverage

Counts from the current NRRL list (fixed sites only; portables excluded) versus
callsigns already present on OpenStreetMap amateur-radio features (Overpass).
Fylke is taken from the NRRL group directory region.

| | Count |
|--|------:|
| NRRL fixed callsigns | 471 |
| Already in OSM | 18 |
| Missing from OSM | 453 |
| Distinct callsigns tagged in OSM (Norway extract) | 33 |

Per fylke:

| Fylke | NRRL | In OSM | Missing |
|-------|-----:|-------:|--------:|
| Østfold | 11 | 0 | 11 |
| Akershus | 37 | 1 | 36 |
| Oslo | 8 | 0 | 8 |
| Innlandet | 53 | 8 | 45 |
| Buskerud | 22 | 1 | 21 |
| Vestfold | 22 | 3 | 19 |
| Telemark | 16 | 0 | 16 |
| Agder | 30 | 0 | 30 |
| Rogaland | 50 | 2 | 48 |
| Vestland | 36 | 3 | 33 |
| Møre og Romsdal | 21 | 0 | 21 |
| Trøndelag | 38 | 0 | 38 |
| Nordland | 56 | 0 | 56 |
| Troms | 50 | 0 | 50 |
| Finnmark | 19 | 0 | 19 |
| Svalbard og Jan Mayen | 1 | 0 | 1 |
| Unknown | 1 | 0 | 1 |
| **Total** | **471** | **18** | **453** |

“In OSM” means the NRRL callsign appears on an OSM object in the amateur-radio
extract (dedicated callsign tag or name/ref). Landmark/QTH matches that are not
yet tagged with the callsign still count as missing.

## Setup

```bash
cd repeater-crosscheck
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.toml config.toml
```

Edit `config.toml`:

- `paths.nrrl_csv` — NRRL repeater list CSV (`Kallesignal`, `Type`, `QTH`, …)
- `paths.channel_csv` / `zone_csv` — optional AnyTone CPS exports
- `http.user_agent` — identify yourself (required by radioid / RepeaterBook)
- `repeaterbook.api_token` — optional; leave empty to skip RepeaterBook until you
  have an approved token ([request form](https://www.repeaterbook.com/api/token_request.php))
- `radioid.api_token` — optional `X-API-Token` (recommended for future-proofing)

## Usage

```bash
python -m repeater_crosscheck -c config.toml
python -m repeater_crosscheck -c config.toml --refresh   # re-download caches
python -m repeater_crosscheck -c config.toml --skip-osm  # offline-ish debug
python -m repeater_crosscheck -c config.toml --skip-elevation  # skip Mapterhorn ASL
```

Open [`output/repeaters.joz`](output/repeaters.joz) in JOSM (from the `output/`
folder), or open [`output/repeaters_review.osm`](output/repeaters_review.osm)
alone. Portable stations are omitted from this layer (they are not fixed sites).

### Reviewing in JOSM

1. **Install JOSM** — download from the official page:
   [https://josm.openstreetmap.de/wiki/Download](https://josm.openstreetmap.de/wiki/Download)
   (Java Web Start, `.jar`, or OS packages / installers).
2. **Create an OpenStreetMap account** (needed to download OSM data with your
   identity and to upload edits) at
   [https://www.openstreetmap.org/user/new](https://www.openstreetmap.org/user/new).
   Confirm the email, then in JOSM open **Edit → Preferences → OSM Server**
   (or **Connection settings**) and authorize with that account (OAuth
   recommended). See
   [JOSM connection help](https://josm.openstreetmap.de/wiki/Help/Preferences/Connection).
3. **Open the review session or file** — from the `output/` folder, open
   [`repeaters.joz`](output/repeaters.joz) (preferred) or
   [`repeaters_review.osm`](output/repeaters_review.osm).
4. **Show a map background** — the `.joz` session already includes
   **Kartverket topo**. Otherwise use **Imagery → OpenStreetMap Carto
   (Standard)** (or another imagery source). Toggle layers in the **Layers**
   panel.
5. **Optional: download existing OSM data** for the area you are reviewing —
   **File → Download data…** (or the download button), select the bbox on the
   slippy map, and download. Keep the review layer and the downloaded OSM data
   as separate layers; copy tags only after checking local knowledge.

Before upload, delete the review-only tag `best_source` (and its value) from
every object that still has it. It records which source the tool preferred and
must not be stored in OpenStreetMap. Do not upload until tags and positions
have been verified.

When a site position is uncertain, consult the NRRL list
(`Relestasjoner.csv` — the `QTH`, `Lokator`, and frequency columns) and move the
object in JOSM to the position that fits best given local knowledge, imagery,
and other sources. Do not upload a guess you cannot defend.

### What is `repeaters.joz`?

A `.joz` file is a **compressed JOSM session** (zip), not a plain `.jos` XML
session. Open it with JOSM from the `output/` directory. This bundle includes:

1. **One data layer per fylkesnavn** (e.g. `Innlandet`, `Akershus`, `Nordland`)
   — review objects for that county only. **Innlandet** is visible by default;
   other fylke layers start hidden (enable them in the Layers panel).
2. **Fylker-linjer** — Norwegian county boundary lines (embedded)
3. **Kartverket topo** — topographic background imagery for Norway

Fylke assignment follows the NRRL group directory region (same as the coverage
table). The monolithic [`repeaters_review.osm`](output/repeaters_review.osm)
file is still written for tools that want everything in one layer.

Some repeater positions in **Innlandet** have been corrected in `overrides.toml` (operator / mast pins) and are reflected in the published review artefacts. Re-open `repeaters.joz` after pulling to load those updates.

Opening the same session as a `.jos` file fails (SAX “Content is not allowed in
prolog”) because the archive is zip-compressed.

Outputs (under `paths.output_dir`, default `./output/`):

| File | Purpose |
|------|---------|
| `repeaters_merged.csv` | One row per NRRL repeater, all source coords + flags, plus `elevation_m` (ground ASL from Mapterhorn; for tools such as [SPLAT!](https://github.com/hoche/splat)) |
| `repeaters_review.osm` | JOSM review: existing OSM masts get merged tags; otherwise synthetic nodes + disagreement ways. Portables omitted. Verify tags and local knowledge before upload. Delete `best_source` before upload. |
| `repeaters.joz` | Compressed JOSM session: one review layer per fylkesnavn + Fylker boundaries + Kartverket topo. Open from `output/`. |
| `unmatched.txt` | Callsigns / codeplug channels that could not be matched |

Coordinates for radioid.net come from the published `map.json` dump (the
filtered JSON API currently omits lat/lon). Cache both the API list and the map
dump under `./cache/`.

## Matching and best position

1. Normalize callsigns (uppercase, strip `-B`-style suffixes).
2. Convert Maidenhead `Lokator` to square centre + precision.
3. Parse CTCSS / DCS / DMR ID from NRRL `Info`.
4. Match other sources by callsign, else frequency + distance.
5. Best position priority:
   1. Local override coordinates in `overrides.toml` (operator knowledge)
   2. OSM mast/tower/peak/hill matching QTH name inside the locator square
   3. OSM member of a same-callsign network/site relation inside/near the square
   4. radioid or RepeaterBook if inside/near the locator square
   5. locator square centre

After positions are resolved, ground elevation at `best_lat`/`best_lon` is
sampled from [Mapterhorn](https://mapterhorn.com/) Terrarium tiles
(`elevation_m` in the CSV only — not written to the JOSM/OSM layer). Values are
metres above mean sea level and can feed path-loss tools such as
[SPLAT!](https://github.com/hoche/splat). Attribution:
[mapterhorn.com/attribution](https://mapterhorn.com/attribution).

Flags include `disagreement` (default > 2 km; suppressed when a local
coordinate override is set), `outside_locator:*`, `qrt`,
`portable`, `locator_mismatch`, `osm_network:*`, and `missing_*` when a source
has no match.

Known site notes (portable, QRT, club pages, extra coordinates, `osm_id`) live in
`overrides.toml`. A local `lat`/`lon` there is treated as the resolved site:
JOSM still shows other source nodes for comparison, but does not draw a
disagreement way for that callsign. When an OSM mast/tower/node/way is known
(from Overpass match, nearest landmark within ~75 m, or an explicit `osm_id`
override), the review file downloads that object and merges amateur-radio /
review tags onto it instead of inventing a duplicate node. Verify tags and local
knowledge before uploading to OSM.

Gruppe names are mapped via [nrrl.no/grupper](https://nrrl.no/grupper/) to each
group's NRRL page and (when present) the club `Nettside`. JOSM nodes get
`group`, `nrrl:group_page`, and `website` tags.

Every JOSM node also gets OpenStreetMap amateur-radio tags derived from the
station type, especially
`communication:amateur_radio:repeater:modulation` using WARC-79 emission
codes (e.g. `11K2F3E` FM, `7K60FXE` DMR, `6K00F7W` D-STAR, `9K36F7W` C4FM,
`20K0F2D` APRS/packet). Multi-mode stations use a semicolon-separated list.

## Terms of use

- **NRRL / club sites**: respect NRRL and local group terms when redistributing
  derived lists.
- **Mapterhorn**: terrain tiles for elevation; respect their
  [attribution](https://mapterhorn.com/attribution) when redistributing derived data.
- **OSM / Overpass**: cache responses, rate-limit, do not hammer public instances.
  Review data is derived from public sources; verify tags and local knowledge
  before uploading to OSM.
- **radioid.net**: personal lookup use; do not mirror as a public directory. Send a
  clear User-Agent. See their [API policy](https://radioid.net/api/).
- **RepeaterBook**: approved clients only; token + User-Agent with contact email.
  Personal use; do not redistribute. See their
  [API wiki](https://www.repeaterbook.com/wiki/doku.php?id=api).
- **AnyTone Norge (Facebook)**: community discussion only; do not republish
  members' private posts without permission.

## NRRL CSV columns

`Kallesignal`, `Type`, `QTH`, `Freq TX`, `Freq RX`, `Gruppe`, `Lokator`, `Info`,
`status`.
