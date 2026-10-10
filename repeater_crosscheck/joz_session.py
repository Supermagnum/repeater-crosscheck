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
from .networks import NETWORK_RELATIONS

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


def _is_network_relation(el: ET.Element) -> bool:
    t = _tags(el)
    return t.get("type") == "network" and (
        t.get("name") in NETWORK_RELATIONS
        or t.get("name") in {m["comment_tag"] for m in NETWORK_RELATIONS.values()}
    )


def _layer_has_id(root: ET.Element, kind: str, eid: str) -> bool:
    return any(el.tag == kind and el.get("id") == eid for el in root)


def _inject_network_relations_into_layers(
    layer_roots: dict[str, ET.Element],
    network_rels: list[ET.Element],
    nodes_by_id: dict[str, ET.Element],
    ways_by_id: dict[str, ET.Element],
) -> ET.Element:
    """
    Ensure each type=network relation is a real OSM relation with members.

    - Builds a self-contained Networks overview layer (relation + member copies).
    - Also embeds each relation into every fylke layer that already holds at least
      one member, copying any cross-fylke members in so the relation is complete
      inside that layer (network=* tags alone are not enough in JOSM).
    """
    # Index which fylke layers already contain which element ids.
    layer_ids: dict[str, set[tuple[str, str]]] = {
        name: {(el.tag, el.get("id", "")) for el in root}
        for name, root in layer_roots.items()
    }

    for rel in network_rels:
        members: list[tuple[str, str, ET.Element]] = []
        for mem in rel.findall("member"):
            mtype = mem.get("type") or ""
            mid = mem.get("ref") or ""
            if mtype == "node":
                el = nodes_by_id.get(mid)
            elif mtype == "way":
                el = ways_by_id.get(mid)
            else:
                el = None
            if el is None:
                continue
            members.append((mtype, mid, el))
        if not members:
            continue

        # Fylke layers that already contain at least one member.
        host_fylker = [
            name
            for name, ids in layer_ids.items()
            if any((mtype, mid) in ids for mtype, mid, _ in members)
        ]
        for fylke in host_fylker:
            root = layer_roots[fylke]
            # Copy any missing members into this layer (same OSM ids).
            for mtype, mid, el in members:
                if (mtype, mid) in layer_ids[fylke]:
                    continue
                root.append(deepcopy(el))
                layer_ids[fylke].add((mtype, mid))
                if mtype == "way":
                    for nd in el.findall("nd"):
                        ref = nd.get("ref") or ""
                        if ref and ("node", ref) not in layer_ids[fylke]:
                            node = nodes_by_id.get(ref)
                            if node is not None:
                                root.append(deepcopy(node))
                                layer_ids[fylke].add(("node", ref))
            # Drop any prior copy of this relation id, then append.
            rid = rel.get("id")
            for old in list(root.findall("relation")):
                if old.get("id") == rid:
                    root.remove(old)
            root.append(deepcopy(rel))

    # Networks overview layer: all network relations + every member (once).
    net_root = ET.Element(
        "osm",
        {
            "version": "0.6",
            "generator": f"repeater-crosscheck/{__version__}",
        },
    )
    seen: set[tuple[str, str]] = set()
    for rel in network_rels:
        for mem in rel.findall("member"):
            mtype = mem.get("type") or ""
            mid = mem.get("ref") or ""
            key = (mtype, mid)
            if key in seen:
                continue
            el = nodes_by_id.get(mid) if mtype == "node" else ways_by_id.get(mid)
            if el is None:
                continue
            net_root.append(deepcopy(el))
            seen.add(key)
            if mtype == "way":
                for nd in el.findall("nd"):
                    ref = nd.get("ref") or ""
                    nkey = ("node", ref)
                    if ref and nkey not in seen:
                        node = nodes_by_id.get(ref)
                        if node is not None:
                            net_root.append(deepcopy(node))
                            seen.add(nkey)
        net_root.append(deepcopy(rel))
    return net_root


def split_review_osm_by_fylke(
    review_osm: Path,
    cs_fylke: dict[str, str],
) -> dict[str, bytes]:
    """
    Split repeaters_review.osm into one OSM document per fylkesnavn.

    Ways pull in their member nodes. Declared type=network relations are embedded
    into member fylke layers (with cross-fylke member copies) and also emitted as
    a dedicated Networks overview layer. Elements without callsigns that are not
    referenced by a kept way/relation are dropped.
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

    network_rels = [r for r in relations if _is_network_relation(r)]
    other_rels = [r for r in relations if not _is_network_relation(r)]

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
    for rel in other_rels:
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

    layer_roots: dict[str, ET.Element] = {}
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
            for rel in other_rels:
                if rel.get("id") == rid:
                    layer_root.append(deepcopy(rel))
                    break
        layer_roots[fylke] = layer_root

    net_root = _inject_network_relations_into_layers(
        layer_roots, network_rels, nodes_by_id, ways_by_id
    )

    out: dict[str, bytes] = {
        fylke: _pretty_osm(layer_roots[fylke]) for fylke in all_fylker
    }
    if list(net_root):
        out["Networks"] = _pretty_osm(net_root)
    return out


def _load_fylker_osm_bytes(joz_path: Path) -> bytes | None:
    """Reuse embedded Fylker boundaries from an existing .joz if present."""
    # County polygons are multi-MB; reject stubs so a bad prior write cannot
    # permanently shrink the layer across regenerations.
    min_fylker_bytes = 100_000
    cache_path = joz_path.parent / ".fylker_data.osm"

    def _accept(data: bytes | None) -> bytes | None:
        if data is not None and len(data) >= min_fylker_bytes:
            return data
        return None

    if joz_path.is_file():
        try:
            with zipfile.ZipFile(joz_path) as z:
                preferred = ("layers/fylker/data.osm", "layers/02/data.osm")
                for name in preferred:
                    try:
                        data = _accept(z.read(name))
                    except KeyError:
                        continue
                    if data is not None:
                        cache_path.write_bytes(data)
                        return data
                for name in z.namelist():
                    if name.endswith("data.osm") and "fylker" in name.lower():
                        data = _accept(z.read(name))
                        if data is not None:
                            cache_path.write_bytes(data)
                            return data
        except zipfile.BadZipFile:
            pass

    if cache_path.is_file():
        data = _accept(cache_path.read_bytes())
        if data is not None:
            return data
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
        # Innlandet + Networks visible; other fylker hidden so the panel is usable.
        visible = (
            "true" if layer_name in {"Innlandet", "Networks"} else "false"
        )
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
