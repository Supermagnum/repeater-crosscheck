from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from .util import override_callsign_key


@dataclass
class LocalOverride:
    callsign: str
    lat: float | None = None
    lon: float | None = None
    tx_mhz: float | None = None
    rx_mhz: float | None = None
    tone: str = ""
    qth: str = ""
    group: str = ""
    type: str = ""
    portable: bool = False
    qrt: bool | None = None  # None = inherit NRRL; False clears NRRL QRT
    on_air: bool = False
    status: str = ""
    url: str = ""
    note: str = ""
    osm_id: str = ""  # e.g. node/5588495799 — merge review tags onto this object
    skip_osm: bool = False  # do not attach / match any OSM object for this callsign
    skip: bool = False  # omit this callsign from merge/outputs entirely
    # Emit osm_id as infrastructure-only; put this callsign on a co-located synthetic.
    detach_osm: bool = False


@dataclass
class LoadedOverrides:
    by_callsign: dict[str, LocalOverride]
    # OSM refs to emit with amateur/callsign tags stripped (tower/mast only).
    scrub_osm: list[str]


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_osm_id_list(val) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        parts = [val]
    elif isinstance(val, list):
        parts = val
    else:
        return []
    out: list[str] = []
    for raw in parts:
        text = str(raw or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def load_overrides(path: Path | None) -> LoadedOverrides:
    if path is None or not path.is_file():
        return LoadedOverrides(by_callsign={}, scrub_osm=[])
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    out: dict[str, LocalOverride] = {}
    scrub: list[str] = []
    for key, val in raw.items():
        if key in {"scrub_osm", "scrub"}:
            if isinstance(val, dict):
                scrub.extend(_parse_osm_id_list(val.get("ids") or val.get("osm_id")))
            else:
                scrub.extend(_parse_osm_id_list(val))
            continue
        if not isinstance(val, dict):
            continue
        call = override_callsign_key(str(key))
        if not call:
            continue
        lat_f = _optional_float(val.get("lat"))
        lon_f = _optional_float(val.get("lon"))
        if (lat_f is None) ^ (lon_f is None):
            lat_f, lon_f = None, None
        qrt: bool | None
        if "qrt" in val:
            qrt = bool(val.get("qrt"))
        else:
            qrt = None
        tone = str(val.get("tone") or val.get("ctcss") or "").strip()
        osm_raw = str(val.get("osm_id") or val.get("osm") or "").strip()
        detach = bool(val.get("detach_osm"))
        ov = LocalOverride(
            callsign=call,
            lat=lat_f,
            lon=lon_f,
            tx_mhz=_optional_float(
                val.get("tx") if val.get("tx") is not None else val.get("tx_mhz")
            ),
            rx_mhz=_optional_float(
                val.get("rx") if val.get("rx") is not None else val.get("rx_mhz")
            ),
            tone=tone,
            qth=str(val.get("qth") or "").strip(),
            group=str(val.get("group") or val.get("gruppe") or "").strip(),
            type=str(val.get("type") or "").strip(),
            portable=bool(val.get("portable")),
            qrt=qrt,
            on_air=bool(val.get("on_air")),
            status=str(val.get("status") or "").strip(),
            url=str(val.get("url") or "").strip(),
            note=str(val.get("note") or "").strip(),
            osm_id=osm_raw,
            skip_osm=bool(val.get("skip_osm")),
            skip=bool(val.get("skip")),
            detach_osm=detach,
        )
        out[call] = ov
        if detach and osm_raw and osm_raw not in scrub:
            scrub.append(osm_raw)
    return LoadedOverrides(by_callsign=out, scrub_osm=scrub)
