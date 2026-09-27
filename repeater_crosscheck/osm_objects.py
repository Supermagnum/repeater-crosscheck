"""Fetch full OSM elements so JOSM can merge review tags onto existing objects."""

from __future__ import annotations

import re
from typing import Any

from .cache import RateLimitedSession, ResponseCache

OSM_API = "https://api.openstreetmap.org/api/0.6"
REF_RE = re.compile(r"^(node|way|relation)/(\d+)$", re.IGNORECASE)


def parse_osm_ref(ref: str | None) -> tuple[str, int] | None:
    if not ref:
        return None
    text = str(ref).strip()
    # Accept full openstreetmap.org URLs.
    text = re.sub(
        r"^https?://(www\.)?openstreetmap\.org/",
        "",
        text,
        flags=re.IGNORECASE,
    )
    m = REF_RE.match(text)
    if not m:
        # Bare numeric id assumed node.
        if text.isdigit():
            return "node", int(text)
        return None
    return m.group(1).lower(), int(m.group(2))


def format_osm_ref(el_type: str, el_id: int) -> str:
    return f"{el_type}/{el_id}"


def collect_osm_refs_from_rows(rows) -> set[str]:
    refs: set[str] = set()
    for row in rows:
        if row.osm_id:
            parsed = parse_osm_ref(row.osm_id)
            if parsed:
                refs.add(format_osm_ref(*parsed))
        for pos in row.positions:
            if pos.source_kind in {"osm", "osm_network"} and pos.source_id:
                parsed = parse_osm_ref(pos.source_id)
                if parsed:
                    refs.add(format_osm_ref(*parsed))
    return refs


def fetch_osm_elements(
    session: RateLimitedSession,
    cache: ResponseCache,
    refs: set[str],
) -> dict[str, dict[str, Any]]:
    """
    Download full OSM objects for the given refs (node/way/relation).

    Ways are fetched with /full so member nodes are included. Results are
    cached per ref. Returns mapping ref -> primary element dict (JSON API shape).
    Side effect: also returns companion nodes for ways under '_members'.
    """
    out: dict[str, dict[str, Any]] = {}
    nodes: list[int] = []
    ways: list[int] = []
    relations: list[int] = []
    for ref in sorted(refs):
        parsed = parse_osm_ref(ref)
        if not parsed:
            continue
        kind, eid = parsed
        cache_key = f"osm_object_{kind}_{eid}"
        cached = cache.get_json(cache_key)
        if cached is not None:
            out[ref] = cached
            continue
        if kind == "node":
            nodes.append(eid)
        elif kind == "way":
            ways.append(eid)
        else:
            relations.append(eid)

    # Batch nodes (API allows comma-separated multiget).
    for i in range(0, len(nodes), 50):
        batch = nodes[i : i + 50]
        url = f"{OSM_API}/nodes.json"
        resp = session.request(
            "GET",
            url,
            params={"nodes": ",".join(str(n) for n in batch)},
        )
        if resp.status_code == 404:
            # Fall back to one-by-one when a batch contains a missing id.
            for nid in batch:
                _fetch_one(session, cache, "node", nid, out)
            continue
        resp.raise_for_status()
        payload = resp.json()
        by_id = {
            int(el["id"]): el
            for el in (payload.get("elements") or [])
            if el.get("type") == "node"
        }
        for nid in batch:
            el = by_id.get(nid)
            if not el:
                _fetch_one(session, cache, "node", nid, out)
                continue
            ref = format_osm_ref("node", nid)
            cache.put_json(f"osm_object_node_{nid}", el)
            out[ref] = el

    for wid in ways:
        _fetch_way_full(session, cache, wid, out)

    for rid in relations:
        _fetch_one(session, cache, "relation", rid, out)

    return out


def _fetch_one(
    session: RateLimitedSession,
    cache: ResponseCache,
    kind: str,
    eid: int,
    out: dict[str, dict[str, Any]],
) -> None:
    url = f"{OSM_API}/{kind}/{eid}.json"
    resp = session.request("GET", url)
    if resp.status_code == 404:
        return
    resp.raise_for_status()
    elements = (resp.json() or {}).get("elements") or []
    if not elements:
        return
    el = elements[0]
    ref = format_osm_ref(kind, eid)
    cache.put_json(f"osm_object_{kind}_{eid}", el)
    out[ref] = el


def _fetch_way_full(
    session: RateLimitedSession,
    cache: ResponseCache,
    wid: int,
    out: dict[str, dict[str, Any]],
) -> None:
    cache_key = f"osm_object_way_{wid}"
    cached = cache.get_json(cache_key)
    if cached is not None:
        out[format_osm_ref("way", wid)] = cached
        return
    url = f"{OSM_API}/way/{wid}/full.json"
    resp = session.request("GET", url)
    if resp.status_code == 404:
        return
    resp.raise_for_status()
    elements = (resp.json() or {}).get("elements") or []
    way = next((e for e in elements if e.get("type") == "way" and int(e["id"]) == wid), None)
    if not way:
        return
    members = [e for e in elements if e.get("type") == "node"]
    packed = dict(way)
    packed["_members"] = members
    cache.put_json(cache_key, packed)
    out[format_osm_ref("way", wid)] = packed


def merge_tag_values(existing: str | None, new: str | None, *, sep: str = ";") -> str:
    """Union semicolon-separated tag values, stable order."""
    parts: list[str] = []
    for blob in (existing, new):
        if not blob:
            continue
        for part in str(blob).split(sep):
            part = part.strip()
            if part and part not in parts:
                parts.append(part)
    return sep.join(parts)


def merged_osm_tags(
    existing_tags: dict[str, str],
    review_tags: dict[str, str],
) -> dict[str, str]:
    """
    Merge review/amateur-radio tags into existing OSM tags.

    Existing non-conflicting tags are kept. For callsign and modulation,
    values are unioned. Review-only keys (source_kind, review, flags, …)
    are always set from the review payload.
    """
    out = {str(k): str(v) for k, v in existing_tags.items() if v is not None}
    union_keys = {
        "communication:amateur_radio:callsign",
        "communication:amateur_radio:repeater:modulation",
        "ref",
        "callsign",
        "frequency",
        "dmr_id",
        "flags",
        "note",
    }
    for key, value in review_tags.items():
        if not value:
            continue
        if key in union_keys and key in out:
            out[key] = merge_tag_values(out[key], value)
        elif key.startswith("communication:amateur_radio") or key in {
            "source_kind",
            "review",
            "flags",
            "note",
            "callsign",
            "frequency",
            "qth",
            "locator",
            "group",
            "nrrl:group_page",
            "website",
            "best_source",
            "fixme",
            "dmr_id",
            "network",
            "osm_relation",
        }:
            # Prefer new amateur-radio / review tags for this export.
            if key in out and key.startswith("communication:amateur_radio:repeater:"):
                # Keep existing frequency/ctcss if already present.
                if key.endswith(("frequency_out", "shift", "ctcss", "toneburst", "dcs")):
                    if out[key]:
                        continue
            # Site identity: keep the first QTH/locator/group already on the object.
            if key in {"qth", "locator", "group"} and out.get(key):
                continue
            out[key] = value
        elif key not in out:
            out[key] = value
    # Ensure repeater flag when we attach amateur modulation/callsign.
    if (
        out.get("communication:amateur_radio:callsign")
        or out.get("communication:amateur_radio:repeater:modulation")
    ):
        out.setdefault("communication:amateur_radio", "yes")
        out.setdefault("communication:amateur_radio:repeater", "yes")
    return out
