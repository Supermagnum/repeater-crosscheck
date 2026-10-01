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


# One slot per callsign/band (callsign order, then frequency ascending).
# Inner multi-values use "," so ";" remains the slot delimiter.
PER_CALLSIGN_TAG_KEYS: tuple[str, ...] = (
    "frequency",
    "communication:amateur_radio:repeater:frequency_out",
    "communication:amateur_radio:repeater:shift",
    "communication:amateur_radio:repeater:ctcss",
    "communication:amateur_radio:repeater:dcs",
    "communication:amateur_radio:repeater:toneburst",
    "communication:amateur_radio:repeater:modulation",
    "dmr_id",
    "flags",
    "qth",
    "locator",
)

# Club/site URLs: unique non-empty values only (no empty slots, no duplicates).
SITE_UNIQUE_TAG_KEYS: tuple[str, ...] = (
    "group",
    "nrrl:group_page",
    "website",
)

# Also positional on multi-member nodes (collapse only when every slot matches).
PER_MEMBER_META_KEYS: tuple[str, ...] = (
    "source_kind",
    "best_source",
    "note",
)

_PER_CALLSIGN_KEY_SET = set(PER_CALLSIGN_TAG_KEYS) | set(SITE_UNIQUE_TAG_KEYS) | {
    "callsign",
    "communication:amateur_radio:callsign",
}


def _slot_value(raw: str | None) -> str:
    """Normalize a single slot value; use comma for within-slot multi-values."""
    if not raw:
        return ""
    parts = [p.strip() for p in str(raw).replace(",", ";").split(";") if p.strip()]
    return ",".join(parts)


def _primary_callsign(tags: dict[str, str]) -> str:
    raw = tags.get("callsign") or tags.get("communication:amateur_radio:callsign") or ""
    return raw.split(";")[0].strip().upper()


def _freq_sort_key(tags: dict[str, str]) -> float:
    raw = tags.get("frequency") or tags.get(
        "communication:amateur_radio:repeater:frequency_out"
    ) or ""
    m = re.search(r"[-+]?\d+(?:\.\d+)?", str(raw))
    if not m:
        return 1e99
    try:
        return float(m.group(0))
    except ValueError:
        return 1e99


def _member_note_slot(tags: dict[str, str]) -> str:
    """Keep per-member review notes; avoid ';' inside a slot (slot delimiter)."""
    text = (tags.get("note") or "").strip()
    # _review_tags joins parts with "; " — normalise to ". " for positional notes.
    text = re.sub(r"\s*;\s*", ". ", text).strip()
    text = re.sub(r"\.\s*\.", ".", text)
    return text.strip()


def _member_best_source_slot(tags: dict[str, str]) -> str:
    """
    Locator-end disagreement nodes use source_kind=nrrl_locator and no best_source
    (the radioid/other end holds the best position).
    """
    flags = tags.get("flags") or ""
    if tags.get("source_kind") == "nrrl_locator" and "disagreement" in flags:
        return ""
    return _slot_value(tags.get("best_source"))


def _collapse_or_join(slots: list[str]) -> str | None:
    """Join positional slots; drop empties. One distinct value collapses to itself."""
    if not any(slots):
        return None
    nonempty = [s for s in slots if s]
    if not nonempty:
        return None
    if len(set(nonempty)) == 1:
        return nonempty[0]
    return ";".join(nonempty)


def _unique_nonempty(slots: list[str]) -> str | None:
    """Stable unique join; drop empties and duplicates (for website/group_page)."""
    parts: list[str] = []
    for slot in slots:
        for part in str(slot or "").split(";"):
            part = part.strip()
            if part and part not in parts:
                parts.append(part)
    return ";".join(parts) if parts else None


# Review-only keys must not be left on existing OSM objects destined for upload.
REVIEW_ONLY_TAG_KEYS: frozenset[str] = frozenset(
    {
        "review",
        "best_source",
        "source_kind",
        "frequency",
        "locator",
        "qth",
        "flags",
        "fixme",
    }
)

# Semicolon-slot keys cleaned on existing OSM objects (drop empties; collapse equals).
_OSM_SLOT_CLEAN_KEYS: frozenset[str] = frozenset(
    {
        "communication:amateur_radio:repeater:ctcss",
        "communication:amateur_radio:repeater:shift",
        "communication:amateur_radio:repeater:dcs",
        "communication:amateur_radio:repeater:toneburst",
        "communication:amateur_radio:repeater:modulation",
        "communication:amateur_radio:repeater:frequency_out",
        "dmr_id",
    }
)


def sanitize_existing_osm_tags(tags: dict[str, str]) -> dict[str, str]:
    """
    Prepare tags for an existing OSM node/way: drop review-only keys and clean
    empty multi-callsign slots (matching manual JOSM upload cleanup).
    """
    out: dict[str, str] = {}
    for key, val in tags.items():
        if key in REVIEW_ONLY_TAG_KEYS:
            continue
        if not val:
            continue
        if key in _OSM_SLOT_CLEAN_KEYS:
            parts = [p.strip() for p in str(val).split(";") if p.strip()]
            if not parts:
                continue
            val = parts[0] if len(set(parts)) == 1 else ";".join(parts)
        out[key] = val
    return out


def align_multi_callsign_tags(members: list[dict[str, str]]) -> dict[str, str]:
    """
    Build tags for a multi-callsign / multi-band review node.

    One semicolon slot per member, ordered by callsign then frequency ascending.
    Multi-band callsigns repeat the callsign once per band. Missing values are
    empty slots. Within a slot, multiple values use commas. Identical values
    across every slot collapse to a single value.

    group / nrrl:group_page / website are unique non-empty unions (no leading
    ';' or duplicated club URLs).
    """
    cleaned: list[dict[str, str]] = []
    for tags in members:
        cs = _primary_callsign(tags)
        if not cs:
            continue
        cleaned.append(dict(tags))
    cleaned.sort(key=lambda t: (_primary_callsign(t), _freq_sort_key(t), t.get("flags") or ""))
    if not cleaned:
        return {}

    callsigns = [_primary_callsign(t) for t in cleaned]
    joined = ";".join(callsigns)
    out: dict[str, str] = {
        "callsign": joined,
        "communication:amateur_radio:callsign": joined,
        "name": joined,
        "communication:amateur_radio": "yes",
        "communication:amateur_radio:repeater": "yes",
        "review": "repeater_crosscheck",
    }
    for key in PER_CALLSIGN_TAG_KEYS:
        slots = [_slot_value(t.get(key)) for t in cleaned]
        joined_val = _collapse_or_join(slots)
        if joined_val is not None:
            out[key] = joined_val

    for key in SITE_UNIQUE_TAG_KEYS:
        slots = [_slot_value(t.get(key)) for t in cleaned]
        joined_val = _unique_nonempty(slots)
        if joined_val is not None:
            out[key] = joined_val

    sk_slots = [_slot_value(t.get("source_kind")) for t in cleaned]
    sk_joined = _collapse_or_join(sk_slots)
    if sk_joined is not None:
        out["source_kind"] = sk_joined

    bs_slots = [_member_best_source_slot(t) for t in cleaned]
    bs_joined = _collapse_or_join(bs_slots)
    if bs_joined is not None:
        out["best_source"] = bs_joined

    note_slots = [_member_note_slot(t) for t in cleaned]
    # Prefix with callsign when notes differ so QRT/override belong to a member.
    if any(note_slots):
        if len(set(note_slots)) == 1 and all(note_slots):
            out["note"] = note_slots[0]
        else:
            parts: list[str] = []
            for cs, note in zip(callsigns, note_slots):
                if note:
                    parts.append(f"{cs}: {note}")
                else:
                    parts.append(f"{cs}:")
            out["note"] = "; ".join(parts)

    for key in ("fixme",):
        for t in cleaned:
            if t.get(key):
                out[key] = t[key]
                break
    return out


def strip_per_callsign_tags(tags: dict[str, str]) -> dict[str, str]:
    """Keep site/infrastructure tags; drop amateur per-repeater / callsign keys."""
    # communications_transponder:tone is not used for CTCSS — drop it so review
    # exports use communication:amateur_radio:repeater:ctcss only.
    drop = _PER_CALLSIGN_KEY_SET | {
        "communications_transponder:tone",
        "communication:amateur_radio",
        "communication:amateur_radio:repeater",
        "callsign",
    }
    return {k: v for k, v in tags.items() if k not in drop and not str(k).startswith(
        "communication:amateur_radio:"
    )}



_NOTE_META_PREFIXES = (
    "operator override position",
    "best position from ",
    "position from ",
    "nrrl locator ",
    "osm network member",
)


def _normalize_note_part(part: str) -> str:
    """Collapse sibling-list / osm_id boilerplate so near-duplicate notes match."""
    text = part.strip()
    text = re.sub(
        r"\s*\((?:with\s+)?[A-Z]{1,2}\d[A-Z0-9]{0,4}"
        r"(?:\s*[/;,]\s*[A-Z]{1,2}\d[A-Z0-9]{0,4})*\)\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*\bOSM\s+(?:node|way|relation)/\d+\b\.?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"https?://(?:www\.)?openstreetmap\.org/(?:node|way|relation)/\d+",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*\.?\s*\bQRT\b\.?\s*$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" .;")
    return text.casefold()


def merge_osm_notes(existing: str | None, new: str | None) -> str:
    """
    Merge OSM note tags without stacking per-callsign override boilerplate.

    Drops review-process chatter and near-duplicates that only differ by
    sibling callsign lists, QRT suffixes, or repeated osm_id mentions.
    """
    parts: list[str] = []
    seen: set[str] = set()
    had_qrt = False
    for blob in (existing, new):
        if not blob:
            continue
        for part in str(blob).split(";"):
            part = part.strip()
            if not part:
                continue
            low = part.casefold()
            if any(low == p or low.startswith(p) for p in _NOTE_META_PREFIXES):
                continue
            if re.search(r"\bQRT\b", part, flags=re.IGNORECASE):
                had_qrt = True
            norm = _normalize_note_part(part)
            if not norm or norm in seen:
                continue
            seen.add(norm)
            # Store without trailing QRT; re-append once at the end if needed.
            clean = re.sub(r"\s*\.?\s*\bQRT\b\.?\s*$", "", part, flags=re.IGNORECASE)
            clean = clean.strip(" .;") or part
            parts.append(clean)
    # Drop shorter parts that are prefixes of a longer part (same site).
    kept: list[str] = []
    norms = [_normalize_note_part(p) for p in parts]
    for i, part in enumerate(parts):
        if any(
            i != j and norms[i] != norms[j] and norms[j].startswith(norms[i])
            for j in range(len(parts))
        ):
            continue
        kept.append(part)
    parts = kept
    if had_qrt and parts:
        parts[-1] = parts[-1].rstrip(".") + ". QRT."
    return "; ".join(parts)


def merged_osm_tags(
    existing_tags: dict[str, str],
    review_tags: dict[str, str],
    *,
    union_site_keys: bool = False,
) -> dict[str, str]:
    """
    Merge review/amateur-radio tags into existing OSM tags.

    Existing non-conflicting tags are kept. For callsign and modulation,
    values are unioned. Review-only keys (source_kind, review, flags, …)
    are always set from the review payload.

    union_site_keys: also union qth/locator/group (stacked synthetic review nodes).
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
    }
    if union_site_keys:
        union_keys.update({"qth", "locator", "group", "name"})
    for key, value in review_tags.items():
        if not value:
            continue
        if key == "note":
            out[key] = merge_osm_notes(out.get(key), value)
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
            # Prefer review amateur-radio tags over stale OSM values in this export.
            # Site identity: keep the first QTH/locator/group already on the object.
            if key in {"qth", "locator", "group"} and out.get(key) and not union_site_keys:
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
