# repeater-crosscheck

Cross-check Norwegian amateur radio repeater positions and write a merged CSV
plus a JOSM review file.

Sources used here are public data. Before uploading any changes to OSM, check that
tags are not broken and that the data matches local knowledge.

## Table of contents

- [Source data](#source-data)
- [OSM coverage](#osm-coverage)
- [OSM tagging: one feature per repeater (Norway)](#osm-tagging-one-feature-per-repeater-norway)
- [OSM tagging: linked repeaters (type=network)](#osm-tagging-linked-repeaters-typenetwork)
- [Setup](#setup)
- [Usage](#usage)
  - [HTCommander / VR-N76](#htcommander--vr-n76)
  - [Reviewing in JOSM](#reviewing-in-josm)
  - [What is `repeaters.joz`?](#what-is-repeatersjoz)
- [Matching and best position](#matching-and-best-position)
- [Terms of use](#terms-of-use)
- [NRRL CSV columns](#nrrl-csv-columns)

## Source data

As far as the author knows, these sources are dated **28 September 2026**.

Primary inputs and comparison sources:

- **[NRRL](https://nrrl.no/)** — official Norwegian repeater list (CSV:
  `Kallesignal`, `Type`, `QTH`, frequencies, `Gruppe`, `Lokator`, `Info`,
  `status`), plus [group pages](https://nrrl.no/grupper/) for club websites
- **[RepeaterBook](https://www.repeaterbook.com/)** — Norway exportROW pins (API
  App #114 token, or local JSON under `paths.repeaterbook_json`) preferred over
  Maidenhead square centres
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
- [`output/htcommander/`](output/htcommander/) (HTCommander / VR-N76 channel groups)

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

## OSM tagging: one feature per repeater (Norway)

Clubs in Norway often mount several services on the **same physical mast**
(for example an FM repeater and an APRS digipeater). That co-location is real,
but OpenStreetMap practice outside Norway is still **one mapped feature per
callsign/band**, not a semicolon stack of callsigns on a single object.

This tool follows that convention:

- Each NRRL callsign (and each TX band of that callsign) gets its **own** review
  node, even when `overrides.toml` pins several callsigns to the same mast
  coordinates or `osm_id`.
- At most **one** callsign/band may have its tags merged onto an existing OSM
  mast/tower object (voice repeaters preferred over APRS/packet digis). The
  others are emitted as co-located synthetic nodes at the same site.
- Do **not** upload `callsign=LA7GR;LD2GG`-style multi-value callsign tags.
  Semicolon lists remain appropriate only where the OSM wiki already uses them
  (for example multi-mode `modulation`).

Shared mast → same coordinates; separate OSM elements → separate callsigns.

When a site position is uncertain, consult the NRRL list
(`Relestasjoner.csv` — the `QTH`, `Lokator`, and frequency columns) and move the
object in JOSM to the position that fits best given local knowledge, imagery,
and other sources. Do not upload a guess you cannot defend.

It is a good idea to look for **misplaced nodes** that should sit on mountain
peaks (or other high ground) with relevant infrastructure, and to check
**nearby cellular masts** and similar towers on the map or imagery. For **LD**
APRS / packet stations, check [aprs.fi](https://aprs.fi/) (and
[aprs.no](https://aprs.no/)): if a callsign does not appear there, it is likely
offline or **QRT** (dead). Mark those in `overrides.toml` (`qrt = true`).
QRT / off-air stations are omitted from CSV, OSM, `.joz`, and HTCommander
exports automatically.

## OSM tagging: linked repeaters (type=network)

Some Norwegian systems are **linked**: several fixed sites share a common
analogue network (same tone plan / linked coverage). On OpenStreetMap that is
modelled as a **`type=network` relation**, not by stacking callsigns on one
object and not by a `network=*` tag alone.

Reference (already on OSM):
[relation/18780801](https://www.openstreetmap.org/relation/18780801) (LA5MR /
Innlandsnettet). Open a member node there — it appears under **Relations**
because it is listed as a **member** of that relation.

### Relation tags

| Key | Value | Notes |
|-----|-------|-------|
| `type` | `network` | Required |
| `name` | Network name | e.g. `LA5MR`, `Fylkesnettet`, `Agder net` |
| `communication:amateur_radio:repeater` | `yes` | Marks it as an amateur repeater network |
| `operator` | Club / service | Optional |
| `website` | URL | Optional |
| `source` | Short provenance | Optional for new relations |

### Members

1. Create or open the `type=network` relation in JOSM.
2. Add each linked site as a **member** (role empty): the node or way that
   carries that callsign’s amateur-radio tags.
3. Membership is the ground truth. A `network=…` tag on the node is optional
   documentation only; **without the relation member, OSM does not treat the
   site as part of the network**.

After the relation has a real OSM id (positive), members may also carry:

- `network=<name>` (same as the relation `name`)
- `osm_relation=relation/<id>` (optional convenience tag used in this project)

Do **not** upload `osm_relation=relation/-123` (temporary JOSM ids). Create and
upload the relation first; only then add `osm_relation` pointing at the new id
if you want that tag.

### Declared Norwegian linked systems

Configured in [`repeater_crosscheck/networks.py`](repeater_crosscheck/networks.py)
and emitted in [`output/repeaters.joz`](output/repeaters.joz) (per-fylke layers
plus a **Networks** overview layer):

| Network | OSM relation | Members (callsigns) |
|---------|--------------|---------------------|
| LA5MR / Innlandsnettet | [18780801](https://www.openstreetmap.org/relation/18780801) | LA5MR, LA5TRR, LA6NR, LA6GR, LA9AR, LA2KRR |
| Fylkesnettet | [18788322](https://www.openstreetmap.org/relation/18788322) | LA3XRR, LA3SRR, LA3BRR, LA3GRR, LA5ER, LA5GR, LA6HR |
| Agder net | create / upload from review | LA6KR, LA4ARR, LA4ORR, LA6JR, LA6SR, LA5AR |
| Sandnes net | create / upload from review | LA4WRR, LA4SRR, LA4ERR |
| Bergen-voss | create / upload from review | LA5CRR, LA5LRR, LA6WR |

### How to upload from the review session

1. Open [`output/repeaters.joz`](output/repeaters.joz) in JOSM.
2. Enable the relevant fylke layer (e.g. Vestland) and/or **Networks**.
3. Select the `type=network` relation (e.g. Bergen-voss). Confirm every
   callsign appears under **Members** with the correct node/way id.
4. Download the surrounding OSM data if needed, resolve conflicts, then upload
   **the relation** (and any new member nodes that are not on OSM yet).
5. After upload, verify on openstreetmap.org that a member node lists the
   relation under **Relations** — the same way
   [LA5MR members](https://www.openstreetmap.org/relation/18780801) do.

Do not regenerate disagreement lines or rewrite positions when you only need
to fix network membership. Edit the relation members in JOSM, or adjust
`networks.py` / `overrides.toml` and rebuild review artefacts carefully.

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
- `http.user_agent` — identify yourself (required by radioid / Overpass)
- `repeaterbook.api_token` — optional `rbuapp_…` from [API Applications](https://www.repeaterbook.com/user/api_apps.php)
  for **App #114** (RepeaterBook Python Client). Without a token, set
  `paths.repeaterbook_json` instead.
- `repeaterbook.user_agent` — must be exactly
  `RepeaterBook Python Client/0.6.0 (+micael@jarniac.dev)` for App #114 tokens
  (literal match; do not substitute your own contact).
- `radioid.api_token` — optional `X-API-Token` (recommended for future-proofing)
- `aprsfi.api_key` — optional key from [aprs.fi](https://aprs.fi/) (My account),
  **or** `paths.aprsfi_json` (web info-page export, e.g.
  [`data/aprsfi_ld_web.json`](data/aprsfi_ld_web.json) from
  `https://aprs.fi/info/a/<call>`). Used **only** for APRS digi / IGate
  callsigns; missing stations are omitted when `aprsfi.omit_missing = true`.
  Local overrides still win. FM/DMR/D-STAR etc. are never changed. Credit
  [aprs.fi](https://aprs.fi/).

## Usage

```bash
python -m repeater_crosscheck -c config.toml
python -m repeater_crosscheck -c config.toml --refresh   # re-download caches
python -m repeater_crosscheck -c config.toml --skip-osm  # offline-ish debug
python -m repeater_crosscheck -c config.toml --skip-elevation  # skip Mapterhorn ASL
python -m repeater_crosscheck -c config.toml --htcommander  # also write HTCommander exports
python -m repeater_crosscheck -c config.toml --htcommander-only  # CSV -> HTCommander only
```

### HTCommander / VR-N76

With `--htcommander` (or `[htcommander] enabled = true` in config), the pipeline
writes [`output/htcommander/`](output/htcommander/):

| File | Purpose |
|------|---------|
| `norway_analog.csv` | All exportable analog channels (CHIRP CSV) |
| `LA5MR.csv` | Innlandsnettet linked sites (CHIRP) |
| `Fylkesnettet.csv` | Vestfold/Telemark linked VHF sites (CHIRP) |
| `Agder net.csv` | Agder linked sites (LA6KR / LA4ARR / LA4ORR / LA6JR / LA6SR / LA5AR) |
| `Sandnes net.csv` | Sandnes linked sites (LA4WRR / LA4SRR / LA4ERR) |
| `Bergen-voss.csv` | Bergen–Voss linked sites (LA5CRR / LA5LRR / LA6WR) |
| `norway_regions.json` | VR-N76 channel groups (6×32): LA5MR, Fylkesnettet, Agder net, Sandnes net, Bergen-voss, … |

Import `norway_regions.json` in HTCommander (all-regions / full backup), or import
a CHIRP CSV and drag channels into radio slots. `--htcommander-only` rebuilds
these from an existing `repeaters_merged.csv` without fetching online sources.

#### GPS vs GPS roaming

Do not confuse **GPS position** (where *you* are) with **GPS roaming** (auto-select
a zone/channel from stored repeater coordinates + radius).

| Platform | Own GPS / APRS map | GPS roaming (lat/lon per zone or channel) |
|----------|--------------------|-------------------------------------------|
| Vero VR-N76 (and other Benshi radios used with HTCommander: UV-Pro, GA-5WB, VR-N75, VR-N7500, …) | Yes — radio GPS + HTCommander map / APRS share | **No** — channel memories have no site coordinates |
| HTCommander channel / regions import | N/A | **No** — CHIRP / regions JSON carry RF only |
| AnyTone AT578UV, D868UV, D878UV / D878UV II (and similar CPS GPS Roaming tables) | Yes | **Yes** — CPS *GPS Roaming*: centre + radius switches zone |
| This repo’s AnyTone `channel.csv` / `zone.csv` inputs | N/A | Positions live in `repeaters_merged.csv` (`best_lat` / `best_lon`); not written as AnyTone GPS-roam rows yet |

Open [`output/repeaters.joz`](output/repeaters.joz) in JOSM (from the `output/`
folder), or open [`output/repeaters_review.osm`](output/repeaters_review.osm)
alone. Portable and QRT / off-air stations are omitted from this layer.

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
6. **Sanity-check positions and activity** — look for misplaced nodes that
   should be on mountain peaks with relevant infrastructure, and for nearby
   cellular masts or similar towers. Check **LD** stations against
   [aprs.fi](https://aprs.fi/); if they do not exist there, they are likely
   offline or QRT.

Before upload, delete review-only tags that must not go into OpenStreetMap:
`best_source`, `fixme`, and any leftover process chatter in `note`. The tool
does **not** emit `flags`, `locator`, `review`, `source_kind`, or bare
`frequency` tags — former flag text is folded into `note` instead. Do not
upload until tags and positions have been verified.

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
| `repeaters_merged.csv` | One row per on-air NRRL repeater (QRT omitted), all source coords, process notes in `notes`, plus `elevation_m` (Mapterhorn ASL; for [SPLAT!](https://github.com/hoche/splat)) |
| `repeaters_review.osm` | JOSM review: at most one callsign/band merged onto an existing OSM mast; co-located siblings are separate nodes. Disagreement ways when sources diverge. Portable and QRT omitted. Verify before upload; delete `best_source` / `fixme`. |
| `repeaters.joz` | Compressed JOSM session: one review layer per fylkesnavn + Networks (type=network relations) + Fylker boundaries + Kartverket topo. Open from `output/`. |
| `htcommander/` | Optional (`--htcommander`): CHIRP CSVs + VR-N76 regions JSON for HTCommander |
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
   1. Local override coordinates in `overrides.toml` (operator knowledge) — never overwritten
   2. OSM feature already tagged with the callsign
   3. OSM mast/tower/peak/hill matching QTH name inside the locator square
   4. OSM member of a same-callsign network/site relation inside/near the square
   5. **aprs.fi** live position (APRS digi / IGate rows only)
   6. radioid.net if inside/near the locator square
   7. **RepeaterBook** lat/lon (preferred over Maidenhead; local JSON or API)
   8. Maidenhead locator square centre (last resort — often kilometres off)

Without a RepeaterBook API token, set `paths.repeaterbook_json` to a Norway
export (CHIRP `rb-norway-all.json` / exportROW shape). A copy ships as
[`data/repeaterbook_norway.json`](data/repeaterbook_norway.json).

After positions are resolved, ground elevation at `best_lat`/`best_lon` is
sampled from [Mapterhorn](https://mapterhorn.com/) Terrarium tiles
(`elevation_m` in the CSV only — not written to the JOSM/OSM layer). Values are
metres above mean sea level and can feed path-loss tools such as
[SPLAT!](https://github.com/hoche/splat). Attribution:
[mapterhorn.com/attribution](https://mapterhorn.com/attribution).

Process markers (`disagreement`, `outside_locator:*`, `portable`,
`locator_mismatch`, `osm_network:*`, `missing_*`, …) are folded into the OSM
`note` / CSV `notes` field — there is no separate `flags` tag on the review
layer. QRT stations are dropped from exports entirely (override with
`on_air = true` / `qrt = false` when NRRL is stale).

Known site notes (portable, club pages, extra coordinates, `osm_id`) live in
`overrides.toml`. A local `lat`/`lon` there is treated as the resolved site:
JOSM still shows other source nodes for comparison, but does not draw a
disagreement way for that callsign. When an OSM mast/tower/node/way is known
(from Overpass match, nearest landmark within ~75 m, or an explicit `osm_id`
override), **one** callsign/band may merge amateur-radio tags onto that object;
other co-located callsigns get their own review nodes at the same coordinates
(see “OSM tagging: one feature per repeater” above). Verify tags and local
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
- **RepeaterBook**: App #114 token + that app's registered User-Agent (see above).
  Personal use; do not redistribute. See their
  [API wiki](https://www.repeaterbook.com/wiki/doku.php?id=api).
- **aprs.fi**: free API with attribution; each user needs their own key.
  See [aprs.fi API terms](https://aprs.fi/page/api). Used only for APRS digis.
- **AnyTone Norge (Facebook)**: community discussion only; do not republish
  members' private posts without permission.

## NRRL CSV columns

`Kallesignal`, `Type`, `QTH`, `Freq TX`, `Freq RX`, `Gruppe`, `Lokator`, `Info`,
`status`.
