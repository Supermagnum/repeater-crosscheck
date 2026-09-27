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

Published review artefacts in this repository:

- [`output/repeaters_merged.csv`](output/repeaters_merged.csv)
- [`output/repeaters_review.osm`](output/repeaters_review.osm) (JOSM review layer)
- [`output/LA-repeaters.joz`](output/LA-repeaters.joz) (JOSM session for fylke-by-fylke review)

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
```

Open [`output/LA-repeaters.joz`](output/LA-repeaters.joz) in JOSM (from the `output/` folder).
It is a compressed JOSM session (`.joz`, not `.jos`) with these layers:

1. **Fylker** — Norwegian county (`admin_level=4`) boundaries (plus Svalbard), so you can
   zoom to and work on one fylke at a time
2. **repeaters_review.osm** — the generated review data (merged tags on existing masts /
   towers, plus synthetic review nodes where needed)
3. **OpenStreetMap Carto (Standard)** — background imagery / map tiles

Toggle or filter on the Fylker layer to concentrate on one county, then review and
upload only after tags and local knowledge have been checked.

Note: a plain `.jos` file is XML. This session embeds the Fylker OSM data, so it must
be opened as `.joz` (zip). Opening it as `.jos` causes a SAX “Content is not allowed
in prolog” error.

Outputs (under `paths.output_dir`, default `./output/`):

| File | Purpose |
|------|---------|
| `repeaters_merged.csv` | One row per NRRL repeater, all source coords + flags |
| `repeaters_review.osm` | JOSM review: existing OSM masts get merged tags; otherwise synthetic nodes + disagreement ways. Verify tags and local knowledge before upload. |
| `LA-repeaters.joz` | Compressed JOSM session (`.joz`): Fylker boundaries + `repeaters_review.osm` + OSM Carto. Use the Fylker layer to work one county at a time. Open from `output/`. |
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
