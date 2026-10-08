from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .cache import RateLimitedSession, ResponseCache
from .config import resolve_path
from .merge import build_merged
from .osm import (
    amateur_elements_to_records,
    fetch_osm_amateur_features,
    fetch_osm_landmarks_for_locators,
    fetch_osm_network_relations,
    relation_member_records,
    relation_members_by_callsign,
)
from .nrrl_groups import build_group_lookup, load_nrrl_groups
from .elevation import enrich_elevations
from .joz_session import write_repeaters_joz
from .htcommander import load_merged_csv, write_htcommander_exports
from .output import write_josm_osm, write_merged_csv, write_unmatched
from .overrides import load_overrides
from .sources_aprsfi import (
    is_aprs_only_type,
    load_aprsfi_locations,
    load_aprsfi_web_json,
)
from .sources_local import (
    load_anytone_channels,
    load_nrrl,
    load_radioid,
    load_repeaterbook,
    load_repeaterbook_json,
)


def run_htcommander_only(cfg: dict, *, base: Path) -> int:
    """Convert existing repeaters_merged.csv to HTCommander formats."""
    paths = cfg["paths"]
    output_dir = resolve_path(paths["output_dir"], base) or (base / "output")
    csv_path = output_dir / "repeaters_merged.csv"
    if not csv_path.is_file():
        print(f"Merged CSV not found: {csv_path}", file=sys.stderr)
        return 2
    rows = load_merged_csv(csv_path)
    print(f"Loaded {len(rows)} rows from {csv_path}")
    write_htcommander_exports(output_dir, rows)
    return 0


def run(cfg: dict, *, base: Path, refresh: bool, args: argparse.Namespace) -> int:
    paths = cfg["paths"]
    nrrl_path = resolve_path(paths["nrrl_csv"], base)
    if nrrl_path is None or not nrrl_path.is_file():
        print(f"NRRL CSV not found: {paths['nrrl_csv']}", file=sys.stderr)
        return 2

    channel_path = resolve_path(paths.get("channel_csv") or "", base)
    zone_path = resolve_path(paths.get("zone_csv") or "", base)
    cache_dir = resolve_path(paths["cache_dir"], base) or (base / "cache")
    output_dir = resolve_path(paths["output_dir"], base) or (base / "output")
    output_dir.mkdir(parents=True, exist_ok=True)

    http_cfg = cfg["http"]
    session = RateLimitedSession(
        user_agent=http_cfg["user_agent"],
        min_interval_s=float(http_cfg["min_interval_s"]),
        timeout_s=float(http_cfg["timeout_s"]),
        retries=int(http_cfg["retries"]),
    )
    cache = ResponseCache(cache_dir, refresh=refresh)

    print(f"Loading NRRL from {nrrl_path}")
    nrrl = load_nrrl(nrrl_path)
    print(f"  {len(nrrl)} repeaters")

    overrides_path = resolve_path(paths.get("overrides") or "", base)
    loaded = load_overrides(overrides_path)
    overrides = loaded.by_callsign
    scrub_osm = list(loaded.scrub_osm)
    if overrides or scrub_osm:
        print(
            f"Loaded {len(overrides)} local overrides"
            + (f", {len(scrub_osm)} scrub OSM refs" if scrub_osm else "")
            + f" from {overrides_path}"
        )

    channels = []
    if channel_path and channel_path.is_file():
        print(f"Loading AnyTone channels from {channel_path}")
        channels = load_anytone_channels(channel_path, zone_path)
        print(f"  {len(channels)} channels")
    elif paths.get("channel_csv"):
        print(f"Warning: channel.csv not found: {channel_path}", file=sys.stderr)

    osm_records = []
    landmarks_by_locator: dict[str, list] = {}
    if not args.skip_osm:
        try:
            print("Fetching OSM amateur-radio features via Overpass...")
            elements = fetch_osm_amateur_features(
                session,
                cache,
                endpoint=cfg["overpass"]["endpoint"],
                bbox=list(cfg["overpass"]["bbox"]),
                min_interval_s=float(http_cfg["overpass_min_interval_s"]),
            )
            osm_records = amateur_elements_to_records(elements)
            print(f"  {len(elements)} elements -> {len(osm_records)} callsign records")

            print("Fetching OSM amateur-radio network/site relations...")
            rel_elements = fetch_osm_network_relations(
                session,
                cache,
                endpoint=cfg["overpass"]["endpoint"],
                bbox=list(cfg["overpass"]["bbox"]),
                min_interval_s=float(http_cfg["overpass_min_interval_s"]),
            )
            grouped = relation_members_by_callsign(rel_elements)
            member_recs = relation_member_records(grouped)
            if member_recs:
                osm_records.extend(member_recs)
            print(
                f"  {len(grouped)} callsign networks, "
                f"{len(member_recs)} member positions"
            )
        except Exception as exc:
            print(f"OSM amateur/network Overpass failed: {exc}", file=sys.stderr)
            traceback.print_exc()
            print("Continuing with whatever OSM data was loaded.", file=sys.stderr)

        try:
            locator_bboxes = {
                rep.locator: rep.locator_bbox
                for rep in nrrl
                if rep.locator and rep.locator_bbox
            }
            print(
                f"Fetching OSM landmarks for {len(locator_bboxes)} locator squares "
                "(batched Overpass queries)..."
            )
            landmarks_by_locator = fetch_osm_landmarks_for_locators(
                session,
                cache,
                endpoint=cfg["overpass"]["endpoint"],
                locator_bboxes=locator_bboxes,
                min_interval_s=float(http_cfg["overpass_min_interval_s"]),
            )
            named = sum(
                1
                for feats in landmarks_by_locator.values()
                for el in feats
                if (el.get("tags") or {}).get("name")
            )
            print(
                f"  landmarks with names across squares: {named} "
                f"(raw features summed {sum(len(v) for v in landmarks_by_locator.values())})"
            )
        except Exception as exc:
            print(f"OSM landmark Overpass failed: {exc}", file=sys.stderr)
            traceback.print_exc()
            print("Continuing without landmark QTH matching.", file=sys.stderr)
    else:
        print("Skipping OSM (--skip-osm)")

    radioid_records = []
    if not args.skip_radioid:
        try:
            print("Fetching radioid.net DMR repeaters for Norway...")
            radioid_records = load_radioid(
                session,
                cache,
                base_url=cfg["radioid"]["base_url"],
                country=cfg["radioid"]["country"],
                api_token=cfg["radioid"].get("api_token") or "",
            )
            print(f"  {len(radioid_records)} records")
        except Exception as exc:
            print(f"radioid.net failed: {exc}", file=sys.stderr)
            traceback.print_exc()
            print("Continuing without radioid data.", file=sys.stderr)
    else:
        print("Skipping radioid (--skip-radioid)")

    rb_records = []
    if not args.skip_repeaterbook:
        token = (cfg["repeaterbook"].get("api_token") or "").strip()
        rb_json = resolve_path(paths.get("repeaterbook_json") or "", base)
        try:
            ua = (cfg["repeaterbook"].get("user_agent") or "").strip() or http_cfg[
                "user_agent"
            ]
            if token:
                print("Fetching RepeaterBook exportROW for Norway...")
            elif rb_json and rb_json.is_file():
                print(f"Loading RepeaterBook from local JSON {rb_json}...")
            else:
                print(
                    "RepeaterBook: no api_token and no paths.repeaterbook_json — "
                    "skipping (token: https://www.repeaterbook.com/api/token_request.php)."
                )
            if token or (rb_json and rb_json.is_file()):
                rb_records = load_repeaterbook(
                    session,
                    cache,
                    base_url=cfg["repeaterbook"]["base_url"],
                    country=cfg["repeaterbook"]["country"],
                    api_token=token,
                    user_agent=ua,
                    local_json=rb_json,
                )
                print(f"  {len(rb_records)} records")
        except Exception as exc:
            print(f"RepeaterBook failed: {exc}", file=sys.stderr)
            traceback.print_exc()
            if rb_json and rb_json.is_file():
                try:
                    print(f"Falling back to {rb_json}...")
                    rb_records = load_repeaterbook_json(rb_json)
                    print(f"  {len(rb_records)} records")
                except Exception as exc2:
                    print(f"Local RepeaterBook JSON failed: {exc2}", file=sys.stderr)
                    print("Continuing without RepeaterBook data.", file=sys.stderr)
            else:
                print("Continuing without RepeaterBook data.", file=sys.stderr)
    else:
        print("Skipping RepeaterBook (--skip-repeaterbook)")

    aprsfi_by_call: dict = {}
    aprsfi_queried = False
    if not getattr(args, "skip_aprsfi", False):
        aprs_cfg_pre = cfg.get("aprsfi") or {}
        aprs_key = (aprs_cfg_pre.get("api_key") or "").strip()
        aprs_json = resolve_path(paths.get("aprsfi_json") or "", base)
        if aprs_key:
            aprs_calls = sorted(
                {
                    r.callsign
                    for r in nrrl
                    if r.callsign and is_aprs_only_type(r.type)
                }
                | {
                    cs
                    for cs, ov in overrides.items()
                    if is_aprs_only_type(ov.type or "APRS")
                }
            )
            print(
                f"Fetching aprs.fi API locations for {len(aprs_calls)} APRS-only callsigns..."
            )
            try:
                aprsfi_by_call = load_aprsfi_locations(
                    session,
                    cache,
                    aprs_calls,
                    api_key=aprs_key,
                    base_url=str(
                        aprs_cfg_pre.get("base_url") or "https://api.aprs.fi/api"
                    ),
                )
                aprsfi_queried = True
                print(
                    f"  {len(aprsfi_by_call)} found on aprs.fi "
                    f"({len(aprs_calls) - len(aprsfi_by_call)} missing)"
                )
            except Exception as exc:
                print(f"aprs.fi API failed: {exc}", file=sys.stderr)
                traceback.print_exc()
                print("Continuing without aprs.fi API data.", file=sys.stderr)
        if not aprsfi_queried and aprs_json and aprs_json.is_file():
            print(f"Loading aprs.fi web lookup from {aprs_json}...")
            try:
                aprsfi_by_call = load_aprsfi_web_json(aprs_json)
                aprsfi_queried = True
                print(f"  {len(aprsfi_by_call)} stations with positions")
            except Exception as exc:
                print(f"aprs.fi JSON failed: {exc}", file=sys.stderr)
                traceback.print_exc()
        elif not aprsfi_queried:
            print(
                "aprs.fi: no api_key and no paths.aprsfi_json — skipping "
                "(https://aprs.fi/page/api)."
            )
    else:
        print("Skipping aprs.fi (--skip-aprsfi)")

    group_lookup: dict = {}
    try:
        print("Fetching NRRL group directory and club websites...")
        groups = load_nrrl_groups(session, cache, fetch_websites=True)
        group_lookup = build_group_lookup(groups)
        with_site = sum(1 for g in groups if g.website)
        print(f"  {len(groups)} groups, {with_site} with Nettside")
    except Exception as exc:
        print(f"NRRL groups failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        print("Continuing without group website mapping.", file=sys.stderr)

    print("Merging...")
    aprs_cfg = cfg.get("aprsfi") or {}
    merged, unmatched = build_merged(
        nrrl,
        osm_records=osm_records,
        landmarks_by_locator=landmarks_by_locator,
        radioid_records=radioid_records,
        rb_records=rb_records,
        aprsfi_by_call=aprsfi_by_call,
        channels=channels,
        thresholds=cfg["thresholds"],
        overrides=overrides,
        group_lookup=group_lookup,
        omit_aprs_missing=bool(aprs_cfg.get("omit_missing", True)),
        aprsfi_queried=aprsfi_queried,
    )

    mh = cfg.get("mapterhorn") or {}
    if not args.skip_elevation and mh.get("enabled", True):
        print("Looking up ground elevation (Mapterhorn Terrarium tiles)...")
        try:
            ok, fail = enrich_elevations(
                merged,
                session,
                cache,
                zoom=int(mh.get("zoom") or 14),
                tile_url_template=str(
                    mh.get("tile_url")
                    or "https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"
                ),
            )
            print(f"  elevation_m set for {ok} rows ({fail} failed / no coords)")
        except Exception as exc:
            print(f"Mapterhorn elevation failed: {exc}", file=sys.stderr)
            traceback.print_exc()
            print("Continuing without elevation_m.", file=sys.stderr)
    elif args.skip_elevation:
        print("Skipping elevation (--skip-elevation)")

    csv_path = output_dir / "repeaters_merged.csv"
    osm_path = output_dir / "repeaters_review.osm"
    unmatched_path = output_dir / "unmatched.txt"

    # OSM first: may snap override best_* onto fetched geometry; CSV must match.
    write_josm_osm(
        osm_path,
        merged,
        disagreement_m=float(cfg["thresholds"]["disagreement_m"]),
        session=session,
        cache=cache,
        scrub_osm=scrub_osm,
    )
    write_merged_csv(csv_path, merged)
    write_unmatched(unmatched_path, unmatched)
    if cfg.get("htcommander", {}).get("enabled") or getattr(
        args, "htcommander", False
    ):
        write_htcommander_exports(output_dir, merged)

    joz_path = output_dir / "repeaters.joz"
    try:
        fylke_layers = write_repeaters_joz(joz_path, osm_path, merged)
        print(
            f"Wrote {joz_path} ({len(fylke_layers)} fylke layers: "
            + ", ".join(fylke_layers)
            + ")"
        )
    except Exception as exc:
        print(f"repeaters.joz failed: {exc}", file=sys.stderr)
        traceback.print_exc()

    print(f"Wrote {csv_path}")
    print(f"Wrote {osm_path}")
    print(f"Wrote {unmatched_path} ({len(unmatched)} notes)")
    return 0
