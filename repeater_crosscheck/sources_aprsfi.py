"""aprs.fi locations — used only for APRS / digipeater callsigns.

Supports the official JSON API (api_key) or a local web-lookup export
(``data/aprsfi_ld_web.json`` from https://aprs.fi/info/a/<call>).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .cache import RateLimitedSession, ResponseCache
from .models import SourceRecord
from .util import normalize_callsign


def is_aprs_only_type(type_str: str | None) -> bool:
    """True for APRS digi / IGate / LoRa APRS — not FM/DMR/D-STAR/C4FM hybrids."""
    blob = (type_str or "").upper()
    if "APRS" not in blob:
        return False
    if any(x in blob for x in ("FM", "DMR", "DSTAR", "D-STAR", "C4FM")):
        return False
    return True


def _entry_to_record(raw: dict[str, Any]) -> SourceRecord | None:
    name = str(raw.get("name") or raw.get("srccall") or "").strip()
    if not name:
        return None
    try:
        lat = float(raw["lat"]) if raw.get("lat") not in (None, "") else None
        lon = float(raw["lng"]) if raw.get("lng") not in (None, "") else None
    except (TypeError, ValueError, KeyError):
        lat, lon = None, None
    if lat is None or lon is None:
        return None
    cs = normalize_callsign(name)
    if not cs:
        return None
    return SourceRecord(
        callsign=cs,
        callsign_raw=name.upper(),
        lat=lat,
        lon=lon,
        qth=str(raw.get("comment") or "")[:80],
        source_kind="aprsfi",
        source_id=name.upper(),
        status=str(raw.get("status") or ""),
        extra={
            "lasttime": raw.get("lasttime"),
            "symbol": raw.get("symbol"),
            "srccall": raw.get("srccall"),
        },
    )


def load_aprsfi_web_json(path: Path) -> dict[str, SourceRecord]:
    """Load positions scraped from aprs.fi station info pages."""
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return {}
    out: dict[str, SourceRecord] = {}
    for raw in entries:
        if not isinstance(raw, dict) or not raw.get("found"):
            continue
        call = normalize_callsign(str(raw.get("call") or ""))
        try:
            lat = float(raw["lat"]) if raw.get("lat") is not None else None
            lon = float(raw["lon"]) if raw.get("lon") is not None else None
        except (TypeError, ValueError):
            lat, lon = None, None
        if not call or lat is None or lon is None:
            continue
        out[call] = SourceRecord(
            callsign=call,
            callsign_raw=call,
            lat=lat,
            lon=lon,
            source_kind="aprsfi",
            source_id=call,
            extra={"source": "aprs.fi web", "last": raw.get("last") or ""},
        )
    return out


def load_aprsfi_locations(
    session: RateLimitedSession,
    cache: ResponseCache,
    callsigns: list[str],
    *,
    api_key: str,
    base_url: str = "https://api.aprs.fi/api",
    batch_size: int = 20,
    min_interval_s: float = 1.0,
) -> dict[str, SourceRecord]:
    """
    Query aprs.fi ``what=loc`` for the given callsigns (batches of up to 20).

    Returns a map of normalized callsign -> best SourceRecord (prefers an exact
    name match over SSID variants). Callsigns with no hit are absent from the map.
    """
    key = (api_key or "").strip()
    if not key:
        return {}

    unique: list[str] = []
    seen: set[str] = set()
    for raw in callsigns:
        cs = normalize_callsign(raw)
        if cs and cs not in seen:
            seen.add(cs)
            unique.append(cs)
    if not unique:
        return {}

    # Prefer exact call, then common digi SSIDs, when matching responses.
    by_call: dict[str, list[SourceRecord]] = {c: [] for c in unique}
    url = f"{base_url.rstrip('/')}/get"

    for i in range(0, len(unique), batch_size):
        batch = unique[i : i + batch_size]
        cache_key = "aprsfi_loc_" + "_".join(batch)
        cached = cache.get_json(cache_key)
        if cached is None:
            if i > 0 and min_interval_s > 0:
                time.sleep(min_interval_s)
            resp = session.request(
                "GET",
                url,
                params={
                    "name": ",".join(batch),
                    "what": "loc",
                    "apikey": key,
                    "format": "json",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            if str(payload.get("result") or "").lower() != "ok":
                raise RuntimeError(
                    f"aprs.fi error: {payload.get('description') or payload}"
                )
            cached = payload.get("entries") or []
            cache.put_json(cache_key, cached)

        for raw in cached:
            if not isinstance(raw, dict):
                continue
            rec = _entry_to_record(raw)
            if rec is None:
                continue
            # Map SSID variants (LD2GG-1) onto base call when that was queried.
            base = normalize_callsign(rec.callsign_raw)
            if base in by_call:
                by_call[base].append(rec)
            elif rec.callsign in by_call:
                by_call[rec.callsign].append(rec)

    out: dict[str, SourceRecord] = {}
    for cs, hits in by_call.items():
        if not hits:
            continue
        # Prefer exact name == callsign, then most recently heard.
        def sort_key(r: SourceRecord) -> tuple:
            exact = 0 if r.callsign_raw.upper() == cs else 1
            try:
                last = -int(r.extra.get("lasttime") or 0)
            except (TypeError, ValueError):
                last = 0
            return (exact, last, r.source_id)

        hits.sort(key=sort_key)
        out[cs] = hits[0]
    return out
