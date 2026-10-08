from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Position:
    lat: float
    lon: float
    source_kind: str
    source_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceRecord:
    callsign: str
    callsign_raw: str = ""
    tx_mhz: float | None = None
    rx_mhz: float | None = None
    lat: float | None = None
    lon: float | None = None
    tone: str = ""
    dmr_id: str = ""
    qth: str = ""
    city: str = ""
    source_kind: str = ""
    source_id: str = ""
    status: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CodeplugChannel:
    number: str
    name: str
    callsign: str
    rx_mhz: float | None
    tx_mhz: float | None
    zones: list[str] = field(default_factory=list)


@dataclass
class NrrlRepeater:
    callsign: str
    callsign_raw: str
    type: str
    qth: str
    tx_mhz: float | None
    rx_mhz: float | None
    group: str
    locator: str
    info: str
    status: str
    tone: str = ""
    dmr_id: str = ""
    locator_lat: float | None = None
    locator_lon: float | None = None
    locator_precision_m: float | None = None
    locator_bbox: tuple[float, float, float, float] | None = None  # S,W,N,E


@dataclass
class MergedRepeater:
    callsign: str
    type: str = ""
    qth: str = ""
    tx: str = ""
    rx: str = ""
    tone: str = ""
    dmr_id: str = ""
    group: str = ""
    status: str = ""
    locator: str = ""
    locator_lat: float | None = None
    locator_lon: float | None = None
    locator_precision_m: float | None = None
    osm_lat: float | None = None
    osm_lon: float | None = None
    osm_id: str = ""
    osm_match: str = ""
    radioid_lat: float | None = None
    radioid_lon: float | None = None
    repeaterbook_lat: float | None = None
    repeaterbook_lon: float | None = None
    aprsfi_lat: float | None = None
    aprsfi_lon: float | None = None
    local_lat: float | None = None
    local_lon: float | None = None
    notes: str = ""
    best_lat: float | None = None
    best_lon: float | None = None
    best_source: str = ""
    elevation_m: float | None = None  # ground ASL (m) from Mapterhorn DEM
    max_disagreement_m: float | None = None
    flags: list[str] = field(default_factory=list)
    codeplug_channels: list[str] = field(default_factory=list)
    codeplug_zones: list[str] = field(default_factory=list)
    group_nrrl_url: str = ""
    group_website: str = ""
    match_methods: dict[str, str] = field(default_factory=dict)
    positions: list[Position] = field(default_factory=list)
