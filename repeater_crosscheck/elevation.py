"""Ground elevation (metres above mean sea level) via Mapterhorn Terrarium tiles."""

from __future__ import annotations

import io
import math
from typing import Iterable

from PIL import Image

from .cache import RateLimitedSession, ResponseCache
from .models import MergedRepeater

DEFAULT_TILE_URL = "https://tiles.mapterhorn.com/{z}/{x}/{y}.webp"
DEFAULT_ZOOM = 14
TILE_SIZE = 512  # Mapterhorn Terrarium tiles are 512 px


def latlon_to_tile_pixel(
    lat: float, lon: float, zoom: int, *, tile_size: int = TILE_SIZE
) -> tuple[int, int, int, int]:
    """Return (tile_x, tile_y, pixel_x, pixel_y) in XYZ / Web Mercator."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2**zoom
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n
    tile_x = int(math.floor(x))
    tile_y = int(math.floor(y))
    # Clamp to valid tile indices.
    tile_x = max(0, min(tile_x, n - 1))
    tile_y = max(0, min(tile_y, n - 1))
    px = int((x - tile_x) * tile_size)
    py = int((y - tile_y) * tile_size)
    px = max(0, min(px, tile_size - 1))
    py = max(0, min(py, tile_size - 1))
    return tile_x, tile_y, px, py


def terrarium_elevation_m(r: int, g: int, b: int) -> float:
    """Decode Terrarium RGB to metres (orthometric / approx. ASL)."""
    return (r * 256.0 + g + b / 256.0) - 32768.0


def _fetch_tile(
    session: RateLimitedSession,
    cache: ResponseCache,
    *,
    z: int,
    x: int,
    y: int,
    tile_url_template: str,
) -> Image.Image:
    cache_key = f"mapterhorn_tile_z{z}_x{x}_y{y}"
    raw = cache.get_bytes(cache_key, suffix=".webp")
    if raw is None:
        url = (
            tile_url_template.replace("{z}", str(z))
            .replace("{x}", str(x))
            .replace("{y}", str(y))
        )
        resp = session.request("GET", url, min_interval_s=0.2)
        resp.raise_for_status()
        raw = resp.content
        cache.put_bytes(cache_key, raw, suffix=".webp")
    return Image.open(io.BytesIO(raw)).convert("RGB")


def elevation_at(
    session: RateLimitedSession,
    cache: ResponseCache,
    lat: float,
    lon: float,
    *,
    zoom: int = DEFAULT_ZOOM,
    tile_url_template: str = DEFAULT_TILE_URL,
) -> float:
    """Return ground elevation in metres ASL at lat/lon from Mapterhorn."""
    tx, ty, px, py = latlon_to_tile_pixel(lat, lon, zoom)
    img = _fetch_tile(
        session,
        cache,
        z=zoom,
        x=tx,
        y=ty,
        tile_url_template=tile_url_template,
    )
    r, g, b = img.getpixel((px, py))
    return terrarium_elevation_m(int(r), int(g), int(b))


def enrich_elevations(
    rows: Iterable[MergedRepeater],
    session: RateLimitedSession,
    cache: ResponseCache,
    *,
    zoom: int = DEFAULT_ZOOM,
    tile_url_template: str = DEFAULT_TILE_URL,
) -> tuple[int, int]:
    """
    Set ``elevation_m`` on each row from ``best_lat``/``best_lon`` (Mapterhorn).

    Returns (ok_count, fail_count). Shared coordinates reuse one lookup.
    """
    memo: dict[tuple[float, float], float | None] = {}
    ok = fail = 0
    for row in rows:
        if row.best_lat is None or row.best_lon is None:
            row.elevation_m = None
            continue
        key = (round(row.best_lat, 5), round(row.best_lon, 5))
        if key not in memo:
            try:
                memo[key] = elevation_at(
                    session,
                    cache,
                    row.best_lat,
                    row.best_lon,
                    zoom=zoom,
                    tile_url_template=tile_url_template,
                )
            except Exception:
                memo[key] = None
        elev = memo[key]
        row.elevation_m = elev
        if elev is None:
            fail += 1
        else:
            ok += 1
    return ok, fail
