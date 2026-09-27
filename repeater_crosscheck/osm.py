from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from .models import NrrlRepeater, Position, SourceRecord
from .util import extract_callsigns, haversine_m, normalize_qth_name, point_in_bbox

# Do not scrape callsigns out of URLs (e.g. source=https://.../la5lrr/).
_URLISH_TAG_KEYS = {
    "source",
    "website",
    "url",
    "image",
    "wikidata",
    "wikipedia",
    "contact:website",
}


def _element_lat_lon(el: dict[str, Any]) -> tuple[float, float] | None:
    if "lat" in el and "lon" in el:
        return float(el["lat"]), float(el["lon"])
    center = el.get("center")
    if center and "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    return None


def _osm_id(el: dict[str, Any]) -> str:
    return f"{el.get('type', 'node')}/{el.get('id')}"


def fetch_osm_amateur_features(session, cache, *, endpoint: str, bbox: list[float], min_interval_s: float) -> list[dict]:
    """Features in Norway bbox with amateur_radio tags or Norwegian callsigns."""
    cache_key = "osm_amateur_norway"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

    south, west, north, east = bbox
    # Tag key containing amateur_radio; name/ref/callsign/description with LA-LN / JW / JX.
    call_re = r"(L[A-N]|JW|JX)[0-9][A-Z]{1,4}"
    query = f"""
[out:json][timeout:180];
(
  nwr[~"amateur_radio"~"."]({south},{west},{north},{east});
  nwr["name"~"{call_re}",i]({south},{west},{north},{east});
  nwr["ref"~"{call_re}",i]({south},{west},{north},{east});
  nwr["callsign"~"{call_re}",i]({south},{west},{north},{east});
  nwr["description"~"{call_re}",i]({south},{west},{north},{east});
  nwr["communication:amateur_radio"="yes"]({south},{west},{north},{east});
);
out center tags;
""".strip()

    resp = session.request(
        "POST",
        endpoint,
        data={"data": query},
        min_interval_s=min_interval_s,
    )
    resp.raise_for_status()
    payload = resp.json()
    elements = payload.get("elements") or []
    cache.put_json(cache_key, elements)
    # Also keep raw text for debugging.
    cache.put_text(cache_key + "_raw", resp.text)
    return elements


def fetch_osm_network_relations(
    session,
    cache,
    *,
    endpoint: str,
    bbox: list[float],
    min_interval_s: float,
) -> list[dict]:
    """Amateur-radio network/site relations plus member geometries."""
    cache_key = "osm_network_relations_norway"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return cached

    south, west, north, east = bbox
    query = f"""
[out:json][timeout:180];
(
  relation["type"="network"][~"^communication:amateur_radio"~"."]({south},{west},{north},{east});
  relation["type"="site"][~"^communication:amateur_radio"~"."]({south},{west},{north},{east});
  relation["type"="network"]["name"~"(L[A-N]|JW|JX)[0-9]",i]({south},{west},{north},{east});
);
out body;
>;
out center tags;
""".strip()
    resp = session.request(
        "POST",
        endpoint,
        data={"data": query},
        min_interval_s=min_interval_s,
    )
    resp.raise_for_status()
    payload = resp.json()
    elements = payload.get("elements") or []
    cache.put_json(cache_key, elements)
    cache.put_text(cache_key + "_raw", resp.text)
    return elements


def relation_members_by_callsign(elements: list[dict]) -> dict[str, list[dict]]:
    """Map callsign -> member elements of network/site relations named after it."""
    by_id: dict[tuple[str, int], dict] = {}
    relations: list[dict] = []
    for el in elements:
        et = el.get("type")
        eid = el.get("id")
        if et and eid is not None:
            by_id[(et, int(eid))] = el
        if et == "relation":
            relations.append(el)

    grouped: dict[str, list[dict]] = {}
    for rel in relations:
        tags = rel.get("tags") or {}
        if tags.get("type") not in {"network", "site"}:
            continue
        calls = extract_callsigns(tags.get("name") or "")
        if not calls:
            blob = " ".join(
                str(tags.get(k) or "")
                for k in ("ref", "callsign", "communication:amateur_radio:callsign")
            )
            calls = extract_callsigns(blob)
        if not calls:
            continue
        members: list[dict] = []
        for mem in rel.get("members") or []:
            key = (str(mem.get("type") or ""), int(mem.get("ref") or 0))
            child = by_id.get(key)
            if not child:
                continue
            if _element_lat_lon(child) is None:
                continue
            extra = dict(child)
            extra["_relation_id"] = rel.get("id")
            extra["_relation_name"] = tags.get("name") or ""
            members.append(extra)
        for call in calls:
            grouped.setdefault(call, []).extend(members)
    return grouped


def relation_member_records(grouped: dict[str, list[dict]]) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for call, members in grouped.items():
        for el in members:
            ll = _element_lat_lon(el)
            if not ll:
                continue
            tags = el.get("tags") or {}
            records.append(
                SourceRecord(
                    callsign=call,
                    callsign_raw=call,
                    lat=ll[0],
                    lon=ll[1],
                    qth=str(tags.get("name") or tags.get("description") or ""),
                    source_kind="osm",
                    source_id=_osm_id(el),
                    extra={
                        "tags": tags,
                        "element": {"type": el.get("type"), "id": el.get("id")},
                        "relation_id": el.get("_relation_id"),
                        "relation_name": el.get("_relation_name") or "",
                        "osm_match": "relation_member",
                    },
                )
            )
    return records


def fetch_osm_landmarks_for_locators(
    session,
    cache,
    *,
    endpoint: str,
    locator_bboxes: dict[str, tuple[float, float, float, float]],
    min_interval_s: float,
    batch_size: int = 40,
) -> dict[str, list[dict]]:
    """
    Fetch mast/tower/peak/hill features for many Maidenhead squares.

    Issues batched Overpass queries (union of locator bboxes) instead of one
    request per square or one giant Norway-wide dump.
    """
    result: dict[str, list[dict]] = {loc: [] for loc in locator_bboxes}
    items = sorted(locator_bboxes.items())
    pad = 0.002

    for start in range(0, len(items), batch_size):
        batch = items[start : start + batch_size]
        batch_key = "osm_landmarks_v2_batch_" + "_".join(loc for loc, _ in batch)
        legacy_key = "osm_landmarks_batch_" + "_".join(loc for loc, _ in batch)
        cached = cache.get_json(batch_key)
        if cached is not None:
            elements = cached
        else:
            parts: list[str] = []
            for _loc, (south, west, north, east) in batch:
                s, w, n, e = south - pad, west - pad, north + pad, east + pad
                parts.append(
                    f'  nwr["man_made"="mast"]({s},{w},{n},{e});\n'
                    f'  nwr["man_made"="tower"]({s},{w},{n},{e});\n'
                    f'  nwr["man_made"="communications_tower"]({s},{w},{n},{e});\n'
                    f'  nwr["natural"="peak"]({s},{w},{n},{e});\n'
                    f'  nwr["natural"="hill"]({s},{w},{n},{e});'
                )
            query = (
                "[out:json][timeout:180];\n(\n"
                + "\n".join(parts)
                + "\n);\nout center tags;"
            )
            try:
                resp = session.request(
                    "POST",
                    endpoint,
                    data={"data": query},
                    min_interval_s=min_interval_s,
                )
                resp.raise_for_status()
                elements = (resp.json() or {}).get("elements") or []
                cache.put_json(batch_key, elements)
            except Exception:
                # Fall back to previous landmark cache (may lack communications_tower).
                legacy = cache.get_json(legacy_key)
                if legacy is None:
                    raise
                elements = legacy
                cache.put_json(batch_key, elements)

        # Assign each element to every locator square that contains it.
        for el in elements:
            ll = _element_lat_lon(el)
            if not ll:
                continue
            lat, lon = ll
            for loc, bbox in batch:
                if point_in_bbox(lat, lon, *bbox, margin_m=250):
                    result[loc].append(el)

    return result


def _callsigns_from_osm_tags(tags: dict[str, Any]) -> list[str]:
    dedicated = (
        tags.get("communication:amateur_radio:callsign")
        or tags.get("callsign")
        or ""
    )
    dedicated_calls = extract_callsigns(str(dedicated))
    if dedicated_calls:
        return dedicated_calls
    parts = [
        str(tags.get(k) or "")
        for k in ("name", "ref", "description", "operator")
        if k not in _URLISH_TAG_KEYS
    ]
    return extract_callsigns(" ".join(parts))


def amateur_elements_to_records(elements: list[dict]) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for el in elements:
        if el.get("type") == "relation":
            tags = el.get("tags") or {}
            # Relation centroids of type=network are not a site position.
            if tags.get("type") in {"network", "site"}:
                continue
        tags = el.get("tags") or {}
        calls = _callsigns_from_osm_tags(tags)
        ll = _element_lat_lon(el)
        if not ll:
            continue
        lat, lon = ll
        if not calls:
            if not any("amateur_radio" in k for k in tags) and tags.get(
                "communication:amateur_radio"
            ) != "yes":
                continue
            calls = [""]
        rel_id = el.get("_relation_id")
        for call in calls:
            records.append(
                SourceRecord(
                    callsign=call,
                    callsign_raw=call,
                    lat=lat,
                    lon=lon,
                    qth=str(tags.get("name") or tags.get("description") or ""),
                    source_kind="osm",
                    source_id=_osm_id(el),
                    extra={
                        "tags": tags,
                        "element": {"type": el.get("type"), "id": el.get("id")},
                        "relation_id": rel_id,
                        "relation_name": el.get("_relation_name") or "",
                    },
                )
            )
    return records


def nearest_landmark(
    lat: float,
    lon: float,
    landmarks: list[dict],
    *,
    max_m: float = 75.0,
    transmitters_only: bool = False,
) -> tuple[Position | None, str]:
    """Pick the closest mast/tower/peak/hill within max_m of a known coordinate."""
    best: tuple[float, float, Position] | None = None  # dist, -pref, pos
    for el in landmarks:
        ll = _element_lat_lon(el)
        if not ll:
            continue
        elat, elon = ll
        dist = haversine_m(lat, lon, elat, elon)
        if dist > max_m:
            continue
        tags = el.get("tags") or {}
        if transmitters_only and not is_transmitter_site(tags):
            continue
        name = tags.get("name") or tags.get("name:no") or ""
        pref = _landmark_site_bonus(tags, name)
        # Strongly prefer transmitters when both are in range.
        if is_transmitter_site(tags):
            pref += 100.0
        elif is_bare_peak(tags):
            pref -= 50.0
        pos = Position(
            lat=elat,
            lon=elon,
            source_kind="osm",
            source_id=_osm_id(el),
            extra={
                "osm_match": "nearest",
                "osm_name": name,
                "distance_m": dist,
                "tags": tags,
            },
        )
        key = (dist, -pref)
        if best is None or key < (best[0], best[1]):
            best = (dist, -pref, pos)
    if best is None:
        return None, ""
    dist, _, pos = best
    label = f"nearest:{pos.extra.get('osm_name') or pos.source_id} ({dist:.0f}m)"
    return pos, label


TRANSMITTER_MAN_MADE = frozenset({"mast", "tower", "communications_tower", "antenna"})


def is_transmitter_site(tags: dict | None) -> bool:
    """True for masts/towers / hovedsender sites (not bare peaks)."""
    tags = tags or {}
    man = (tags.get("man_made") or "").lower()
    if man in TRANSMITTER_MAN_MADE:
        return True
    name = (tags.get("name") or tags.get("name:no") or "").casefold()
    return "hovedsender" in name


def is_bare_peak(tags: dict | None) -> bool:
    """Natural peak/hill with no transmitter tagging — do not merge amateur tags onto these."""
    tags = tags or {}
    natural = (tags.get("natural") or "").lower()
    if natural not in {"peak", "hill", "ridge", "saddle"}:
        return False
    return not is_transmitter_site(tags)


def retarget_peak_to_transmitter(
    pos: Position,
    landmarks: list[dict],
    *,
    max_m: float = 250.0,
) -> Position:
    """
    If pos is a bare peak/hill, retarget to a nearby mast/tower/communications_tower.

    This is the Tron-class fix: QTH 'Tronfjell' must not land on peak 'Tron' when
    'Tron hovedsender' sits beside it.
    """
    tags = pos.extra.get("tags") or {}
    if not is_bare_peak(tags):
        return pos

    best: tuple[tuple[float, float], Position, float] | None = None
    for el in landmarks:
        el_tags = el.get("tags") or {}
        if not is_transmitter_site(el_tags):
            continue
        ll = _element_lat_lon(el)
        if not ll:
            continue
        dist = haversine_m(pos.lat, pos.lon, ll[0], ll[1])
        if dist > max_m:
            continue
        name = el_tags.get("name") or el_tags.get("name:no") or ""
        pref = _landmark_site_bonus(el_tags, name)
        cand = Position(
            lat=ll[0],
            lon=ll[1],
            source_kind="osm",
            source_id=_osm_id(el),
            extra={
                "osm_match": "retarget_peak",
                "osm_name": name,
                "distance_m": dist,
                "retargeted_from": pos.source_id,
                "tags": el_tags,
            },
        )
        key = (dist, -pref)
        if best is None or key < best[0]:
            best = (key, cand, dist)

    if best is None:
        return pos
    _, cand, dist = best
    return cand


def _landmark_site_bonus(tags: dict, name: str) -> float:
    """Prefer communications towers / hovedsender over bare natural peaks."""
    bonus = 0.0
    man = (tags.get("man_made") or "").lower()
    if man in TRANSMITTER_MAN_MADE:
        bonus += 50.0
    if (tags.get("tower:type") or "").lower() == "communication":
        bonus += 20.0
    if tags.get("communication:amateur_radio") or tags.get(
        "communication:amateur_radio:callsign"
    ):
        bonus += 35.0
    lname = (name or "").casefold()
    if "hovedsender" in lname:
        bonus += 45.0
    elif re.search(r"\bsender\b", lname):
        bonus += 25.0
    if is_bare_peak(tags):
        bonus -= 40.0
    return bonus


def _qth_stems(target: str) -> list[str]:
    """QTH plus shorter stems (tronfjell -> tron) for matching 'Tron hovedsender'."""
    stems = [target]
    for suf in (
        "fjellet",
        "fjell",
        "aasen",
        "asen",
        "toppen",
        "berget",
        "kampen",
        "vola",
        "heia",
        "høyen",
        "hoyen",
    ):
        if target.endswith(suf) and len(target) > len(suf) + 2:
            stem = target[: -len(suf)].rstrip()
            if stem and stem not in stems:
                stems.append(stem)
    return stems


def _qth_name_overlap_score(target: str, norm_name: str) -> float:
    """
    Score how specifically an OSM name matches a QTH.

    Substring hits like QTH 'tronfjell' vs peak 'tron' score lower than
    'tron hovedsender' / full equality.
    """
    if not target or not norm_name:
        return 0.0
    if target == norm_name:
        return 100.0
    if target in norm_name:
        # Name contains the full QTH (e.g. 'tronfjell toppen').
        return 90.0 + min(10.0, len(target) / max(len(norm_name), 1) * 10.0)
    # Stem match: QTH Tronfjell vs "Tron hovedsender".
    padded = f" {norm_name} "
    for stem in _qth_stems(target):
        if not stem or stem == target:
            continue
        if padded.startswith(f" {stem} ") or f" {stem} " in padded or norm_name == stem:
            # Prefer names that add site words (hovedsender) over bare peak stem.
            extra = len(norm_name) - len(stem)
            return 70.0 + min(15.0, extra * 2.0)
    if norm_name in target:
        # Shorter OSM name inside QTH (e.g. bare 'tron' peak) — weak.
        return 55.0 + 25.0 * (len(norm_name) / len(target))
    return float(fuzz.ratio(target, norm_name))


def match_qth_landmark(
    repeater: NrrlRepeater,
    landmarks: list[dict],
    *,
    fuzzy_min: int,
) -> tuple[Position | None, str]:
    """Pick best mast/tower/peak/hill whose name matches NRRL QTH inside locator square."""
    if not repeater.qth or not repeater.locator_bbox:
        return None, ""

    target = normalize_qth_name(repeater.qth)
    if not target:
        return None, ""

    south, west, north, east = repeater.locator_bbox
    candidates: list[tuple[float, Position, str]] = []

    for el in landmarks:
        tags = el.get("tags") or {}
        name = tags.get("name") or tags.get("name:no") or ""
        if not name:
            continue
        ll = _element_lat_lon(el)
        if not ll:
            continue
        lat, lon = ll
        if not point_in_bbox(lat, lon, south, west, north, east, margin_m=0):
            continue

        norm = normalize_qth_name(name)
        if not norm:
            continue

        overlap = _qth_name_overlap_score(target, norm)
        if overlap >= 90.0:
            method = "qth_exact"
        elif overlap >= float(fuzzy_min):
            method = "qth_fuzzy"
        else:
            continue

        score = overlap + _landmark_site_bonus(tags, name)
        pos = Position(
            lat=lat,
            lon=lon,
            source_kind="osm",
            source_id=_osm_id(el),
            extra={
                "osm_match": method,
                "osm_name": name,
                "score": score,
                "tags": tags,
            },
        )
        candidates.append((score, pos, method))

    if not candidates:
        return None, ""

    candidates.sort(key=lambda t: (-t[0], t[1].source_id))
    best_score, best_pos, method = candidates[0]
    match_label = f"{method}:{best_pos.extra.get('osm_name')} ({best_pos.source_id})"
    return best_pos, match_label


def record_has_callsign(rec: SourceRecord, callsign: str) -> bool:
    """True when the OSM feature is tagged/named with this callsign."""
    tags = rec.extra.get("tags") or {}
    tagged = extract_callsigns(
        str(tags.get("communication:amateur_radio:callsign") or tags.get("callsign") or "")
    )
    named = extract_callsigns(str(tags.get("name") or ""))
    return callsign in tagged or callsign in named


def source_records_as_landmarks(records: list[SourceRecord]) -> list[dict]:
    """Project amateur OSM SourceRecords into landmark-shaped dicts for QTH matching."""
    out: list[dict] = []
    seen: set[str] = set()
    for rec in records:
        if rec.lat is None or rec.lon is None or not rec.source_id:
            continue
        if rec.source_id in seen:
            continue
        seen.add(rec.source_id)
        parsed = None
        text = str(rec.source_id)
        if "/" in text:
            kind, _, rest = text.partition("/")
            if rest.isdigit():
                parsed = (kind, int(rest))
        if not parsed:
            continue
        kind, eid = parsed
        tags = dict(rec.extra.get("tags") or {})
        if rec.qth and "name" not in tags:
            tags = {**tags, "name": rec.qth}
        out.append(
            {
                "type": kind,
                "id": eid,
                "lat": rec.lat,
                "lon": rec.lon,
                "tags": tags,
            }
        )
    return out


def pick_osm_callsign_match(
    callsign: str,
    osm_records: list[SourceRecord],
    locator_bbox: tuple[float, float, float, float] | None,
    *,
    locator_lat: float | None = None,
    locator_lon: float | None = None,
    near_m: float = 2000,
) -> SourceRecord | None:
    hits = [r for r in osm_records if r.callsign == callsign and r.lat is not None]
    if not hits:
        return None

    def score(r: SourceRecord) -> tuple:
        inside = 0
        if locator_bbox:
            south, west, north, east = locator_bbox
            if point_in_bbox(r.lat, r.lon, south, west, north, east, margin_m=near_m):
                inside = 1
        dist = 1e12
        if locator_lat is not None and locator_lon is not None:
            dist = haversine_m(locator_lat, locator_lon, r.lat, r.lon)
        in_relation = 1 if r.extra.get("relation_id") else 0
        # Prefer features inside the NRRL square, then closer to centre.
        return (-inside, dist, -in_relation, r.source_id)

    hits.sort(key=score)
    dedicated: list[SourceRecord] = []
    for r in hits:
        tags = r.extra.get("tags") or {}
        tagged = extract_callsigns(
            str(tags.get("communication:amateur_radio:callsign") or tags.get("callsign") or "")
        )
        named = extract_callsigns(str(tags.get("name") or ""))
        if callsign in tagged or callsign in named:
            dedicated.append(r)

    if locator_bbox:
        south, west, north, east = locator_bbox
        inside_hits = [
            r
            for r in (dedicated or hits)
            if point_in_bbox(r.lat, r.lon, south, west, north, east, margin_m=near_m)
        ]
        if inside_hits:
            inside_hits.sort(key=score)
            return inside_hits[0]
        # One uniquely tagged mast/node may sit just outside a coarse locator.
        if len(dedicated) == 1:
            return dedicated[0]
        return None

    return (dedicated[0] if dedicated else hits[0])
