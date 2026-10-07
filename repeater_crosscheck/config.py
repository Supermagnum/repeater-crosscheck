from __future__ import annotations

import argparse
import copy
import sys
import tomllib
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "paths": {
        "nrrl_csv": "",
        "channel_csv": "",
        "zone_csv": "",
        "overrides": "./overrides.toml",
        "cache_dir": "./cache",
        "output_dir": "./output",
        # Local RepeaterBook exportROW / CHIRP rb-*-all.json (used when no API token).
        "repeaterbook_json": "./data/repeaterbook_norway.json",
    },
    "thresholds": {
        "disagreement_m": 2000,
        "locator_near_m": 500,
        "freq_tolerance_mhz": 0.001,
        "freq_match_max_m": 5000,
        "qth_fuzzy_min": 80,
    },
    "http": {
        "user_agent": "repeater-crosscheck/1.0 (+local personal use; you@example.com)",
        "min_interval_s": 1.5,
        "overpass_min_interval_s": 5.0,
        "timeout_s": 120,
        "retries": 3,
    },
    "radioid": {
        "api_token": "",
        "base_url": "https://radioid.net",
        "country": "Norway",
    },
    "repeaterbook": {
        "api_token": "",
        "base_url": "https://www.repeaterbook.com",
        "country": "Norway",
        # App #114 (RepeaterBook Python Client) — must match registration literally.
        "user_agent": "RepeaterBook Python Client/0.6.0 (+micael@jarniac.dev)",
    },
    "overpass": {
        "endpoint": "https://overpass-api.de/api/interpreter",
        "bbox": [57.5, 4.0, 81.0, 35.0],
    },
    "mapterhorn": {
        # Ground elevation (m ASL) for CSV via Terrarium tiles.
        "enabled": True,
        "zoom": 14,
        "tile_url": "https://tiles.mapterhorn.com/{z}/{x}/{y}.webp",
    },
    "htcommander": {
        # Write CHIRP CSV + VR-N76 regions JSON under output/htcommander/.
        "enabled": False,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(path: Path | None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if path is None:
        return cfg
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return deep_merge(cfg, data)


def resolve_path(value: str, base: Path) -> Path | None:
    if not value or not str(value).strip():
        return None
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (base / p).resolve()
    return p


def apply_cli_overrides(cfg: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    paths = cfg["paths"]
    if args.nrrl:
        paths["nrrl_csv"] = args.nrrl
    if args.channel:
        paths["channel_csv"] = args.channel
    if args.zone:
        paths["zone_csv"] = args.zone
    if args.cache_dir:
        paths["cache_dir"] = args.cache_dir
    if args.output_dir:
        paths["output_dir"] = args.output_dir
    if args.disagreement_m is not None:
        cfg["thresholds"]["disagreement_m"] = args.disagreement_m
    if getattr(args, "htcommander", False):
        cfg.setdefault("htcommander", {})["enabled"] = True
    return cfg


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Cross-check Norwegian amateur radio repeater positions "
            "(NRRL, OSM, radioid.net, RepeaterBook) and write review files."
        )
    )
    p.add_argument(
        "-c",
        "--config",
        default="config.toml",
        help="Path to config.toml (default: ./config.toml)",
    )
    p.add_argument("--nrrl", help="Override NRRL CSV path")
    p.add_argument("--channel", help="Override AnyTone channel.csv path")
    p.add_argument("--zone", help="Override AnyTone zone.csv path")
    p.add_argument("--cache-dir", help="Override cache directory")
    p.add_argument("--output-dir", help="Override output directory")
    p.add_argument(
        "--disagreement-m",
        type=float,
        default=None,
        help="Flag positions farther apart than this many metres",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore local cache and re-download online sources",
    )
    p.add_argument(
        "--skip-osm",
        action="store_true",
        help="Skip Overpass/OSM lookups",
    )
    p.add_argument(
        "--skip-radioid",
        action="store_true",
        help="Skip radioid.net",
    )
    p.add_argument(
        "--skip-repeaterbook",
        action="store_true",
        help="Skip RepeaterBook",
    )
    p.add_argument(
        "--skip-elevation",
        action="store_true",
        help="Skip Mapterhorn ground elevation (CSV elevation_m)",
    )
    p.add_argument(
        "--htcommander",
        action="store_true",
        help=(
            "Write HTCommander exports under output/htcommander/ "
            "(CHIRP CSV + VR-N76 regions JSON with LA5MR / Fylkesnettet groups)"
        ),
    )
    p.add_argument(
        "--htcommander-only",
        action="store_true",
        help=(
            "Only convert existing repeaters_merged.csv to HTCommander formats "
            "(implies --htcommander; skips online sources)"
        ),
    )
    return p


def main(argv: list[str] | None = None) -> int:
    from .pipeline import run, run_htcommander_only

    args = build_arg_parser().parse_args(argv)
    if args.htcommander_only:
        args.htcommander = True

    config_path = Path(args.config).expanduser()
    if not config_path.is_file():
        example = Path("config.example.toml")
        print(
            f"Config not found: {config_path}\n"
            f"Copy {example} to config.toml and edit paths / credentials.",
            file=sys.stderr,
        )
        return 2

    cfg = apply_cli_overrides(load_config(config_path), args)
    base = config_path.resolve().parent
    if args.htcommander_only:
        return run_htcommander_only(cfg, base=base)
    return run(cfg, base=base, refresh=args.refresh, args=args)
