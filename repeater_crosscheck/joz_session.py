"""Build repeaters.joz with one JOSM data layer per fylke."""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from pathlib import Path
from copy import deepcopy

from . import __version__
from .fylke import FYLKE_LAYER_ORDER, callsign_fylke_map, fylke_slug
from .models import MergedRepeater

# Kartverket topo imagery layer (from the hand-built session).
_KARTVERKET_LAYER_XML = """        <layer index="{index}" name="Kartverket topo" type="imagery" version="0.1" visible="true">
            <max-zoom>18</max-zoom>
            <min-zoom>3</min-zoom>
            <valid-georeference>true</valid-georeference>
            <transparent>true</transparent>
            <minimumTileExpire>3600</minimumTileExpire>
            <name>Kartverket topo</name>
            <id>kartverket-topo</id>
            <type>tms</type>
            <url>https://cache.kartverket.no/v1/service?SERVICE=WMTS&amp;REQUEST=GetTile&amp;VERSION=1.0.0&amp;LAYER=topo&amp;STYLE=default&amp;FORMAT=image/png&amp;tileMatrixSet=webmercator&amp;tileMatrix={{zoom}}&amp;tileRow={{y}}&amp;tileCol={{x}}</url>
            <attribution-text>© Kartverket</attribution-text>
            <attribution-url>https://www.kartverket.no/</attribution-url>
            <permission-reference-url>https://community.openstreetmap.org/t/fornyet-tillatelse-fra-kartverket/92410</permission-reference-url>
            <country-code>NO</country-code>
            <cookies/>
            <bounds>57.72468,4.08142,71.40266,31.78344</bounds>
            <category>map</category>
            <show-errors>true</show-errors>
            <automatic-downloading>true</automatic-downloading>
            <automatically-change-resolution>true</automatically-change-resolution>
        </layer>
"""


def _tags(el: ET.Element) -> dict[str, str]:
    return {t.get("k", ""): t.get("v", "") for t in el.findall("tag")}


def _callsigns_on(el: ET.Element) -> list[str]:
    tags = _tags(el)
    raw = tags.get("callsign") or tags.get("communication:amateur_radio:callsign") or ""
    return [p.strip().upper() for p in raw.split(";") if p.strip()]


def _primary_fylke_for_element(el: ET.Element, cs_fylke: dict[str, str]) -> str | None:
    """One layer per feature so stacked copies do not reappear when all layers are on."""
    calls = _callsigns_on(el)
    if not calls:
        return None
    counts: dict[str, int] = defaultdict(int)
    for c in calls:
        counts[cs_fylke.get(c, "Unknown")] += 1
    order = {name: i for i, name in enumerate(FYLKE_LAYER_ORDER)}
    return min(counts, key=lambda f: (-counts[f], order.get(f, 999), f))


def _pretty_osm(root: ET.Element) -> bytes:
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="utf-8")
        + b"\n"
    )


def split_review_osm_by_fylke(
    review_osm: Path,
    cs_fylke: dict[str, str],
) -> dict[str, bytes]:
    """
    Split repeaters_review.osm into one OSM document per fylkesnavn.

    Ways pull in their member nodes. Elements without callsigns that are not
    referenced by a kept way are dropped.
    """
    root = ET.parse(review_osm).getroot()
    nodes_by_id: dict[str, ET.Element] = {}
    ways: list[ET.Element] = []
    relations: list[ET.Element] = []
    for el in list(root):
        if el.tag == "node":
            nodes_by_id[el.get("id", "")] = el
        elif el.tag == "way":
            ways.append(el)
        elif el.tag == "relation":
            relations.append(el)

    fylke_nodes: dict[str, set[str]] = defaultdict(set)
    fylke_ways: dict[str, set[str]] = defaultdict(set)
    fylke_rels: dict[str, set[str]] = defaultdict(set)

    for nid, node in nodes_by_id.items():
        fylke = _primary_fylke_for_element(node, cs_fylke)
        if fylke:
            fylke_nodes[fylke].add(nid)
    for way in ways:
        wid = way.get("id", "")
        fylke = _primary_fylke_for_element(way, cs_fylke)
        if fylke:
            fylke_ways[fylke].add(wid)
    for rel in relations:
        rid = rel.get("id", "")
        fylke = _primary_fylke_for_element(rel, cs_fylke)
        if fylke:
            fylke_rels[fylke].add(rid)

    # Ways need member nodes
    ways_by_id = {w.get("id", ""): w for w in ways}
    for fylke, wids in list(fylke_ways.items()):
        for wid in wids:
            way = ways_by_id.get(wid)
            if way is None:
                continue
            for nd in way.findall("nd"):
                ref = nd.get("ref")
                if ref:
                    fylke_nodes[fylke].add(ref)

    out: dict[str, bytes] = {}
    all_fylker = sorted(
        set(fylke_nodes) | set(fylke_ways) | set(fylke_rels),
        key=lambda n: (
            FYLKE_LAYER_ORDER.index(n) if n in FYLKE_LAYER_ORDER else 999,
            n,
        ),
    )
    for fylke in all_fylker:
        layer_root = ET.Element(
            "osm",
            {
                "version": "0.6",
                "generator": f"repeater-crosscheck/{__version__}",
            },
        )
        for nid in sorted(fylke_nodes[fylke], key=lambda x: int(x)):
            node = nodes_by_id.get(nid)
            if node is not None:
                layer_root.append(deepcopy(node))
        for wid in sorted(fylke_ways[fylke], key=lambda x: int(x)):
            way = ways_by_id.get(wid)
            if way is not None:
                layer_root.append(deepcopy(way))
        for rid in sorted(fylke_rels[fylke], key=lambda x: int(x)):
            for rel in relations:
                if rel.get("id") == rid:
                    layer_root.append(deepcopy(rel))
                    break
        out[fylke] = _pretty_osm(layer_root)
    return out


def _load_fylker_osm_bytes(joz_path: Path) -> bytes | None:
    """Reuse embedded Fylker boundaries from an existing .joz if present."""
    if not joz_path.is_file():
        return None
    # County polygons are multi-MB; reject stubs so a bad prior write cannot
    # permanently shrink the layer across regenerations.
    min_fylker_bytes = 100_000
    try:
        with zipfile.ZipFile(joz_path) as z:
            preferred = ("layers/fylker/data.osm", "layers/02/data.osm")
            for name in preferred:
                try:
                    data = z.read(name)
                except KeyError:
                    continue
                if len(data) >= min_fylker_bytes:
                    return data
            for name in z.namelist():
                if name.endswith("data.osm") and "fylker" in name.lower():
                    data = z.read(name)
                    if len(data) >= min_fylker_bytes:
                        return data
    except zipfile.BadZipFile:
        return None
    return None


def write_repeaters_joz(
    joz_path: Path,
    review_osm: Path,
    rows: list[MergedRepeater],
    *,
    fylker_osm: Path | None = None,
) -> list[str]:
    """
    Write a .joz session with one data layer per fylkesnavn plus Fylker + topo.

    Returns the list of fylke layer names written.
    """
    joz_path.parent.mkdir(parents=True, exist_ok=True)
    cs_fylke = callsign_fylke_map(rows)
    by_fylke = split_review_osm_by_fylke(review_osm, cs_fylke)

    fylker_bytes: bytes | None = None
    if fylker_osm and fylker_osm.is_file():
        fylker_bytes = fylker_osm.read_bytes()
    if fylker_bytes is None:
        fylker_bytes = _load_fylker_osm_bytes(joz_path)

    # Stable order
    fylke_names = sorted(
        by_fylke.keys(),
        key=lambda n: (
            FYLKE_LAYER_ORDER.index(n) if n in FYLKE_LAYER_ORDER else 999,
            n,
        ),
    )

    layer_entries: list[tuple[str, str, bytes]] = []  # (arcname, layer_name, data)
    for name in fylke_names:
        slug = fylke_slug(name)
        arc = f"layers/{slug}/data.osm"
        layer_entries.append((arc, name, by_fylke[name]))

    fylker_arc = None
    if fylker_bytes:
        fylker_arc = "layers/fylker/data.osm"

    # Build session.jos
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<josm-session version="0.1">',
        "    <viewport>",
        '        <center lat="61.5" lon="10.5"/>',
        '        <scale meter-per-pixel="1200"/>',
        "    </viewport>",
        "    <projection>",
        "        <projection-choice>",
        "            <id>core:mercator</id>",
        "            <parameters/>",
        "        </projection-choice>",
        "        <code>EPSG:3857</code>",
        "    </projection>",
        '    <layers active="1">',
    ]
    index = 1
    for arc, layer_name, _data in layer_entries:
        # Innlandet visible; others hidden so the panel is usable.
        visible = "true" if layer_name == "Innlandet" else "false"
        lines.append(
            f'        <layer index="{index}" name="{layer_name}" '
            f'type="osm-data" version="0.1" visible="{visible}">'
        )
        lines.append(f"            <file>{arc}</file>")
        lines.append("        </layer>")
        index += 1
    if fylker_arc:
        lines.append(
            f'        <layer index="{index}" name="Fylker-linjer" '
            f'type="osm-data" version="0.1" visible="true">'
        )
        lines.append(f"            <file>{fylker_arc}</file>")
        lines.append("        </layer>")
        index += 1
    lines.append(_KARTVERKET_LAYER_XML.format(index=index).rstrip())
    lines.append("    </layers>")
    lines.append("</josm-session>")
    lines.append("")
    session_xml = "\n".join(lines).encode("utf-8")

    # Write zip (JOSM style: real files only, no empty directory entries)
    tmp = joz_path.with_suffix(".joz.tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for arc, _name, data in layer_entries:
            z.writestr(arc, data)
        if fylker_arc and fylker_bytes:
            z.writestr(fylker_arc, fylker_bytes)
        z.writestr("session.jos", session_xml)
    tmp.replace(joz_path)
    return fylke_names
