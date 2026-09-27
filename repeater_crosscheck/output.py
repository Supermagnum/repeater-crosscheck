from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path

from . import __version__
from .models import MergedRepeater, Position
from .modulation import osm_amateur_tags
from .util import fmt_coord


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


def _ordered_positions(row: MergedRepeater) -> list[Position]:
    """Emit review nodes: one per primary source, plus every network member."""
    kind_order = ["nrrl_locator", "osm", "radioid", "repeaterbook", "local", "best"]
    by_kind: dict[str, Position] = {}
    network: list[Position] = []
    for pos in row.positions:
        if pos.source_kind == "osm_network":
            network.append(pos)
            continue
        if pos.source_kind not in by_kind:
            by_kind[pos.source_kind] = pos

    out: list[Position] = []
    for kind in kind_order:
        if kind in by_kind:
            out.append(by_kind[kind])
    network.sort(key=lambda p: p.source_id)
    out.extend(network)
    return out


def write_josm_osm(
    path: Path,
    rows: list[MergedRepeater],
    *,
    disagreement_m: float,
) -> None:
    """Write a JOSM review file. upload='never' — never upload to OSM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    root = ET.Element(
        "osm",
        {
            "version": "0.6",
            "upload": "never",
            "generator": f"repeater-crosscheck/{__version__}",
        },
    )

    next_id = -1

    for row in rows:
        positions = _ordered_positions(row)
        node_ids: list[int] = []
        for pos in positions:
            nid = next_id
            next_id -= 1
            node_ids.append(nid)
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
            kind = pos.source_kind
            name_suffix = kind
            if kind == "osm_network":
                site = pos.extra.get("osm_name") or pos.source_id
                name_suffix = f"network:{site}"
            tags = {
                "source_kind": kind,
                "callsign": row.callsign,
                "name": f"{row.callsign} ({name_suffix})",
                "frequency": row.tx,
                "tone": row.tone,
                "flags": "|".join(row.flags),
                "qth": row.qth,
                "locator": row.locator,
                "group": row.group,
                "nrrl:group_page": row.group_nrrl_url,
                "website": row.group_website,
                "review": "repeater_crosscheck",
                "note": row.notes,
            }
            if kind in {"osm", "osm_network"} and pos.source_id:
                tags["osm_id"] = pos.source_id
            if kind == "osm_network":
                rel = pos.extra.get("relation_id")
                if rel:
                    tags["osm_relation"] = f"relation/{rel}"
                rel_name = pos.extra.get("relation_name")
                if rel_name:
                    tags["network"] = str(rel_name)
            if kind == "local":
                tags["fix"] = "operator_override"
            if kind == "best":
                tags["best_source"] = row.best_source
            if row.dmr_id:
                tags["dmr_id"] = row.dmr_id
            # OpenStreetMap amateur-radio tagging (review file only; upload=never).
            tags.update(osm_amateur_tags(row))
            for k, v in tags.items():
                if v:
                    ET.SubElement(node, "tag", {"k": k, "v": str(v)})

        # Disagreement ways only for unresolved conflicts (no local override).
        primary_ids = [
            nid
            for nid, pos in zip(node_ids, positions)
            if pos.source_kind != "osm_network"
        ]
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

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    # Atomic replace so JOSM / editors see a new file mtime reliably.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    tmp.replace(path)
