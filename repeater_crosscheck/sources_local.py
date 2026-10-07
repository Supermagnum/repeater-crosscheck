from __future__ import annotations

import csv
from pathlib import Path

from .models import CodeplugChannel, NrrlRepeater, SourceRecord
from .util import (
    extract_callsigns,
    maidenhead_to_bbox,
    normalize_callsign,
    parse_nrrl_info,
)


def _parse_mhz(value: str | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    # NRRL (and similar) sometimes drops the decimal (e.g. 4329375 -> 432.9375).
    if v > 1300:
        for div in (1_000_000.0, 10_000.0, 1_000.0):
            cand = v / div
            if 28.0 <= cand <= 1300.0:
                return cand
    return v


def load_nrrl(path: Path) -> list[NrrlRepeater]:
    rows: list[NrrlRepeater] = []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            call_raw = (raw.get("Kallesignal") or "").strip()
            call = normalize_callsign(call_raw)
            if not call:
                continue
            locator = (raw.get("Lokator") or "").strip()
            info = (raw.get("Info") or "").strip()
            tone, dmr_id = parse_nrrl_info(info)
            loc = maidenhead_to_bbox(locator)
            tx_mhz = _parse_mhz(raw.get("Freq TX"))
            rx_mhz = _parse_mhz(raw.get("Freq RX"))
            # Placeholder rows with no usable frequency (e.g. LA9NRR).
            if tx_mhz is None or tx_mhz <= 0:
                continue
            rows.append(
                NrrlRepeater(
                    callsign=call,
                    callsign_raw=call_raw,
                    type=(raw.get("Type") or "").strip(),
                    qth=(raw.get("QTH") or "").strip(),
                    tx_mhz=tx_mhz,
                    rx_mhz=rx_mhz,
                    group=(raw.get("Gruppe") or "").strip(),
                    locator=locator.upper() if locator else "",
                    info=info,
                    status=(raw.get("status") or "").strip(),
                    tone=tone,
                    dmr_id=dmr_id,
                    locator_lat=loc.lat if loc else None,
                    locator_lon=loc.lon if loc else None,
                    locator_precision_m=loc.precision_m if loc else None,
                    locator_bbox=(loc.south, loc.west, loc.north, loc.east) if loc else None,
                )
            )
    rows.sort(key=lambda r: r.callsign)
    return rows


def load_anytone_channels(channel_path: Path, zone_path: Path | None) -> list[CodeplugChannel]:
    zone_for_channel: dict[str, list[str]] = {}
    if zone_path and zone_path.is_file():
        with zone_path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for raw in reader:
                zname = (raw.get("Zone Name") or "").strip()
                members = (raw.get("Zone Channel Member") or "").split("|")
                for member in members:
                    member = member.strip()
                    if not member:
                        continue
                    zone_for_channel.setdefault(member, [])
                    if zname and zname not in zone_for_channel[member]:
                        zone_for_channel[member].append(zname)

    channels: list[CodeplugChannel] = []
    with channel_path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            name = (raw.get("Channel Name") or "").strip()
            if not name:
                continue
            calls = extract_callsigns(name)
            call = calls[0] if calls else ""
            channels.append(
                CodeplugChannel(
                    number=(raw.get("No.") or "").strip(),
                    name=name,
                    callsign=call,
                    rx_mhz=_parse_mhz(raw.get("Receive Frequency")),
                    tx_mhz=_parse_mhz(raw.get("Transmit Frequency")),
                    zones=sorted(zone_for_channel.get(name, [])),
                )
            )
    channels.sort(key=lambda c: (c.callsign or "", c.name))
    return channels


def load_radioid(
    session,
    cache,
    *,
    base_url: str,
    country: str,
    api_token: str,
    per_page: int = 200,
) -> list[SourceRecord]:
    """
    Load Norwegian DMR repeaters from radioid.net.

    The filtered JSON API does not currently return lat/lon. Coordinates come
    from the published map.json dump (same data-use policy as the API), filtered
    to the requested country. The API list is still fetched to discover IDs and
    remain compatible if fields return later.
    """
    cache_key = f"radioid_map_{country}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return [_dict_to_source(r, "radioid") for r in cached]

    headers = {}
    if api_token:
        headers["X-API-Token"] = api_token

    # Optional API pass (cached separately) for talkgroups / future fields.
    api_key = f"radioid_dmr_repeater_{country}"
    api_cached = cache.get_json(api_key)
    if api_cached is None:
        results: list[dict] = []
        page = 1
        pages = 1
        while page <= pages:
            url = f"{base_url.rstrip('/')}/api/dmr/repeater/"
            params = {"country": country, "page": page, "per_page": per_page}
            resp = session.request("GET", url, params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            pages = int(payload.get("pages") or 1)
            batch = payload.get("results") or []
            results.extend(batch)
            if not batch:
                break
            page += 1
        cache.put_json(api_key, results)

    # map.json includes lat/lng used by the RadioID map.
    map_url = f"{base_url.rstrip('/')}/static/map.json"
    resp = session.request("GET", map_url, headers=headers)
    resp.raise_for_status()
    payload = resp.json()
    markers = payload.get("markers") or []
    country_l = country.casefold()
    filtered = [
        m
        for m in markers
        if str(m.get("country") or "").casefold() == country_l
    ]
    cache.put_json(cache_key, filtered)
    return [_dict_to_source(r, "radioid") for r in filtered]


def _dict_to_source(raw: dict, kind: str) -> SourceRecord:
    call_raw = str(raw.get("callsign") or raw.get("Callsign") or "")
    lat = raw.get("latitude", raw.get("Lat", raw.get("lat")))
    lon = raw.get(
        "longitude",
        raw.get("Long", raw.get("lon", raw.get("lng", raw.get("Lng")))),
    )
    try:
        lat_f = float(lat) if lat not in (None, "", "None") else None
    except (TypeError, ValueError):
        lat_f = None
    try:
        lon_f = float(lon) if lon not in (None, "", "None") else None
    except (TypeError, ValueError):
        lon_f = None

    freq = raw.get("frequency") or raw.get("Frequency") or raw.get("freq")
    offset = raw.get("offset") or raw.get("Offset")
    tx = _parse_mhz(str(freq) if freq is not None else None)
    rx = None
    if tx is not None and offset not in (None, ""):
        try:
            rx = tx + float(offset)
        except (TypeError, ValueError):
            rx = None
    # Some APIs provide input_freq separately.
    inp = raw.get("input_freq") or raw.get("Input Freq") or raw.get("input_frequency")
    if inp not in (None, ""):
        rx = _parse_mhz(str(inp))

    dmr_id = str(
        raw.get("id")
        or raw.get("dmr_id")
        or raw.get("DMR ID")
        or raw.get("locator")
        or ""
    )
    # RadioID uses locator as numeric DMR id in some payloads; ignore if huge/floaty.
    if dmr_id.endswith(".0"):
        dmr_id = dmr_id[:-2]
    tone = str(raw.get("pl") or raw.get("tsq") or raw.get("CTCSS") or "")

    return SourceRecord(
        callsign=normalize_callsign(call_raw),
        callsign_raw=call_raw,
        tx_mhz=tx,
        rx_mhz=rx,
        lat=lat_f,
        lon=lon_f,
        tone=tone,
        dmr_id=dmr_id,
        qth=str(raw.get("city") or raw.get("City") or raw.get("landmark") or ""),
        city=str(raw.get("city") or raw.get("City") or ""),
        source_kind=kind,
        source_id=str(raw.get("id") or raw.get("Rptr ID") or raw.get("locator") or ""),
        status=str(raw.get("status") or raw.get("operational_status") or ""),
        extra=raw,
    )


def _rb_results_from_payload(payload) -> list[dict]:
    if isinstance(payload, dict) and payload.get("status") == "error":
        raise RuntimeError(f"RepeaterBook error: {payload.get('message')}")
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(
            f"RepeaterBook auth/error: {payload.get('message') or payload}"
        )
    if isinstance(payload, dict):
        results = payload.get("results")
    else:
        results = payload
    if results is None:
        return []
    if not isinstance(results, list):
        raise RuntimeError("RepeaterBook payload has no results list")
    return results


def load_repeaterbook_json(path: Path) -> list[SourceRecord]:
    """Load a local exportROW / CHIRP RepeaterBook JSON ({count, results, ...})."""
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [_rb_to_source(r) for r in _rb_results_from_payload(payload)]


def load_repeaterbook(
    session,
    cache,
    *,
    base_url: str,
    country: str,
    api_token: str,
    user_agent: str,
    local_json: Path | None = None,
) -> list[SourceRecord]:
    """
    Load Norway (ROW) repeaters from RepeaterBook.

    Prefer the API when ``api_token`` is set. Otherwise use ``local_json``
    (exportROW / CHIRP ``rb-*-all.json`` shape) so Maidenhead centres can still
    be replaced with published lat/lon.
    """
    cache_key = f"repeaterbook_row_{country}"

    if api_token:
        cached = cache.get_json(cache_key)
        if cached is not None:
            return [_rb_to_source(r) for r in cached]

        url = f"{base_url.rstrip('/')}/api/exportROW.php"
        headers = {
            "X-RB-App-Token": api_token,
            "User-Agent": user_agent,
        }
        resp = session.request(
            "GET",
            url,
            params={"country": country},
            headers=headers,
        )
        resp.raise_for_status()
        results = _rb_results_from_payload(resp.json())
        cache.put_json(cache_key, results)
        return [_rb_to_source(r) for r in results]

    if local_json is not None and local_json.is_file():
        return load_repeaterbook_json(local_json)
    return []


def _rb_to_source(raw: dict) -> SourceRecord:
    call_raw = str(raw.get("Callsign") or raw.get("callsign") or "")
    lat = raw.get("Lat") or raw.get("lat") or raw.get("Latitude")
    lon = raw.get("Long") or raw.get("lon") or raw.get("Longitude")
    try:
        lat_f = float(lat) if lat not in (None, "") else None
    except (TypeError, ValueError):
        lat_f = None
    try:
        lon_f = float(lon) if lon not in (None, "") else None
    except (TypeError, ValueError):
        lon_f = None

    freq = raw.get("Frequency") or raw.get("frequency")
    inp = raw.get("Input Freq") or raw.get("input_freq") or raw.get("Input Frequency")
    tone = str(
        raw.get("PL")
        or raw.get("TSQ")
        or raw.get("pl")
        or ""
    )
    dmr_id = str(raw.get("DMR ID") or raw.get("dmr_id") or "")
    return SourceRecord(
        callsign=normalize_callsign(call_raw),
        callsign_raw=call_raw,
        tx_mhz=_parse_mhz(str(freq) if freq is not None else None),
        rx_mhz=_parse_mhz(str(inp) if inp is not None else None),
        lat=lat_f,
        lon=lon_f,
        tone=tone,
        dmr_id=dmr_id,
        qth=str(raw.get("Landmark") or raw.get("Nearest City") or raw.get("city") or ""),
        city=str(raw.get("Nearest City") or raw.get("city") or ""),
        source_kind="repeaterbook",
        source_id=str(raw.get("Rptr ID") or raw.get("id") or ""),
        status=str(raw.get("Operational Status") or raw.get("status") or ""),
        extra=raw,
    )

