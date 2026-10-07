"""Export analog repeaters for HTCommander / VR-N76.

Writes:
  - CHIRP CSV (flat import)
  - htcommander-regions JSON (VR-N76 channel groups: 6 regions x 32 slots)

Linked networks (LA5MR, Fylkesnettet) become named regions in the JSON file.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .models import MergedRepeater

# VR-N76: 6 editable channel groups, 32 channels each.
VR_N76_REGION_COUNT = 6
VR_N76_CHANNEL_COUNT = 32
REGIONS_FORMAT_ID = "htcommander-regions"
REGIONS_FORMAT_VERSION = 1


CHIRP_HEADER = [
    "Location",
    "Name",
    "Frequency",
    "Duplex",
    "Offset",
    "Tone",
    "rToneFreq",
    "cToneFreq",
    "DtcsCode",
    "DtcsPolarity",
    "RxDtcsCode",
    "CrossMode",
    "Mode",
    "TStep",
    "Skip",
    "Power",
    "Comment",
    "URCALL",
    "RPT1CALL",
    "RPT2CALL",
    "DVCODE",
]

# OSM type=network relations for Norwegian linked repeater systems.
NETWORK_RELATIONS = {
    "LA5MR": {
        "relation": "relation/18780801",
        "title": "LA5MR / Innlandsnettet",
        "comment_tag": "LA5MR",
        # Voice sites in the LA5MR OSM relation + NRRL Innlandsnettet FM.
        "callsigns": {
            "LA5MR",
            "LA5TRR",
            "LA6NR",
            "LA6GR",
            "LA9AR",
            "LA2KRR",
        },
    },
    "Fylkesnettet": {
        "relation": "relation/18788322",
        "title": "Fylkesnettet Vestfold/Telemark",
        "comment_tag": "Fylkesnettet",
        # Linked VHF sites (LA3DRR is the UHF hub at Vealøs, not in this group).
        "callsigns": {
            "LA3GRR",
            "LA5ER",
            "LA3BRR",
            "LA5GR",
            "LA3SRR",
            "LA3XRR",
        },
    },
    # RepeaterBook LA6JR features: linked to LA6KR, LA9KR, LA5AR, LA4ARR.
    "LA6JR": {
        "relation": "",
        "title": "LA6JR / Sørlandet linked",
        "comment_tag": "LA6JR",
        "callsigns": {
            "LA6JR",
            "LA6KR",
            "LA5AR",
            "LA4ARR",
            "LA9KR",
        },
    },
}

# Members missing from NRRL (or listed under another callsign) — OSM / club nets.
SUPPLEMENTAL_CHANNELS: dict[str, dict[str, str]] = {
    "LA3BRR": {
        "callsign": "LA3BRR",
        "qth": "Bronane/Drangedal",
        "tx": "145.5625",
        "rx": "144.9625",
        "tone": "74.4",
        "type": "FM Repeater",
        "status": "",
        "notes": "Fylkesnettet member (OSM relation/18788322).",
    },
    "LA3GRR": {
        "callsign": "LA3GRR",
        "qth": "Gaustatoppen",
        "tx": "145.6125",
        "rx": "145.0125",
        "tone": "74.4",
        "type": "FM Repeater",
        "status": "",
        "notes": "Fylkesnettet member (OSM relation/18788322).",
    },
}

# Prefer OSM/club RF identity for these when writing network group files.
NETWORK_FREQ_OVERRIDES: dict[str, dict[str, str]] = {
    # NRRL still lists LA3SRR as crossband; OSM + Grenland net list it as VHF -0.6.
    "LA3SRR": {"tx": "145.275", "rx": "144.675", "tone": "74.4"},
}

_SKIP_STATUS = re.compile(r"qrt|planlagt", re.I)
_ANALOG_TYPE = re.compile(
    r"^(FM|Crossband)",
    re.I,
)
_DIGITAL_ONLY = re.compile(
    r"^(DMR|DSTAR|D-STAR|C4FM|APRS|Winlink|BPQ)",
    re.I,
)


@dataclass
class ChirpChannel:
    name: str
    rx_mhz: float  # Frequency column = listen (repeater TX)
    tx_mhz: float  # user transmit (repeater RX)
    tone_hz: float | None
    mode: str  # FM or NFM
    comment: str
    power: str = "5W"


def _parse_mhz(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", ".")
    if not s:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return v


def _parse_tone(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in {"1750", "Off", "OFF"}:
        return None
    # "110.9/ID: 242101" or "1750/88.5"
    for part in re.split(r"[/;|]", s):
        part = part.strip().replace(",", ".")
        if part.upper().startswith("DCS"):
            return None
        try:
            v = float(part)
        except ValueError:
            continue
        if 67.0 <= v <= 254.1:
            return v
    return None


def _clamp_name(name: str, limit: int = 10) -> str:
    name = name.strip()
    if len(name) <= limit:
        return name
    return name[:limit]


def _channel_name(callsign: str, qth: str) -> str:
    """Prefer callsign (fits HTCommander 10-char limit); fall back to QTH."""
    cs = (callsign or "").strip().upper()
    if cs and len(cs) <= 10:
        return cs
    q = (qth or "").split("/", 1)[0].strip()
    q = re.sub(r"[^\w\- ]+", "", q, flags=re.UNICODE)
    return _clamp_name(q or cs or "CH")


def _is_exportable_analog(row: dict[str, str]) -> bool:
    status = (row.get("status") or "").strip()
    if status and _SKIP_STATUS.search(status):
        return False
    flags = (row.get("flags") or "").lower()
    if "qrt" in flags.split("|"):
        return False
    typ = (row.get("type") or "").strip()
    if not typ:
        return False
    if _DIGITAL_ONLY.search(typ) and "FM" not in typ.upper():
        return False
    if not _ANALOG_TYPE.search(typ) and "FM" not in typ.upper():
        return False
    # Pure digital hybrids without usable FM side still have FM in the type
    # string sometimes (FM/DMR). Keep those — operator can use analog.
    tx = _parse_mhz(row.get("tx"))
    rx = _parse_mhz(row.get("rx"))
    if tx is None or rx is None:
        return False
    # Skip HF / microwave outside typical HT coverage (VHF/UHF).
    if tx < 50 or tx > 520:
        return False
    # APRS / digi simplex on 144.8 is not a voice repeater memory.
    if abs(tx - 144.8) < 1e-6 and abs(rx - 144.8) < 1e-6:
        return False
    return True


def row_to_chirp(row: dict[str, str], *, network_tag: str = "") -> ChirpChannel | None:
    if not _is_exportable_analog(row):
        return None
    listen = _parse_mhz(row.get("tx"))
    talk = _parse_mhz(row.get("rx"))
    if listen is None or talk is None:
        return None
    tone = _parse_tone(row.get("tone"))
    callsign = (row.get("callsign") or "").strip().upper()
    qth = (row.get("qth") or "").strip()
    name = _channel_name(callsign, qth)
    # Norwegian repeaters are coordinated as 12.5 kHz narrow FM.
    mode = "NFM"
    parts = [p for p in (callsign, qth, network_tag) if p]
    notes = (row.get("notes") or "").strip()
    if notes:
        parts.append(notes.split(".", 1)[0][:60])
    comment = " | ".join(parts)
    return ChirpChannel(
        name=name,
        rx_mhz=listen,
        tx_mhz=talk,
        tone_hz=tone,
        mode=mode,
        comment=comment,
    )


def chirp_to_row(ch: ChirpChannel, location: int) -> dict[str, str]:
    duplex = ""
    offset = 0.0
    # Cross-band / non-standard shift: encode as split (Offset = TX MHz).
    delta = ch.tx_mhz - ch.rx_mhz
    if abs(delta) < 1e-7:
        duplex = ""
        offset = 0.0
    elif abs(abs(delta) - 0.6) < 1e-4 or abs(abs(delta) - 2.0) < 1e-4 or abs(
        abs(delta) - 0.1
    ) < 1e-4:
        duplex = "-" if delta < 0 else "+"
        offset = abs(delta)
    else:
        duplex = "split"
        offset = ch.tx_mhz

    tone_mode = ""
    r_tone = "88.5"
    c_tone = "88.5"
    if ch.tone_hz is not None:
        tone_mode = "Tone"
        r_tone = f"{ch.tone_hz:.1f}"
        c_tone = r_tone

    return {
        "Location": str(location),
        "Name": ch.name,
        "Frequency": f"{ch.rx_mhz:.6f}",
        "Duplex": duplex,
        "Offset": f"{offset:.6f}",
        "Tone": tone_mode,
        "rToneFreq": r_tone,
        "cToneFreq": c_tone,
        "DtcsCode": "023",
        "DtcsPolarity": "NN",
        "RxDtcsCode": "023",
        "CrossMode": "Tone->Tone",
        "Mode": ch.mode,
        "TStep": "12.50",
        "Skip": "",
        "Power": ch.power,
        "Comment": ch.comment,
        "URCALL": "",
        "RPT1CALL": "",
        "RPT2CALL": "",
        "DVCODE": "",
    }


def merged_to_dict(row: MergedRepeater) -> dict[str, str]:
    return {
        "callsign": row.callsign,
        "type": row.type,
        "qth": row.qth,
        "tx": "" if row.tx is None else str(row.tx),
        "rx": "" if row.rx is None else str(row.rx),
        "tone": row.tone or "",
        "status": row.status or "",
        "flags": "|".join(row.flags),
        "notes": row.notes or "",
        "group": row.group or "",
    }


def _apply_overrides(row: dict[str, str]) -> dict[str, str]:
    cs = (row.get("callsign") or "").upper()
    ov = NETWORK_FREQ_OVERRIDES.get(cs)
    if not ov:
        return row
    out = dict(row)
    out.update(ov)
    return out


def collect_channels(rows: list[dict[str, str]]) -> list[ChirpChannel]:
    out: list[ChirpChannel] = []
    seen: set[tuple[str, float, float]] = set()
    for row in rows:
        ch = row_to_chirp(row)
        if ch is None:
            continue
        key = (ch.name, round(ch.rx_mhz, 5), round(ch.tx_mhz, 5))
        if key in seen:
            continue
        seen.add(key)
        out.append(ch)
    return out


def collect_network_channels(
    rows: list[dict[str, str]],
    network_key: str,
) -> list[ChirpChannel]:
    meta = NETWORK_RELATIONS[network_key]
    wanted = {c.upper() for c in meta["callsigns"]}
    tag = meta["comment_tag"]
    by_cs: dict[str, dict[str, str]] = {}
    for row in rows:
        cs = (row.get("callsign") or "").upper()
        if cs in wanted:
            by_cs[cs] = row
    for cs in wanted:
        if cs not in by_cs and cs in SUPPLEMENTAL_CHANNELS:
            by_cs[cs] = dict(SUPPLEMENTAL_CHANNELS[cs])

    out: list[ChirpChannel] = []
    for cs in sorted(wanted):
        row = by_cs.get(cs)
        if row is None:
            continue
        row = _apply_overrides(row)
        # Network members stay in the group even if NRRL still says Planlagt.
        if (row.get("status") or "").lower() == "planlagt":
            row = dict(row)
            row["status"] = ""
        ch = row_to_chirp(row, network_tag=tag)
        if ch is not None:
            out.append(ch)
    return out


def write_chirp_csv(path: Path, channels: list[ChirpChannel]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CHIRP_HEADER, lineterminator="\n")
        writer.writeheader()
        for i, ch in enumerate(channels):
            writer.writerow(chirp_to_row(ch, i))
    return len(channels)


def chirp_to_radio_channel(ch: ChirpChannel | None, channel_id: int) -> dict:
    """RadioChannelInfo JSON map used by HTCommander regions files."""
    if ch is None:
        return {
            "channelId": channel_id,
            "name": "",
            "rxFreq": 0,
            "txFreq": 0,
            "txSubAudio": 0,
            "rxSubAudio": 0,
            "scan": False,
            "txAtMaxPower": True,
            "txAtMedPower": False,
            "talkAround": False,
            "preDeEmphBypass": False,
            "sign": False,
            "txDisable": False,
            "mute": False,
            "fixedFreq": False,
            "fixedBandwidth": False,
            "fixedTxPower": False,
            "txMod": 0,  # FM
            "rxMod": 0,
            "bandwidth": 0,  # narrow
        }
    tone_x100 = 0
    if ch.tone_hz is not None:
        tone_x100 = int(round(ch.tone_hz * 100))
    return {
        "channelId": channel_id,
        "name": _clamp_name(ch.name),
        "rxFreq": int(round(ch.rx_mhz * 1_000_000)),
        "txFreq": int(round(ch.tx_mhz * 1_000_000)),
        # Tone encode only (matches CHIRP Tone mode).
        "txSubAudio": tone_x100,
        "rxSubAudio": 0,
        "scan": True,
        "txAtMaxPower": True,
        "txAtMedPower": False,
        "talkAround": False,
        "preDeEmphBypass": False,
        "sign": False,
        "txDisable": False,
        "mute": False,
        "fixedFreq": False,
        "fixedBandwidth": False,
        "fixedTxPower": False,
        "txMod": 0,
        "rxMod": 0,
        "bandwidth": 0 if ch.mode == "NFM" else 1,
    }


def _pad_region_channels(
    channels: list[ChirpChannel],
    slot_count: int = VR_N76_CHANNEL_COUNT,
) -> list[dict]:
    if len(channels) > slot_count:
        channels = channels[:slot_count]
    out: list[dict] = []
    for i in range(slot_count):
        ch = channels[i] if i < len(channels) else None
        out.append(chirp_to_radio_channel(ch, i))
    return out


def build_vr_n76_regions(dict_rows: list[dict[str, str]]) -> dict:
    """
    HTCommander all-regions JSON for VR-N76 channel groups.

    Region 0 = LA5MR / Innlandsnettet
    Region 1 = Fylkesnettet
    Region 2 = LA6JR / Sørlandet linked
    Regions 3-5 = empty (rename in HTCommander as needed)
    """
    region_specs: list[tuple[str, list[ChirpChannel]]] = [
        ("LA5MR", collect_network_channels(dict_rows, "LA5MR")),
        ("Fylkesnettet", collect_network_channels(dict_rows, "Fylkesnettet")),
        ("LA6JR", collect_network_channels(dict_rows, "LA6JR")),
    ]
    while len(region_specs) < VR_N76_REGION_COUNT:
        idx = len(region_specs) + 1
        region_specs.append((f"Group {idx}", []))

    regions = []
    for index, (name, channels) in enumerate(region_specs):
        regions.append(
            {
                "index": index,
                "name": name[:16],
                "channels": _pad_region_channels(channels),
            }
        )
    return {
        "format": REGIONS_FORMAT_ID,
        "version": REGIONS_FORMAT_VERSION,
        "regionCount": VR_N76_REGION_COUNT,
        "channelCount": VR_N76_CHANNEL_COUNT,
        "regions": regions,
    }


def write_regions_json(path: Path, regions_doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(regions_doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_htcommander_exports(
    output_dir: Path,
    rows: list[MergedRepeater] | list[dict[str, str]],
) -> list[Path]:
    """Write CHIRP CSVs plus a VR-N76 regions JSON for HTCommander."""
    dict_rows: list[dict[str, str]]
    if rows and isinstance(rows[0], MergedRepeater):
        dict_rows = [merged_to_dict(r) for r in rows]  # type: ignore[arg-type]
    else:
        dict_rows = list(rows)  # type: ignore[arg-type]

    ht_dir = output_dir / "htcommander"
    written: list[Path] = []

    all_path = ht_dir / "norway_analog.csv"
    n = write_chirp_csv(all_path, collect_channels(dict_rows))
    written.append(all_path)
    print(f"Wrote {all_path} ({n} channels)")

    for key, meta in NETWORK_RELATIONS.items():
        path = ht_dir / f"{key}.csv"
        channels = collect_network_channels(dict_rows, key)
        n = write_chirp_csv(path, channels)
        written.append(path)
        print(
            f"Wrote {path} ({n} channels, {meta['title']}, {meta['relation']})"
        )

    regions_path = ht_dir / "norway_regions.json"
    doc = build_vr_n76_regions(dict_rows)
    write_regions_json(regions_path, doc)
    written.append(regions_path)
    filled = sum(
        1
        for r in doc["regions"]
        for c in r["channels"]
        if c.get("rxFreq", 0) > 0
    )
    print(
        f"Wrote {regions_path} "
        f"({doc['regionCount']} regions x {doc['channelCount']} slots, "
        f"{filled} programmed; VR-N76 / HTCommander regions)"
    )
    return written


def load_merged_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))
