from __future__ import annotations

from dataclasses import replace

from .models import (
    CodeplugChannel,
    MergedRepeater,
    NrrlRepeater,
    Position,
    SourceRecord,
)
from .osm import (
    is_bare_peak,
    match_qth_landmark,
    nearest_landmark,
    pick_osm_callsign_match,
    record_has_callsign,
    retarget_peak_to_transmitter,
    source_records_as_landmarks,
)
from .osm_objects import format_osm_ref, parse_osm_ref
from .overrides import LocalOverride
from .nrrl_groups import NrrlGroup, match_gruppe
from .util import haversine_m, point_in_bbox


def _norm_tx_key(tx: str | None) -> str:
    text = (tx or "").strip()
    if not text:
        return ""
    try:
        return f"{float(text):.6f}"
    except ValueError:
        return text


def _row_has_best_position(row: MergedRepeater) -> bool:
    return row.best_lat is not None and row.best_lon is not None


def _merge_duplicate_row(keep: MergedRepeater, other: MergedRepeater) -> MergedRepeater:
    """Fill empty fields on keep from other (same callsign / TX duplicate)."""
    for attr in (
        "type",
        "qth",
        "rx",
        "tone",
        "dmr_id",
        "group",
        "status",
        "locator",
        "notes",
        "group_nrrl_url",
        "group_website",
    ):
        cur = getattr(keep, attr, None)
        alt = getattr(other, attr, None)
        if (cur is None or cur == "") and alt not in (None, ""):
            setattr(keep, attr, alt)
    if not keep.codeplug_channels and other.codeplug_channels:
        keep.codeplug_channels = list(other.codeplug_channels)
    if not keep.codeplug_zones and other.codeplug_zones:
        keep.codeplug_zones = list(other.codeplug_zones)
    if keep.locator_lat is None and other.locator_lat is not None:
        keep.locator_lat = other.locator_lat
        keep.locator_lon = other.locator_lon
        keep.locator_precision_m = other.locator_precision_m
    for attr in (
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
        "elevation_m",
    ):
        cur = getattr(keep, attr, None)
        alt = getattr(other, attr, None)
        if cur in (None, "") and alt not in (None, ""):
            setattr(keep, attr, alt)
    for flag in other.flags:
        if flag not in keep.flags:
            keep.flags.append(flag)
    for key, val in other.match_methods.items():
        keep.match_methods.setdefault(key, val)
    if other.notes and other.notes not in (keep.notes or ""):
        keep.notes = (keep.notes + " " + other.notes).strip() if keep.notes else other.notes
    return keep


def collapse_positionless_duplicates(rows: list[MergedRepeater]) -> list[MergedRepeater]:
    """
    Merge duplicate CSV rows for the same callsign+TX when one lacks a position
    (e.g. LD8SH listed under two NRRL groups).
    """
    groups: dict[tuple[str, str], list[MergedRepeater]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = ((row.callsign or "").upper(), _norm_tx_key(row.tx))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    out: list[MergedRepeater] = []
    for key in order:
        bucket = groups[key]
        if len(bucket) == 1:
            out.append(bucket[0])
            continue
        with_pos = [r for r in bucket if _row_has_best_position(r)]
        without = [r for r in bucket if not _row_has_best_position(r)]
        if with_pos and without:
            keep = with_pos[0]
            for other in with_pos[1:] + without:
                keep = _merge_duplicate_row(keep, other)
            out.append(keep)
        else:
            # Different bands or all positioned — keep as separate rows.
            out.extend(bucket)
    return out


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
    dedicated_osm_call: bool = False,
) -> tuple[float | None, float | None, str]:
    """
    Priority:
      0) local / operator override with coordinates
      a0) OSM feature already tagged with this callsign
      a) OSM mast/tower/peak matching QTH inside locator square
      a2) OSM member of a same-callsign network relation inside/near the square
      b) radioid if inside/near locator square
      b2) RepeaterBook lat/lon (preferred over Maidenhead centre)
      c) locator square centre (last resort — often kilometres off)
    """
    bbox = repeater.locator_bbox

    if local_pos is not None:
        return local_pos.lat, local_pos.lon, "local"

    # Callsign-tagged OSM wins over QTH landmark (avoids wrong summit vs mast).
    if (
        dedicated_osm_call
        and osm_call is not None
        and osm_call.lat is not None
        and osm_call.lon is not None
    ):
        return osm_call.lat, osm_call.lon, "osm"

    if osm_qth_pos is not None:
        return osm_qth_pos.lat, osm_qth_pos.lon, "osm"

    if osm_relation_pos is not None:
        return osm_relation_pos.lat, osm_relation_pos.lon, "osm"

    def near_ok(lat: float | None, lon: float | None) -> bool:
        if lat is None or lon is None or bbox is None:
            return False
        south, west, north, east = bbox
        return point_in_bbox(lat, lon, south, west, north, east, margin_m=near_m)

    if not portable:
        if radioid and near_ok(radioid.lat, radioid.lon):
            return radioid.lat, radioid.lon, "radioid"
        # Maidenhead centres are too coarse; prefer published RepeaterBook pins
        # even when slightly outside the NRRL locator square.
        if (
            repeaterbook
            and repeaterbook.lat is not None
            and repeaterbook.lon is not None
        ):
            return repeaterbook.lat, repeaterbook.lon, "repeaterbook"
        if radioid and radioid.lat is not None and radioid.lon is not None:
            return radioid.lat, radioid.lon, "radioid"

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

    overrides = overrides or {}
    # Override-only stations (e.g. APRS SSIDs heard on aprs.no / aprs.fi but
    # absent from the NRRL list) are synthesized into the NRRL-shaped loop.
    nrrl_by_call = {r.callsign: r for r in nrrl}
    extra_nrrl: list[NrrlRepeater] = []
    for call, ov in overrides.items():
        if ov.skip or call in nrrl_by_call:
            continue
        if ov.lat is None and ov.lon is None and not ov.type and ov.tx_mhz is None:
            continue
        status = ov.status
        if not status and ov.qrt:
            status = "QRT"
        elif not status and ov.on_air:
            status = "on-air"
        extra_nrrl.append(
            NrrlRepeater(
                callsign=call,
                callsign_raw=call,
                type=ov.type or "APRS",
                qth=ov.qth or "",
                tx_mhz=ov.tx_mhz,
                rx_mhz=ov.rx_mhz if ov.rx_mhz is not None else ov.tx_mhz,
                group=ov.group or "",
                locator="",
                info=ov.note or "",
                status=status,
                tone=ov.tone,
            )
        )
    if extra_nrrl:
        nrrl = list(nrrl) + extra_nrrl

    nrrl_calls = {r.callsign for r in nrrl}
    merged: list[MergedRepeater] = []

    for rep in nrrl:
        ov = overrides.get(rep.callsign)
        if ov and ov.skip:
            continue
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

        landmarks = list(landmarks_by_locator.get(rep.locator, []))
        # Include amateur-tagged OSM objects (e.g. communications_tower) so QTH
        # matching can prefer "Tron hovedsender" over the bare peak "Tron".
        if rep.locator_bbox:
            south, west, north, east = rep.locator_bbox
            for el in source_records_as_landmarks(osm_records):
                if point_in_bbox(el["lat"], el["lon"], south, west, north, east, margin_m=near_m):
                    landmarks.append(el)
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

        # Explicit override OSM object wins (merge tags onto that mast/node/way).
        # detach_osm keeps coordinates but leaves the infrastructure object alone.
        ov_osm_ref = None
        if ov and ov.osm_id and not (ov and ov.skip_osm) and not (ov and ov.detach_osm):
            parsed = parse_osm_ref(ov.osm_id)
            if parsed:
                ov_osm_ref = format_osm_ref(*parsed)

        # Prefer a feature already tagged with this callsign over a QTH name guess
        # (avoids peak "Tron" beating tower "Tron hovedsender" for LA9AR).
        skip_osm = bool(ov and (ov.skip_osm or ov.detach_osm))
        dedicated_call = (
            not skip_osm
            and osm_call is not None
            and osm_call.lat is not None
            and record_has_callsign(osm_call, rep.callsign)
        )

        if ov and ov.detach_osm:
            unmatched_notes.append(
                f"{rep.callsign}: detached from {ov.osm_id} (synthetic node)"
            )
        elif skip_osm and ov and ov.skip_osm:
            if "missing_osm" not in row.flags:
                row.flags.append("missing_osm")
            unmatched_notes.append(f"{rep.callsign}: OSM match skipped (skip_osm override)")
        elif dedicated_call:
            row.osm_lat = osm_call.lat
            row.osm_lon = osm_call.lon
            row.osm_id = osm_call.source_id
            rel = osm_call.extra.get("relation_id")
            if rel:
                row.osm_match = (
                    f"relation_member:relation/{rel};{osm_call.source_id}"
                )
                row.match_methods["osm"] = "relation_member"
                row.flags.append(
                    f"osm_network:{osm_call.extra.get('relation_name') or rel}"
                )
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
        elif osm_qth_pos:
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
            # Nearby mast/tower when operator coords pin the site.
            near_lat = local_pos.lat if local_pos else None
            near_lon = local_pos.lon if local_pos else None
            if near_lat is not None and near_lon is not None:
                near_pos, near_label = nearest_landmark(
                    near_lat,
                    near_lon,
                    landmarks,
                    max_m=150.0,
                    transmitters_only=True,
                )
                if not near_pos:
                    near_pos, near_label = nearest_landmark(
                        near_lat, near_lon, landmarks, max_m=75.0
                    )
                if near_pos:
                    row.osm_lat = near_pos.lat
                    row.osm_lon = near_pos.lon
                    row.osm_id = near_pos.source_id
                    row.osm_match = near_label
                    row.match_methods["osm"] = "nearest"
                    positions.append(near_pos)
            if not row.osm_id:
                unmatched_notes.append(f"{rep.callsign}: not found in OSM")
                row.flags.append("missing_osm")

        # Peak -> nearby mast/tower (Tron-class): never treat a bare summit as the site.
        if row.osm_id and not ov_osm_ref:
            for i, p in enumerate(list(positions)):
                if p.source_kind != "osm" or p.source_id != row.osm_id:
                    continue
                new_p = retarget_peak_to_transmitter(p, landmarks, max_m=250.0)
                if new_p.source_id == p.source_id:
                    break
                positions[i] = new_p
                row.osm_id = new_p.source_id
                row.osm_lat = new_p.lat
                row.osm_lon = new_p.lon
                dist = new_p.extra.get("distance_m")
                dist_s = f"{dist:.0f}m" if isinstance(dist, (int, float)) else "?"
                row.osm_match = (
                    f"retarget:{new_p.extra.get('osm_name') or new_p.source_id} "
                    f"from {p.source_id} ({dist_s})"
                )
                row.match_methods["osm"] = "retarget_peak"
                break

        if ov_osm_ref:
            row.osm_id = ov_osm_ref
            row.osm_match = f"override:{ov_osm_ref}"
            row.match_methods["osm"] = "override"
            if "missing_osm" in row.flags:
                row.flags.remove("missing_osm")
            # Ensure an osm position exists so JOSM fetch/merge can target it.
            if not any(
                p.source_kind == "osm" and p.source_id == ov_osm_ref for p in positions
            ):
                plat = row.osm_lat if row.osm_lat is not None else (
                    local_pos.lat if local_pos else rep.locator_lat
                )
                plon = row.osm_lon if row.osm_lon is not None else (
                    local_pos.lon if local_pos else rep.locator_lon
                )
                if plat is not None and plon is not None:
                    row.osm_lat = plat
                    row.osm_lon = plon
                    positions.append(
                        Position(
                            lat=plat,
                            lon=plon,
                            source_kind="osm",
                            source_id=ov_osm_ref,
                            extra={"osm_match": "override"},
                        )
                    )

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
        if rid:
            row.match_methods["radioid"] = rid_method
            if rid.lat is not None:
                row.radioid_lat = rid.lat
                row.radioid_lon = rid.lon
                positions.append(
                    Position(
                        lat=rid.lat,
                        lon=rid.lon,
                        source_kind="radioid",
                        source_id=rid.source_id,
                    )
                )
            # DMR ID only from same-callsign hits. Frequency+distance fallback can
            # land on a co-channel neighbour (e.g. LA7TR→LD7FF) and must not copy ID.
            if rid.dmr_id and not row.dmr_id and rid_method == "callsign":
                row.dmr_id = rid.dmr_id
        elif _looks_dmr(rep) and radioid_records:
            unmatched_notes.append(f"{rep.callsign}: not found in radioid")
            row.flags.append("missing_radioid")

        rb, rb_method = match_source_records(
            rep, rb_records, freq_tol=freq_tol, freq_max_m=freq_max_m
        )
        if rb:
            row.match_methods["repeaterbook"] = rb_method
            if rb.lat is not None:
                row.repeaterbook_lat = rb.lat
                row.repeaterbook_lon = rb.lon
                positions.append(
                    Position(
                        lat=rb.lat,
                        lon=rb.lon,
                        source_kind="repeaterbook",
                        source_id=rb.source_id,
                    )
                )
            if rb.dmr_id and not row.dmr_id and rb_method == "callsign":
                row.dmr_id = rb.dmr_id
            if rb.tone and not row.tone and rb_method == "callsign":
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
            dedicated_osm_call=bool(dedicated_call),
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
            # Off-air stations are omitted from CSV / OSM / HTCommander / .joz.
            continue

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
    merged = collapse_positionless_duplicates(merged)
    unmatched_notes = sorted(set(unmatched_notes))
    return merged, unmatched_notes
