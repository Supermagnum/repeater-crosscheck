from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

from . import __version__
from .cache import RateLimitedSession, ResponseCache
from .models import MergedRepeater, Position
from .modulation import osm_amateur_tags
from .osm import is_bare_peak
from .osm_objects import (
    collect_osm_refs_from_rows,
    fetch_osm_elements,
    format_osm_ref,
    merged_osm_tags,
    parse_osm_ref,
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

    if foreign:
        tags = {
            "source_kind": "osm_network",
            "review": "repeater_crosscheck",
            "network": str(rel_name),
        }
        if rel:
            tags["osm_relation"] = f"relation/{rel}"
        return {k: v for k, v in tags.items() if v}

    review = _review_tags(row, kind="osm_network", pos=pos)
    # Keep existing OSM name on communications towers.
    if existing_tags.get("name"):
        review.pop("name", None)
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


def write_josm_osm(
    path: Path,
    rows: list[MergedRepeater],
    *,
    disagreement_m: float,
    session: RateLimitedSession | None = None,
    cache: ResponseCache | None = None,
) -> None:
    """
    Write a JOSM review file.

    Tagged upload='never' as a safeguard. Sources are public; upload only after
    tags and local knowledge have been checked.

    When an OSM mast/tower/node/way is known, download that object and merge
    amateur-radio / review tags onto it instead of inventing a duplicate node.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element(
        "osm",
        {
            "version": "0.6",
            "upload": "never",
            "generator": f"repeater-crosscheck/{__version__}",
        },
    )

    osm_elements: dict[str, dict] = {}
    if session is not None and cache is not None:
        refs = collect_osm_refs_from_rows(rows)
        if refs:
            print(f"Fetching {len(refs)} existing OSM objects to merge tags...")
            osm_elements = fetch_osm_elements(session, cache, refs)
            print(f"  loaded {len(osm_elements)} objects")

    # Accumulate merged tags per existing OSM object (multi-callsign sites).
    pending_osm: dict[str, dict[str, str]] = {}
    member_nodes_emitted: set[int] = set()

    next_id = -1

    for row in rows:
        primary_ref = _primary_osm_ref(row)
        primary_el = osm_elements.get(primary_ref) if primary_ref else None
        if primary_el is not None and not _mergeable_osm_element(primary_el):
            # Peak/hill only — keep coords via synthetic nodes, do not modify the peak.
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
            # Do not invent a review "Name (best)" on real OSM objects.
            review.pop("name", None)
            existing = pending_osm.get(primary_ref) or dict(primary_el.get("tags") or {})
            pending_osm[primary_ref] = merged_osm_tags(existing, review)

        synthetic_ids: list[tuple[str, int]] = []  # kind, id for disagreement way
        for kind in kind_order:
            pos = by_kind.get(kind)
            if not pos:
                continue
            if primary_el is not None:
                # Real OSM object already carries the review tags — no synthetic
                # osm/best/local clones (was creating triple LA5TRR-style nodes).
                if kind in {"osm", "best", "local"}:
                    continue
                # Keep locator/radioid/RB only when they disagree with the site.
                if kind in {"nrrl_locator", "radioid", "repeaterbook"}:
                    if "disagreement" not in row.flags:
                        continue
                    if primary_lat is not None and _coincident(
                        pos, primary_lat, primary_lon, max_m=2000.0
                    ):
                        continue
            nid = next_id
            next_id -= 1
            synthetic_ids.append((kind, nid))
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

        for pos in sorted(network, key=lambda p: p.source_id):
            ref = None
            parsed = parse_osm_ref(pos.source_id)
            if parsed:
                ref = format_osm_ref(*parsed)
            el = osm_elements.get(ref) if ref else None
            if el is not None and ref and _mergeable_osm_element(el):
                existing = pending_osm.get(ref) or dict(el.get("tags") or {})
                review = _network_merge_tags(row, pos, existing)
                pending_osm[ref] = merged_osm_tags(existing, review)
                continue
            # Fallback synthetic network node (also used for bare peaks).
            nid = next_id
            next_id -= 1
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
            tags = _review_tags(row, kind="osm_network", pos=pos)
            if pos.source_id:
                tags["osm_id"] = pos.source_id
            _append_tags(node, tags)

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

    # Emit merged OSM objects (nodes / ways + member nodes).
    for ref in sorted(pending_osm):
        el = osm_elements.get(ref)
        if not el:
            continue
        tags = pending_osm[ref]
        parsed_ref = parse_osm_ref(ref)
        etype = el.get("type") or (parsed_ref[0] if parsed_ref else "node")
        if etype == "node":
            node = ET.SubElement(root, "node", _element_attrs(el))
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

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(path)
