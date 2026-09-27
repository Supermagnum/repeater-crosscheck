from __future__ import annotations

from dataclasses import replace

from .models import (
    CodeplugChannel,
    MergedRepeater,
    NrrlRepeater,
    Position,
    SourceRecord,
)
from .osm import match_qth_landmark, pick_osm_callsign_match
from .overrides import LocalOverride
from .nrrl_groups import NrrlGroup, match_gruppe
from .util import haversine_m, point_in_bbox


def _looks_dmr(rep: NrrlRepeater) -> bool:
    blob = f"{rep.type} {rep.info} {rep.dmr_id}".upper()
    return "DMR" in blob or bool(rep.dmr_id)


def _is_portable_qth(qth: str | None) -> bool:
    text = (qth or "").casefold()
    return "portabel" in text or "portable" in text


def _fmt_freq(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.4f}".rstrip("0").rstrip(".")

def _freq_close(a: float | None, b: float | None, tol: float) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def match_source_records(
    repeater: NrrlRepeater,
    records: list[SourceRecord],
    *,
    freq_tol: float,
    freq_max_m: float,
) -> tuple[SourceRecord | None, str]:
    """Match by callsign first, then frequency + distance."""
    by_call = [r for r in records if r.callsign and r.callsign == repeater.callsign]
    if by_call:
        # Prefer ones with coordinates, then stable id.
        by_call.sort(key=lambda r: (0 if r.lat is not None else 1, r.source_id))
        return by_call[0], "callsign"

    if repeater.locator_lat is None or repeater.tx_mhz is None:
        return None, ""

    candidates: list[tuple[float, SourceRecord]] = []
    for r in records:
        if r.lat is None or r.lon is None:
            continue
        tx_ok = _freq_close(r.tx_mhz, repeater.tx_mhz, freq_tol) or _freq_close(
            r.rx_mhz, repeater.tx_mhz, freq_tol
        )
        # Also compare against repeater RX (output of repeater is TX in NRRL naming:
        # NRRL "Freq TX" is the repeater transmit / user receive frequency).
        rx_ok = _freq_close(r.tx_mhz, repeater.rx_mhz, freq_tol) or _freq_close(
            r.rx_mhz, repeater.rx_mhz, freq_tol
        )
        if not (tx_ok or rx_ok):
            continue
        dist = haversine_m(repeater.locator_lat, repeater.locator_lon, r.lat, r.lon)
        if dist <= freq_max_m:
            candidates.append((dist, r))

    if not candidates:
        return None, ""
    candidates.sort(key=lambda t: (t[0], t[1].source_id))
    return candidates[0][1], "frequency_distance"


def choose_best_position(
    repeater: NrrlRepeater,
    *,
    local_pos: Position | None,
    osm_qth_pos: Position | None,
    osm_relation_pos: Position | None,
    radioid: SourceRecord | None,
    repeaterbook: SourceRecord | None,
    osm_call: SourceRecord | None,
    near_m: float,
    portable: bool,
) -> tuple[float | None, float | None, str]:
    """
    Priority:
      0) local / operator override with coordinates
      a) OSM mast/tower/peak matching QTH inside locator square
      a2) OSM member of a same-callsign network relation inside/near the square
      b) radioid or RepeaterBook if inside/near locator square
      c) locator square centre
    """
    bbox = repeater.locator_bbox

    if local_pos is not None:
        return local_pos.lat, local_pos.lon, "local"

    if osm_qth_pos is not None:
        return osm_qth_pos.lat, osm_qth_pos.lon, "osm"

    if osm_relation_pos is not None:
        return osm_relation_pos.lat, osm_relation_pos.lon, "osm"

    def near_ok(lat: float | None, lon: float | None) -> bool:
        if lat is None or lon is None or bbox is None:
            return False
        south, west, north, east = bbox
        return point_in_bbox(lat, lon, south, west, north, east, margin_m=near_m)

    # Prefer radioid then repeaterbook when both qualify (stable order).
    if not portable:
        if radioid and near_ok(radioid.lat, radioid.lon):
            return radioid.lat, radioid.lon, "radioid"
        if repeaterbook and near_ok(repeaterbook.lat, repeaterbook.lon):
            return repeaterbook.lat, repeaterbook.lon, "repeaterbook"

    # If no locator, still accept source coords as best with clear label.
    if bbox is None:
        if radioid and radioid.lat is not None:
            return radioid.lat, radioid.lon, "radioid"
        if repeaterbook and repeaterbook.lat is not None:
            return repeaterbook.lat, repeaterbook.lon, "repeaterbook"
        if osm_call and osm_call.lat is not None:
            return osm_call.lat, osm_call.lon, "osm"

    if repeater.locator_lat is not None and repeater.locator_lon is not None:
        return repeater.locator_lat, repeater.locator_lon, "nrrl_locator"

    return None, None, ""


def compute_disagreement(positions: list[Position]) -> float | None:
    coords = [(p.lat, p.lon) for p in positions]
    if len(coords) < 2:
        return 0.0 if coords else None
    max_d = 0.0
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            d = haversine_m(coords[i][0], coords[i][1], coords[j][0], coords[j][1])
            if d > max_d:
                max_d = d
    return max_d


def build_merged(
    nrrl: list[NrrlRepeater],
    *,
    osm_records: list[SourceRecord],
    landmarks_by_locator: dict[str, list[dict]],
    radioid_records: list[SourceRecord],
    rb_records: list[SourceRecord],
    channels: list[CodeplugChannel],
    thresholds: dict,
    overrides: dict[str, LocalOverride] | None = None,
    group_lookup: dict[str, NrrlGroup] | None = None,
) -> tuple[list[MergedRepeater], list[str]]:
    unmatched_notes: list[str] = []
    disagreement_m = float(thresholds["disagreement_m"])
    near_m = float(thresholds["locator_near_m"])
    freq_tol = float(thresholds["freq_tolerance_mhz"])
    freq_max_m = float(thresholds["freq_match_max_m"])
    fuzzy_min = int(thresholds["qth_fuzzy_min"])

    channels_by_call: dict[str, list[CodeplugChannel]] = {}
    for ch in channels:
        if ch.callsign:
            channels_by_call.setdefault(ch.callsign, []).append(ch)

    nrrl_calls = {r.callsign for r in nrrl}
    merged: list[MergedRepeater] = []
    overrides = overrides or {}

    for rep in nrrl:
        ov = overrides.get(rep.callsign)
        portable = bool(ov and ov.portable) or _is_portable_qth(rep.qth)
        status_u = (rep.status or "").upper()
        info_u = (rep.info or "").upper()
        nrrl_qrt = status_u == "QRT" or "QRT" in info_u
        if ov and ov.on_air:
            qrt = False
        elif ov and ov.qrt is not None:
            qrt = ov.qrt
        else:
            qrt = nrrl_qrt
        match_rep = replace(rep, qth=ov.qth) if ov and ov.qth else rep

        row_status = rep.status
        if ov and ov.status:
            row_status = ov.status
        elif ov and ov.on_air:
            row_status = "on-air"
        elif ov and ov.qrt is False and nrrl_qrt:
            row_status = "on-air"

        row = MergedRepeater(
            callsign=rep.callsign,
            type=(ov.type if ov and ov.type else rep.type),
            qth=ov.qth if ov and ov.qth else rep.qth,
            tx=_fmt_freq(ov.tx_mhz if ov and ov.tx_mhz is not None else rep.tx_mhz),
            rx=_fmt_freq(ov.rx_mhz if ov and ov.rx_mhz is not None else rep.rx_mhz),
            tone=(ov.tone if ov and ov.tone else rep.tone),
            dmr_id=rep.dmr_id,
            group=(ov.group if ov and ov.group else rep.group),
            status=row_status,
            locator=rep.locator,
            locator_lat=rep.locator_lat,
            locator_lon=rep.locator_lon,
            locator_precision_m=rep.locator_precision_m,
            notes=(ov.note if ov else ""),
        )
        if ov and ov.url and ov.url not in row.notes:
            row.notes = (row.notes + " " + ov.url).strip()

        positions: list[Position] = []
        if not portable and rep.locator_lat is not None and rep.locator_lon is not None:
            positions.append(
                Position(
                    lat=rep.locator_lat,
                    lon=rep.locator_lon,
                    source_kind="nrrl_locator",
                    source_id=rep.locator,
                )
            )
        elif portable and rep.locator_lat is not None:
            # Keep locator as a coarse area hint, but flag it.
            positions.append(
                Position(
                    lat=rep.locator_lat,
                    lon=rep.locator_lon,
                    source_kind="nrrl_locator",
                    source_id=rep.locator,
                )
            )

        local_pos = None
        if ov and ov.lat is not None and ov.lon is not None:
            local_pos = Position(
                lat=ov.lat,
                lon=ov.lon,
                source_kind="local",
                source_id="overrides.toml",
            )
            row.local_lat = ov.lat
            row.local_lon = ov.lon
            positions.append(local_pos)

        landmarks = landmarks_by_locator.get(rep.locator, [])
        osm_qth_pos, osm_match_label = match_qth_landmark(
            match_rep, landmarks, fuzzy_min=fuzzy_min
        )
        osm_call = pick_osm_callsign_match(
            rep.callsign,
            osm_records,
            rep.locator_bbox,
            locator_lat=rep.locator_lat,
            locator_lon=rep.locator_lon,
            near_m=near_m,
        )
        osm_relation_pos = None
        if osm_call and osm_call.extra.get("relation_id") and osm_call.lat is not None:
            south = west = north = east = None
            if rep.locator_bbox:
                south, west, north, east = rep.locator_bbox
            if south is None or point_in_bbox(
                osm_call.lat, osm_call.lon, south, west, north, east, margin_m=near_m
            ):
                osm_relation_pos = Position(
                    lat=osm_call.lat,
                    lon=osm_call.lon,
                    source_kind="osm",
                    source_id=osm_call.source_id,
                    extra={
                        "osm_match": "relation_member",
                        "relation_id": osm_call.extra.get("relation_id"),
                    },
                )

        if osm_qth_pos:
            row.osm_lat = osm_qth_pos.lat
            row.osm_lon = osm_qth_pos.lon
            row.osm_id = osm_qth_pos.source_id
            row.osm_match = osm_match_label
            row.match_methods["osm"] = osm_qth_pos.extra.get("osm_match", "qth")
            positions.append(osm_qth_pos)
        elif osm_call and osm_call.lat is not None:
            row.osm_lat = osm_call.lat
            row.osm_lon = osm_call.lon
            row.osm_id = osm_call.source_id
            rel = osm_call.extra.get("relation_id")
            if rel:
                row.osm_match = (
                    f"relation_member:relation/{rel};{osm_call.source_id}"
                )
                row.match_methods["osm"] = "relation_member"
                row.flags.append(f"osm_network:{osm_call.extra.get('relation_name') or rel}")
            else:
                row.osm_match = f"callsign:{osm_call.source_id}"
                row.match_methods["osm"] = "callsign"
            positions.append(
                Position(
                    lat=osm_call.lat,
                    lon=osm_call.lon,
                    source_kind="osm",
                    source_id=osm_call.source_id,
                    extra=osm_call.extra,
                )
            )
        else:
            unmatched_notes.append(f"{rep.callsign}: not found in OSM")
            row.flags.append("missing_osm")

        # Multi-site OSM networks (e.g. LA5MR) even when this QTH is not a member.
        net_hits = [
            r
            for r in osm_records
            if r.callsign == rep.callsign and r.extra.get("relation_id")
        ]
        if net_hits:
            rel_name = net_hits[0].extra.get("relation_name") or net_hits[0].extra.get(
                "relation_id"
            )
            flag = f"osm_network:{rel_name}"
            if flag not in row.flags:
                row.flags.append(flag)
            # One JOSM node per network member for multi-site review.
            seen_ids: set[str] = set()
            for rec in sorted(net_hits, key=lambda r: r.source_id):
                if rec.lat is None or rec.lon is None:
                    continue
                if rec.source_id in seen_ids:
                    continue
                seen_ids.add(rec.source_id)
                positions.append(
                    Position(
                        lat=rec.lat,
                        lon=rec.lon,
                        source_kind="osm_network",
                        source_id=rec.source_id,
                        extra={
                            "relation_id": rec.extra.get("relation_id"),
                            "relation_name": rec.extra.get("relation_name") or "",
                            "osm_name": rec.qth,
                        },
                    )
                )

        rid, rid_method = match_source_records(
            rep, radioid_records, freq_tol=freq_tol, freq_max_m=freq_max_m
        )
        if rid and rid.lat is not None:
            row.radioid_lat = rid.lat
            row.radioid_lon = rid.lon
            row.match_methods["radioid"] = rid_method
            positions.append(
                Position(lat=rid.lat, lon=rid.lon, source_kind="radioid", source_id=rid.source_id)
            )
            if rid.dmr_id and not row.dmr_id:
                row.dmr_id = rid.dmr_id
        else:
            if _looks_dmr(rep) and radioid_records:
                unmatched_notes.append(f"{rep.callsign}: not found in radioid")
                row.flags.append("missing_radioid")

        rb, rb_method = match_source_records(
            rep, rb_records, freq_tol=freq_tol, freq_max_m=freq_max_m
        )
        if rb and rb.lat is not None:
            row.repeaterbook_lat = rb.lat
            row.repeaterbook_lon = rb.lon
            row.match_methods["repeaterbook"] = rb_method
            positions.append(
                Position(
                    lat=rb.lat,
                    lon=rb.lon,
                    source_kind="repeaterbook",
                    source_id=rb.source_id,
                )
            )
            if rb.dmr_id and not row.dmr_id:
                row.dmr_id = rb.dmr_id
            if rb.tone and not row.tone:
                row.tone = rb.tone
        elif rb_records:
            unmatched_notes.append(f"{rep.callsign}: not found in RepeaterBook")
            row.flags.append("missing_repeaterbook")

        osm_call_for_best = osm_call
        best_lat, best_lon, best_source = choose_best_position(
            rep,
            local_pos=local_pos,
            osm_qth_pos=osm_qth_pos,
            osm_relation_pos=osm_relation_pos,
            radioid=rid,
            repeaterbook=rb,
            osm_call=osm_call_for_best,
            near_m=near_m,
            portable=portable,
        )
        row.best_lat = best_lat
        row.best_lon = best_lon
        row.best_source = best_source
        if best_lat is not None:
            positions.append(
                Position(lat=best_lat, lon=best_lon, source_kind="best", source_id=best_source)
            )

        row.positions = positions
        # Local operator coordinates resolve the site: keep other source nodes
        # for comparison, but do not flag or draw disagreement ways.
        if local_pos is not None:
            disagree_positions = [local_pos]
        else:
            disagree_positions = [
                p
                for p in positions
                if p.source_kind not in {"best", "osm_network"}
            ]
            if portable:
                disagree_positions = [
                    p for p in disagree_positions if p.source_kind != "nrrl_locator"
                ]
        row.max_disagreement_m = compute_disagreement(disagree_positions)

        if (
            local_pos is None
            and row.max_disagreement_m is not None
            and row.max_disagreement_m > disagreement_m
        ):
            row.flags.append("disagreement")

        if portable:
            row.flags.append("portable")

        # Positions outside locator square (excluding locator centre itself).
        if rep.locator_bbox and not portable and local_pos is None:
            south, west, north, east = rep.locator_bbox
            for p in positions:
                if p.source_kind in {"nrrl_locator", "best", "osm_network"}:
                    continue
                if not point_in_bbox(p.lat, p.lon, south, west, north, east, margin_m=0):
                    flag = f"outside_locator:{p.source_kind}"
                    if flag not in row.flags:
                        row.flags.append(flag)

        if (
            local_pos
            and rep.locator_lat is not None
            and not portable
        ):
            dist_loc = haversine_m(
                local_pos.lat, local_pos.lon, rep.locator_lat, rep.locator_lon
            )
            if dist_loc > max(float(rep.locator_precision_m or 0), 20000):
                row.flags.append("locator_mismatch")

        status_u = (row.status or "").upper()
        if qrt or status_u == "QRT":
            row.flags.append("qrt")

        chs = channels_by_call.get(rep.callsign, [])
        row.codeplug_channels = sorted({c.name for c in chs})
        zones: set[str] = set()
        for c in chs:
            zones.update(c.zones)
        row.codeplug_zones = sorted(zones)

        if group_lookup:
            ginfo = match_gruppe(row.group, group_lookup)
            if ginfo:
                row.group_nrrl_url = ginfo.nrrl_url
                row.group_website = ginfo.website
            else:
                unmatched_notes.append(
                    f"{rep.callsign}: Gruppe {row.group!r} not mapped on nrrl.no/grupper/"
                )

        row.flags = sorted(set(row.flags))
        merged.append(row)

    # Codeplug channels with a callsign that never appears in NRRL / sources.
    all_source_calls = set(nrrl_calls)
    for rec in osm_records + radioid_records + rb_records:
        if rec.callsign:
            all_source_calls.add(rec.callsign)

    for ch in channels:
        if not ch.callsign:
            unmatched_notes.append(
                f"codeplug channel {ch.name!r}: no callsign found in channel name"
            )
            continue
        if ch.callsign not in all_source_calls:
            unmatched_notes.append(
                f"codeplug channel {ch.name!r}: callsign {ch.callsign} not in any source"
            )

    merged.sort(key=lambda r: r.callsign)
    unmatched_notes = sorted(set(unmatched_notes))
    return merged, unmatched_notes
