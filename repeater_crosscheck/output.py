from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from . import __version__
from .cache import RateLimitedSession, ResponseCache
from .models import MergedRepeater, Position
from .modulation import osm_amateur_tags
from .osm import is_bare_peak
from .osm_objects import (
    align_multi_callsign_tags,
    collect_osm_refs_from_rows,
    fetch_osm_elements,
    format_osm_ref,
    parse_osm_ref,
    sanitize_existing_osm_tags,
    strip_per_callsign_tags,
)
from .util import fmt_coord, haversine_m


CSV_FIELDS = [
    "callsign",
    "type",
    "qth",
    "tx",
    "rx",
    "tone",
    "dmr_id",
    "group",
    "status",
    "locator",
    "locator_lat",
    "locator_lon",
    "locator_precision_m",
    "osm_lat",
    "osm_lon",
    "osm_id",
    "osm_match",
    "radioid_lat",
    "radioid_lon",
    "repeaterbook_lat",
    "repeaterbook_lon",
    "local_lat",
    "local_lon",
    "best_lat",
    "best_lon",
    "best_source",
    "elevation_m",
    "max_disagreement_m",
    "flags",
    "codeplug_channels",
    "codeplug_zones",
    "group_nrrl_url",
    "group_website",
    "notes",
]


def _coord_cell(value: float | None) -> str:
    return fmt_coord(value)


def _precision_cell(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.1f}"


def write_merged_csv(path: Path, rows: list[MergedRepeater]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "callsign": row.callsign,
                    "type": row.type,
                    "qth": row.qth,
                    "tx": row.tx,
                    "rx": row.rx,
                    "tone": row.tone,
                    "dmr_id": row.dmr_id,
                    "group": row.group,
                    "status": row.status,
                    "locator": row.locator,
                    "locator_lat": _coord_cell(row.locator_lat),
                    "locator_lon": _coord_cell(row.locator_lon),
                    "locator_precision_m": _precision_cell(row.locator_precision_m),
                    "osm_lat": _coord_cell(row.osm_lat),
                    "osm_lon": _coord_cell(row.osm_lon),
                    "osm_id": row.osm_id,
                    "osm_match": row.osm_match,
                    "radioid_lat": _coord_cell(row.radioid_lat),
                    "radioid_lon": _coord_cell(row.radioid_lon),
                    "repeaterbook_lat": _coord_cell(row.repeaterbook_lat),
                    "repeaterbook_lon": _coord_cell(row.repeaterbook_lon),
                    "local_lat": _coord_cell(row.local_lat),
                    "local_lon": _coord_cell(row.local_lon),
                    "best_lat": _coord_cell(row.best_lat),
                    "best_lon": _coord_cell(row.best_lon),
                    "best_source": row.best_source,
                    "elevation_m": (
                        f"{row.elevation_m:.1f}"
                        if row.elevation_m is not None
                        else ""
                    ),
                    "max_disagreement_m": (
                        f"{row.max_disagreement_m:.1f}"
                        if row.max_disagreement_m is not None
                        else ""
                    ),
                    "flags": "|".join(row.flags),
                    "codeplug_channels": "|".join(row.codeplug_channels),
                    "codeplug_zones": "|".join(row.codeplug_zones),
                    "group_nrrl_url": row.group_nrrl_url,
                    "group_website": row.group_website,
                    "notes": row.notes,
                }
            )


def write_unmatched(path: Path, notes: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(notes) + ("\n" if notes else "")
    path.write_text(text, encoding="utf-8")


def _member_callsigns(tags: dict[str, str]) -> set[str]:
    raw = tags.get("communication:amateur_radio:callsign") or tags.get("callsign") or ""
    return {p.strip().upper() for p in str(raw).split(";") if p.strip()}


def _network_merge_tags(
    row: MergedRepeater,
    pos: Position,
    existing_tags: dict[str, str],
) -> dict[str, str]:
    """
    Tags to merge onto an LA5MR-style network member.

    If the member already carries a different amateur callsign (e.g. Tron
    hovedsender = LA9AR), only attach network membership markers — never stamp
    the network callsign / QTH / frequency onto that site.
    """
    rel = pos.extra.get("relation_id")
    rel_name = pos.extra.get("relation_name") or row.callsign
    member_cs = _member_callsigns(existing_tags)
    network_cs = (row.callsign or "").upper()
    foreign = bool(member_cs) and network_cs not in member_cs
    member_primary = next(iter(sorted(member_cs)), "")

    if foreign:
        # Preserve the site's own amateur identity; only mark network membership.
        tags = {
            k: v
            for k, v in existing_tags.items()
            if k == "callsign"
            or k.startswith("communication:amateur_radio")
            or k in {"frequency", "dmr_id"}
        }
        tags.update(
            {
                "source_kind": "osm_network",
                "review": "repeater_crosscheck",
                "network": str(rel_name),
            }
        )
        if member_primary:
            tags["name"] = member_primary
            tags.setdefault("callsign", member_primary)
            tags.setdefault(
                "communication:amateur_radio:callsign", member_primary
            )
        if rel:
            tags["osm_relation"] = f"relation/{rel}"
        return {k: v for k, v in tags.items() if v}

    review = _review_tags(row, kind="osm_network", pos=pos)
    # Relation members: name follows the callsign on the object.
    review["name"] = row.callsign
    return review


def _review_tags(row: MergedRepeater, *, kind: str, pos: Position | None = None) -> dict[str, str]:
    note_parts: list[str] = []
    if row.notes:
        note_parts.append(row.notes)
    if kind == "best":
        src = row.best_source or "unknown"
        note_parts.append(f"best position from {src}")
    elif kind == "local":
        note_parts.append("operator override position")
    elif kind == "nrrl_locator":
        loc = row.locator or (pos.source_id if pos else "")
        note_parts.append(f"NRRL locator {loc}".strip())
    elif kind == "osm_network":
        site = ""
        if pos is not None:
            site = str(pos.extra.get("osm_name") or pos.source_id or "")
        note_parts.append(
            f"OSM network member{f' ({site})' if site else ''}".strip()
        )
    elif kind in {"radioid", "repeaterbook", "osm"}:
        note_parts.append(f"position from {kind}")

    tags = {
        "source_kind": kind,
        "callsign": row.callsign,
        "name": row.callsign,
        "frequency": row.tx,
        "flags": "|".join(row.flags),
        "qth": row.qth,
        "locator": row.locator,
        "group": row.group,
        "nrrl:group_page": row.group_nrrl_url,
        "website": row.group_website,
        "review": "repeater_crosscheck",
        "note": "; ".join(p for p in note_parts if p),
    }
    if kind == "local":
        tags["fixme"] = "operator_override"
        tags["best_source"] = row.best_source or "local"
    if kind == "best":
        tags["best_source"] = row.best_source
    if kind == "osm_network" and pos is not None:
        rel = pos.extra.get("relation_id")
        if rel:
            tags["osm_relation"] = f"relation/{rel}"
        rel_name = pos.extra.get("relation_name")
        if rel_name:
            tags["network"] = str(rel_name)
    if row.dmr_id:
        tags["dmr_id"] = row.dmr_id
    tags.update(osm_amateur_tags(row))
    return {k: v for k, v in tags.items() if v}


def _element_attrs(el: dict) -> dict[str, str]:
    attrs = {
        "id": str(el["id"]),
        "version": str(el.get("version") or "1"),
        "visible": "true",
        "action": "modify",
    }
    for key in ("changeset", "timestamp", "user", "uid"):
        if el.get(key) is not None:
            attrs[key] = str(el[key])
    if el.get("type") == "node" or ("lat" in el and "lon" in el):
        if el.get("lat") is not None and el.get("lon") is not None:
            attrs["lat"] = f"{float(el['lat']):.7f}"
            attrs["lon"] = f"{float(el['lon']):.7f}"
    return attrs


def _append_tags(elem: ET.Element, tags: dict[str, str]) -> None:
    for k in sorted(tags):
        v = tags[k]
        if v:
            ET.SubElement(elem, "tag", {"k": k, "v": str(v)})


def _coincident(pos: Position, lat: float, lon: float, *, max_m: float = 35.0) -> bool:
    return haversine_m(pos.lat, pos.lon, lat, lon) <= max_m


_SYNTHETIC_KIND_RANK = {
    "local": 0,
    "osm": 1,
    "radioid": 2,
    "repeaterbook": 3,
    "best": 4,
    "nrrl_locator": 5,
}


def _elem_tags(el: ET.Element) -> dict[str, str]:
    return {t.get("k", ""): t.get("v", "") for t in el.findall("tag") if t.get("k")}


def _set_elem_tags(el: ET.Element, tags: dict[str, str]) -> None:
    for child in list(el.findall("tag")):
        el.remove(child)
    _append_tags(el, tags)


def _set_tag(el: ET.Element, key: str, value: str) -> None:
    for t in el.findall("tag"):
        if t.get("k") == key:
            t.set("v", value)
            return
    ET.SubElement(el, "tag", {"k": key, "v": value})


def _apply_callsign_as_name(root: ET.Element) -> None:
    """Set name=<callsign> on every review feature that has a single callsign."""
    for el in list(root.findall("node")) + list(root.findall("way")):
        tags = _elem_tags(el)
        raw = (
            tags.get("callsign")
            or tags.get("communication:amateur_radio:callsign")
            or ""
        ).strip()
        if not raw or ";" in raw:
            continue
        if tags.get("name") == raw:
            continue
        _set_tag(el, "name", raw)


def _merge_coincident_synthetics(root: ET.Element, *, max_m: float = 10.0) -> None:
    """
    Collapse duplicate synthetic nodes for the *same* callsign and band only.

    Co-located different callsigns (or bands) stay as separate nodes — matching
    common OSM practice outside Norway (one feature per repeater/band even on a
    shared mast).
    """
    synthetics = [
        el
        for el in list(root)
        if el.tag == "node"
        and el.get("id")
        and int(el.get("id")) < 0
        and el.get("lat") is not None
        and el.get("lon") is not None
        and _elem_tags(el).get("callsign")
    ]
    n = len(synthetics)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    def _identity(tags: dict[str, str]) -> tuple[str, str]:
        cs = (tags.get("callsign") or "").split(";")[0].strip().upper()
        band = (
            tags.get("frequency")
            or tags.get("communication:amateur_radio:repeater:frequency_out")
            or ""
        ).strip()
        return cs, band

    for i in range(n):
        id_i = _identity(_elem_tags(synthetics[i]))
        if not id_i[0]:
            continue
        lat_i = float(synthetics[i].get("lat"))
        lon_i = float(synthetics[i].get("lon"))
        for j in range(i + 1, n):
            if _identity(_elem_tags(synthetics[j])) != id_i:
                continue
            d = haversine_m(
                lat_i,
                lon_i,
                float(synthetics[j].get("lat")),
                float(synthetics[j].get("lon")),
            )
            if d <= max_m:
                union(i, j)

    clusters: dict[int, list[ET.Element]] = defaultdict(list)
    for i, el in enumerate(synthetics):
        clusters[find(i)].append(el)

    id_map: dict[str, str] = {}
    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda e: (
                _SYNTHETIC_KIND_RANK.get(_elem_tags(e).get("source_kind", ""), 9),
                int(e.get("id")),
            )
        )
        survivor = members[0]
        # Same callsign+band duplicates: keep the preferred kind's tags.
        tags = _elem_tags(survivor)
        for other in members[1:]:
            id_map[other.get("id", "")] = survivor.get("id", "")
            root.remove(other)
        _set_elem_tags(survivor, tags)

    if not id_map:
        return
    for way in list(root):
        if way.tag != "way":
            continue
        refs: list[str] = []
        for nd in way.findall("nd"):
            ref = id_map.get(nd.get("ref", ""), nd.get("ref", ""))
            nd.set("ref", ref)
            refs.append(ref)
        unique = {r for r in refs if r}
        if len(unique) < 2:
            root.remove(way)


def _osm_mast_merge_priority(row: MergedRepeater) -> tuple:
    """Prefer voice repeaters over APRS/packet digis when claiming a shared mast."""
    blob = (row.type or "").upper()
    digi = any(x in blob for x in ("APRS", "PACKET", "WINLINK", "BPQ", "LORA"))
    return (1 if digi else 0, (row.callsign or "").upper(), (row.tx or "").strip())


def _primary_osm_ref(row: MergedRepeater) -> str | None:
    """OSM object for this callsign's own site (not LA5MR-style network members)."""
    parsed = parse_osm_ref(row.osm_id) if row.osm_id else None
    if parsed:
        return format_osm_ref(*parsed)
    for pos in row.positions:
        if pos.source_kind == "osm" and pos.source_id:
            parsed = parse_osm_ref(pos.source_id)
            if parsed:
                return format_osm_ref(*parsed)
    return None


def _mergeable_osm_element(el: dict | None) -> bool:
    """Do not merge review tags onto bare natural peaks (Tron-class bug)."""
    if not el:
        return False
    return not is_bare_peak(el.get("tags") or {})


def _may_merge_review_onto_osm(
    row: MergedRepeater,
    el: dict,
    *,
    pending_tags: dict[str, str] | None,
) -> bool:
    """
    Only rewrite a real OSM object when this callsign already belongs there
    (or an override / nearest / callsign match pinned it).

    QTH name hits on commercial towers (e.g. Gausta hovedsender) must not
    invent ``callsign=LD3DG;LD3GT`` on an untagged mast — emit synthetics.
    Never attach a second NRRL callsign onto an object already claimed by another.
    """
    cs = (row.callsign or "").upper()
    if not cs:
        return False
    tags = dict(el.get("tags") or {})
    if pending_tags:
        tags = {**tags, **pending_tags}
    match = row.osm_match or ""
    method = (row.match_methods or {}).get("osm") or ""
    # Explicit osm_id override may share a mast with another callsign.
    if match.startswith("override:") or method == "override":
        return True

    on_object = _member_callsigns(tags)
    if cs in on_object:
        return True
    if on_object and cs not in on_object:
        return False
    if method in {"callsign", "relation_member"}:
        return True
    if match.startswith("callsign:") or match.startswith("relation_member:"):
        return True
    if method in {"nearest", "retarget_peak"}:
        return True
    if match.startswith("nearest") or "retarget_peak" in match:
        return True
    # qth_exact / qth_fuzzy alone on an untagged object: coords only, no merge.
    return False


def _element_center(el: dict) -> tuple[float, float] | None:
    if el.get("lat") is not None and el.get("lon") is not None:
        return float(el["lat"]), float(el["lon"])
    if el.get("center"):
        return float(el["center"]["lat"]), float(el["center"]["lon"])
    members = el.get("_members") or []
    coords = [
        (float(m["lat"]), float(m["lon"]))
        for m in members
        if m.get("lat") is not None and m.get("lon") is not None
    ]
    if not coords:
        return None
    return (
        sum(c[0] for c in coords) / len(coords),
        sum(c[1] for c in coords) / len(coords),
    )


def write_josm_osm(
    path: Path,
    rows: list[MergedRepeater],
    *,
    disagreement_m: float,
    session: RateLimitedSession | None = None,
    cache: ResponseCache | None = None,
    scrub_osm: list[str] | None = None,
) -> None:
    """
    Write a JOSM review file.

    Sources are public; verify tags and local knowledge before uploading to OSM.

    When an OSM mast/tower/node/way is known, download that object and merge
    amateur-radio / review tags onto it instead of inventing a duplicate node.

    scrub_osm: existing OSM refs to emit with amateur/callsign tags removed
    (infrastructure only), e.g. after detaching a repeater onto a synthetic node.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element(
        "osm",
        {
            "version": "0.6",
            "generator": f"repeater-crosscheck/{__version__}",
        },
    )

    scrub_refs: set[str] = set()
    for raw in scrub_osm or []:
        parsed = parse_osm_ref(raw)
        if parsed:
            scrub_refs.add(format_osm_ref(*parsed))

    # Innlandsnettet / LA5MR network — members should use callsign as name.
    network_relation_refs = {"relation/18780801"}

    osm_elements: dict[str, dict] = {}
    if session is not None and cache is not None:
        refs = collect_osm_refs_from_rows(rows) | scrub_refs | network_relation_refs
        if refs:
            print(f"Fetching {len(refs)} existing OSM objects to merge tags...")
            osm_elements = fetch_osm_elements(session, cache, refs)
            print(f"  loaded {len(osm_elements)} objects")
            # Pull relation member geometries into the fetch set (second pass).
            extra: set[str] = set()
            for rref in network_relation_refs:
                rel = osm_elements.get(rref)
                if not rel:
                    continue
                for mem in rel.get("members") or []:
                    mtype = str(mem.get("type") or "")
                    mid = mem.get("ref")
                    if mtype and mid is not None:
                        extra.add(format_osm_ref(mtype, int(mid)))
            missing = extra - set(osm_elements)
            if missing:
                print(f"Fetching {len(missing)} LA5MR relation member objects...")
                more = fetch_osm_elements(session, cache, missing)
                osm_elements.update(more)
                print(f"  loaded {len(more)} members")

    # Seed scrubbed infrastructure objects (no amateur merge members).
    pending_osm_base: dict[str, dict[str, str]] = {}
    pending_osm_raw: dict[str, dict[str, str]] = {}
    for ref in scrub_refs:
        el = osm_elements.get(ref)
        if not el:
            continue
        raw = {str(k): str(v) for k, v in dict(el.get("tags") or {}).items()}
        pending_osm_raw[ref] = raw
        base = strip_per_callsign_tags(raw)
        note = base.get("note") or ""
        # Drop notes that still list detached callsigns / osm_id boilerplate.
        if re.search(r"\b(?:L[A-N]|JW|JX)\d", note, re.I) or re.search(
            r"\bOSM\s+(?:node|way|relation)/", note, re.I
        ):
            base.pop("note", None)
        pending_osm_base[ref] = base

    # osm_id overrides without local coords: snap CSV best_* onto the fetched object
    # so CSV and review geometry agree (e.g. LA9AR on Tron hovedsender way).
    for row in rows:
        if "portable" in row.flags:
            continue
        if not (row.osm_match or "").startswith("override:"):
            continue
        if row.best_source == "local" and row.local_lat is not None:
            continue
        ref = _primary_osm_ref(row)
        el = osm_elements.get(ref) if ref else None
        center = _element_center(el) if el else None
        if not center:
            continue
        clat, clon = center
        if row.best_lat is not None and row.best_lon is not None:
            if haversine_m(row.best_lat, row.best_lon, clat, clon) <= 75.0:
                continue
        row.best_lat, row.best_lon = clat, clon
        row.best_source = "osm"
        row.osm_lat, row.osm_lon = clat, clon

    # Base OSM tags (site/infrastructure) + at most one repeater per mast object.
    pending_osm_members: dict[str, list[dict[str, str]]] = defaultdict(list)
    pending_osm_move: dict[str, tuple[float, float]] = {}
    member_nodes_emitted: set[int] = set()
    # One review feature per callsign+band (+ site). Multi-band rows get one node each.
    emitted_sites: set[str] = set()

    # Shared Norwegian masts: only one callsign+band may rewrite the existing OSM
    # object; siblings get co-located synthetic nodes (OSM one-feature-per-repeater).
    mast_owner: dict[str, MergedRepeater] = {}
    for row in rows:
        if "portable" in row.flags:
            continue
        ref = _primary_osm_ref(row)
        if not ref or ref in scrub_refs:
            continue
        prev = mast_owner.get(ref)
        if prev is None or _osm_mast_merge_priority(row) < _osm_mast_merge_priority(prev):
            mast_owner[ref] = row

    next_id = -1

    def _pending_preview(ref: str | None) -> dict[str, str] | None:
        if not ref:
            return None
        members = pending_osm_members.get(ref)
        if not members:
            return None
        # Single-owner merge: one member's tags (no multi-callsign semicolon stack).
        return dict(members[0]) if len(members) == 1 else align_multi_callsign_tags(members)

    for row in rows:
        # Portables are not fixed OSM candidates — omit from the review layer.
        # Exclusion is based on the portable flag only (not on missing coordinates):
        # LA2LRR / LD3DP have positions but must still be excluded.
        if "portable" in row.flags:
            continue

        primary_ref = _primary_osm_ref(row)
        if primary_ref in scrub_refs:
            primary_ref = None
        primary_el = osm_elements.get(primary_ref) if primary_ref else None
        owns_mast = (
            primary_ref is not None
            and mast_owner.get(primary_ref) is row
        )
        if primary_el is not None and primary_ref and not owns_mast:
            # Another callsign/band already claims this mast — synthetic at site.
            primary_el = None
        if primary_el is not None and not _mergeable_osm_element(primary_el):
            # Peak/hill only — keep coords via synthetic nodes, do not modify the peak.
            # Explicit osm_id overrides may still pin onto a peak (operator choice).
            match = row.osm_match or ""
            method = (row.match_methods or {}).get("osm") or ""
            if not (match.startswith("override:") or method == "override"):
                primary_el = None
        if primary_el is not None and primary_ref:
            if not _may_merge_review_onto_osm(
                row,
                primary_el,
                pending_tags=_pending_preview(primary_ref),
            ):
                # Keep coordinates from the landmark match; do not rewrite the object.
                primary_el = None
        primary_lat = primary_lon = None
        if primary_el is not None:
            if primary_el.get("lat") is not None:
                primary_lat = float(primary_el["lat"])
                primary_lon = float(primary_el["lon"])
            elif primary_el.get("center"):
                primary_lat = float(primary_el["center"]["lat"])
                primary_lon = float(primary_el["center"]["lon"])
            # Fall back to row osm coords.
            if primary_lat is None and row.osm_lat is not None:
                primary_lat, primary_lon = row.osm_lat, row.osm_lon
        elif primary_ref and row.osm_lat is not None:
            # Sibling on a shared mast: still know the site coordinates.
            primary_lat, primary_lon = row.osm_lat, row.osm_lon

        # One feature per callsign+band (+ site). Band key keeps multi-TX rows distinct.
        band = (row.tx or "").strip() or "none"
        if primary_ref and primary_el is not None:
            site_key = f"{row.callsign}|{band}|osm:{primary_ref}"
        elif row.best_lat is not None and row.best_lon is not None:
            site_key = f"{row.callsign}|{band}:{row.best_lat:.5f}:{row.best_lon:.5f}"
        else:
            site_key = f"{row.callsign}|{band}:none"
        site_already_emitted = site_key in emitted_sites

        kind_order = ["nrrl_locator", "osm", "radioid", "repeaterbook", "local", "best"]
        by_kind: dict[str, Position] = {}
        network: list[Position] = []
        for pos in row.positions:
            if pos.source_kind == "osm_network":
                network.append(pos)
                continue
            # Bad Maidenhead (e.g. LA7CR JP49XW → ~70N sea) — do not plot it when
            # we already know it disagrees with a resolved site.
            if pos.source_kind == "nrrl_locator" and "locator_mismatch" in row.flags:
                continue
            if pos.source_kind not in by_kind:
                by_kind[pos.source_kind] = pos

        # Merge review tags onto the real OSM object; skip synthetic osm/best/local
        # when they sit on that same mast.
        if primary_ref and primary_el is not None:
            merge_kind = "osm"
            if row.best_source in {"osm", "local"}:
                merge_kind = "best" if row.best_source == "osm" else "local"
            review = _review_tags(row, kind=merge_kind)
            review["source_kind"] = "osm"
            if row.best_source:
                review["best_source"] = row.best_source
            # Single-callsign OSM features use the callsign as name.
            review["name"] = row.callsign
            # OSM note: operator site note only — no "operator override position" stack.
            if row.notes:
                review["note"] = row.notes
            else:
                review.pop("note", None)
            raw = {
                str(k): str(v) for k, v in dict(primary_el.get("tags") or {}).items()
            }
            pending_osm_raw.setdefault(primary_ref, raw)
            if primary_ref not in pending_osm_base:
                # Drop stale per-repeater / callsign tags from OSM; review members
                # redefine who is on this object (e.g. Rafjellet LD2KF/LD2KR).
                pending_osm_base[primary_ref] = strip_per_callsign_tags(raw)
            pending_osm_members[primary_ref].append(review)
            # Operator-local best position: move the OSM node to that coordinate.
            if (
                row.best_source == "local"
                and row.local_lat is not None
                and row.local_lon is not None
            ):
                pending_osm_move[primary_ref] = (row.local_lat, row.local_lon)

        # Merge onto mergeable network members first (no bare-peak synthetic clones).
        # Only the mast owner callsign+band may rewrite a shared network member.
        network_merged = 0
        for pos in sorted(network, key=lambda p: p.source_id or ""):
            ref = None
            parsed = parse_osm_ref(pos.source_id)
            if parsed:
                ref = format_osm_ref(*parsed)
            if ref in scrub_refs:
                continue
            el = osm_elements.get(ref) if ref else None
            if el is not None and ref and _mergeable_osm_element(el):
                owner = mast_owner.get(ref)
                if owner is not None and owner is not row:
                    continue
                if ref not in mast_owner:
                    mast_owner[ref] = row
                raw = {str(k): str(v) for k, v in dict(el.get("tags") or {}).items()}
                pending_osm_raw.setdefault(ref, raw)
                if ref not in pending_osm_base:
                    pending_osm_base[ref] = strip_per_callsign_tags(raw)
                review = _network_merge_tags(row, pos, pending_osm_raw[ref])
                pending_osm_members[ref].append(review)
                network_merged += 1

        # Duplicate band/mode rows at the same site: tags already merged; no more nodes.
        if site_already_emitted:
            continue
        emitted_sites.add(site_key)

        # Decide which synthetic kinds to emit (avoid coincident clones).
        kinds_to_emit: list[str] = []
        if primary_el is not None or network_merged:
            # Real OSM object(s) already carry review tags — only comparison nodes
            # when sources disagree with the site.
            if "disagreement" in row.flags:
                for kind in ("nrrl_locator", "radioid", "repeaterbook"):
                    pos = by_kind.get(kind)
                    if not pos:
                        continue
                    if primary_lat is not None and _coincident(
                        pos, primary_lat, primary_lon, max_m=2000.0
                    ):
                        continue
                    kinds_to_emit.append(kind)
        elif "disagreement" in row.flags:
            # Show distinct source positions for comparison.
            for kind in kind_order:
                if by_kind.get(kind):
                    kinds_to_emit.append(kind)
        else:
            # One review node at the resolved site (no stack of locator+best clones).
            prefer = ["local", "best", "osm", "radioid", "repeaterbook", "nrrl_locator"]
            for kind in prefer:
                if by_kind.get(kind):
                    kinds_to_emit.append(kind)
                    break
            # Shared-mast sibling with no merge: still need a site node even if
            # by_kind lacks an entry (coords only on row.best_*).
            if not kinds_to_emit and row.best_lat is not None and row.best_lon is not None:
                by_kind["best"] = Position(
                    lat=row.best_lat,
                    lon=row.best_lon,
                    source_kind="best",
                    source_id=row.best_source or "best",
                )
                kinds_to_emit.append("best")

        synthetic_ids: list[tuple[str, int]] = []  # kind, id for disagreement way
        emitted_coords: list[tuple[float, float]] = []
        for kind in kinds_to_emit:
            pos = by_kind.get(kind)
            if not pos:
                continue
            # Drop later kinds that sit on the same spot as an earlier emit.
            if any(
                _coincident(pos, lat, lon, max_m=35.0) for lat, lon in emitted_coords
            ):
                continue
            nid = next_id
            next_id -= 1
            synthetic_ids.append((kind, nid))
            emitted_coords.append((pos.lat, pos.lon))
            node = ET.SubElement(
                root,
                "node",
                {
                    "id": str(nid),
                    "visible": "true",
                    "lat": f"{pos.lat:.6f}",
                    "lon": f"{pos.lon:.6f}",
                },
            )
            _append_tags(node, _review_tags(row, kind=kind, pos=pos))

        # Disagreement ways among synthetic review nodes only.
        primary_ids = [nid for kind, nid in synthetic_ids if kind != "osm_network"]
        if (
            "disagreement" in row.flags
            and row.max_disagreement_m is not None
            and row.max_disagreement_m > disagreement_m
            and len(primary_ids) >= 2
        ):
            wid = next_id
            next_id -= 1
            way = ET.SubElement(root, "way", {"id": str(wid), "visible": "true"})
            for nid in primary_ids:
                ET.SubElement(way, "nd", {"ref": str(nid)})
            if len(primary_ids) >= 3:
                ET.SubElement(way, "nd", {"ref": str(primary_ids[0])})
            for k, v in {
                "review": "disagreement",
                "callsign": row.callsign,
                "max_disagreement_m": f"{row.max_disagreement_m:.1f}",
                "flags": "|".join(row.flags),
            }.items():
                ET.SubElement(way, "tag", {"k": k, "v": v})

    # LA5MR / Innlandsnettet relation members: keep their callsign tags and set
    # name=<callsign>. Scrubbed refs stay infrastructure-only.
    for rref in network_relation_refs:
        rel = osm_elements.get(rref)
        if not rel:
            continue
        for mem in rel.get("members") or []:
            mtype = str(mem.get("type") or "")
            mid = mem.get("ref")
            if not mtype or mid is None:
                continue
            ref = format_osm_ref(mtype, int(mid))
            if ref in scrub_refs:
                continue
            el = osm_elements.get(ref)
            if not el:
                continue
            raw = {str(k): str(v) for k, v in dict(el.get("tags") or {}).items()}
            pending_osm_raw.setdefault(ref, raw)
            member_cs = _member_callsigns(raw)
            if not member_cs:
                # Still emit untouched infrastructure if already pending.
                continue
            primary_cs = sorted(member_cs)[0]
            if ref not in pending_osm_base:
                pending_osm_base[ref] = strip_per_callsign_tags(raw)
            # Preserve / restore the member's own callsign identity.
            preserve = {
                k: v
                for k, v in raw.items()
                if k == "callsign"
                or k.startswith("communication:amateur_radio")
                or k in {"frequency", "dmr_id"}
            }
            preserve["name"] = primary_cs
            preserve.setdefault("callsign", primary_cs)
            preserve.setdefault(
                "communication:amateur_radio:callsign", primary_cs
            )
            preserve["source_kind"] = "osm_network"
            preserve["network"] = "LA5MR"
            preserve["osm_relation"] = rref
            preserve["review"] = "repeater_crosscheck"
            # Avoid duplicating if a prior merge already added this callsign.
            existing_members = pending_osm_members.get(ref) or []
            already = any(
                (m.get("callsign") or m.get("communication:amateur_radio:callsign") or "")
                .split(";")[0]
                .strip()
                .upper()
                == primary_cs
                for m in existing_members
            )
            if not already:
                pending_osm_members[ref].append(preserve)

    # Emit merged OSM objects (nodes / ways + member nodes).
    for ref in sorted(set(pending_osm_base) | set(pending_osm_members)):
        el = osm_elements.get(ref)
        if not el:
            continue
        base = dict(pending_osm_base.get(ref) or {})
        members = pending_osm_members.get(ref) or []
        if members:
            # Prefer a single callsign+band on the object (no semicolon stacks).
            if len(members) == 1:
                aligned = dict(members[0])
            else:
                aligned = align_multi_callsign_tags(members)
            tags = sanitize_existing_osm_tags({**base, **aligned})
        else:
            tags = sanitize_existing_osm_tags(base)
        # LA5MR relation members (and other single-callsign sites): name = callsign.
        cs_raw = (
            tags.get("communication:amateur_radio:callsign")
            or tags.get("callsign")
            or ""
        )
        cs_primary = cs_raw.split(";")[0].strip()
        if cs_primary and ";" not in cs_raw:
            tags["name"] = cs_primary
        parsed_ref = parse_osm_ref(ref)
        etype = el.get("type") or (parsed_ref[0] if parsed_ref else "node")
        if etype == "node":
            attrs = _element_attrs(el)
            if ref in pending_osm_move:
                mlat, mlon = pending_osm_move[ref]
                attrs["lat"] = f"{mlat:.7f}"
                attrs["lon"] = f"{mlon:.7f}"
                attrs["action"] = "modify"
            node = ET.SubElement(root, "node", attrs)
            _append_tags(node, tags)
        elif etype == "way":
            for member in el.get("_members") or []:
                mid = int(member["id"])
                if mid in member_nodes_emitted:
                    continue
                member_nodes_emitted.add(mid)
                mnode = ET.SubElement(root, "node", _element_attrs(member))
                # Member geometry nodes keep their original tags only.
                _append_tags(
                    mnode,
                    {str(k): str(v) for k, v in (member.get("tags") or {}).items()},
                )
            way = ET.SubElement(root, "way", _element_attrs(el))
            for nid in el.get("nodes") or []:
                ET.SubElement(way, "nd", {"ref": str(nid)})
            _append_tags(way, tags)
        elif etype == "relation":
            rel = ET.SubElement(root, "relation", _element_attrs(el))
            for mem in el.get("members") or []:
                ET.SubElement(
                    rel,
                    "member",
                    {
                        "type": str(mem.get("type")),
                        "ref": str(mem.get("ref")),
                        "role": str(mem.get("role") or ""),
                    },
                )
            _append_tags(rel, tags)

    _merge_coincident_synthetics(root)
    _apply_callsign_as_name(root)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(path)
