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


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_overrides(path: Path | None) -> dict[str, LocalOverride]:
    if path is None or not path.is_file():
        return {}
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    out: dict[str, LocalOverride] = {}
    for key, val in raw.items():
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
        out[call] = LocalOverride(
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
        )
    return out
