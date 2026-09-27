from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass


# Mainland LA-LN plus Svalbard JW / Jan Mayen JX.
CALLSIGN_RE = re.compile(r"\b(?:L[A-N]|JW|JX)[0-9][A-Z]{1,4}\b", re.IGNORECASE)
# Trailing channel / mode suffixes often seen in codeplugs and databases.
SUFFIX_RE = re.compile(r"-(?:B|C|A|R|V|U|\d+)$", re.IGNORECASE)


def normalize_callsign(raw: str | None) -> str:
    if not raw:
        return ""
    text = raw.strip().upper()
    # Prefer an embedded callsign token (e.g. channel name "Alta LA7YR").
    m = CALLSIGN_RE.search(text)
    if m:
        cs = m.group(0).upper()
    else:
        cs = text.replace(" ", "")
    cs = SUFFIX_RE.sub("", cs)
    return cs


def extract_callsigns(text: str | None) -> list[str]:
    if not text:
        return []
    found = CALLSIGN_RE.findall(text.upper())
    out: list[str] = []
    seen: set[str] = set()
    for raw in found:
        cs = normalize_callsign(raw)
        if cs and cs not in seen:
            seen.add(cs)
            out.append(cs)
    return out


def normalize_qth_name(name: str | None) -> str:
    """Casefold, map æøå, strip /suffixes and common noise for comparison."""
    if not name:
        return ""
    s = name.strip()
    # Drop "/Oslo", "/Skien" style city suffixes.
    if "/" in s:
        s = s.split("/", 1)[0]
    s = s.replace("æ", "ae").replace("Æ", "Ae")
    s = s.replace("ø", "oe").replace("Ø", "Oe")
    s = s.replace("å", "aa").replace("Å", "Aa")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.casefold()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Drop weak qualifiers.
    for noise in ("portabel", "portable", "repeater", "relestasjon"):
        s = re.sub(rf"\b{noise}\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class LocatorPosition:
    locator: str
    lat: float
    lon: float
    south: float
    west: float
    north: float
    east: float
    precision_m: float


def maidenhead_to_bbox(locator: str) -> LocatorPosition | None:
    """Convert a Maidenhead locator to centre + bounding box + approximate size."""
    loc = (locator or "").strip().upper()
    if not loc or loc in {"NA", "N/A", "-"}:
        return None
    if len(loc) < 2 or len(loc) % 2:
        return None
    # Validate characters by length pairs.
    try:
        lon = -180.0
        lat = -90.0
        lon_width = 20.0
        lat_width = 10.0

        # Field (A-R)
        lon += (ord(loc[0]) - ord("A")) * 20.0
        lat += (ord(loc[1]) - ord("A")) * 10.0

        if len(loc) >= 4:
            if not (loc[2].isdigit() and loc[3].isdigit()):
                return None
            lon_width = 2.0
            lat_width = 1.0
            lon += int(loc[2]) * lon_width
            lat += int(loc[3]) * lat_width

        if len(loc) >= 6:
            if not (loc[4].isalpha() and loc[5].isalpha()):
                return None
            lon_width = 2.0 / 24.0
            lat_width = 1.0 / 24.0
            lon += (ord(loc[4]) - ord("A")) * lon_width
            lat += (ord(loc[5]) - ord("A")) * lat_width

        if len(loc) >= 8:
            if not (loc[6].isdigit() and loc[7].isdigit()):
                return None
            lon_width = lon_width / 10.0
            lat_width = lat_width / 10.0
            lon += int(loc[6]) * lon_width
            lat += int(loc[7]) * lat_width

        if len(loc) > 8:
            # Ignore finer extensions; treat as 8-char square.
            pass

        south, west = lat, lon
        north, east = lat + lat_width, lon + lon_width
        centre_lat = (south + north) / 2.0
        centre_lon = (west + east) / 2.0
        # Approximate square size as mean of width/height in metres at centre.
        width_m = haversine_m(centre_lat, west, centre_lat, east)
        height_m = haversine_m(south, centre_lon, north, centre_lon)
        precision_m = (width_m + height_m) / 2.0
        return LocatorPosition(
            locator=loc[:8],
            lat=centre_lat,
            lon=centre_lon,
            south=south,
            west=west,
            north=north,
            east=east,
            precision_m=precision_m,
        )
    except (ValueError, IndexError):
        return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def point_in_bbox(
    lat: float,
    lon: float,
    south: float,
    west: float,
    north: float,
    east: float,
    margin_m: float = 0.0,
) -> bool:
    if margin_m > 0:
        # Expand bbox by approximate degrees.
        mid_lat = (south + north) / 2.0
        dlat = margin_m / 111_320.0
        dlon = margin_m / (111_320.0 * max(math.cos(math.radians(mid_lat)), 0.01))
        south -= dlat
        north += dlat
        west -= dlon
        east += dlon
    return south <= lat <= north and west <= lon <= east


def fmt_coord(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"


_DMR_ID_RE = re.compile(r"(?:ID\s*:?\s*)(\d{5,7})", re.IGNORECASE)


def parse_nrrl_info(info: str | None) -> tuple[str, str]:
    """Return (tone, dmr_id) parsed from NRRL Info text."""
    if not info:
        return "", ""
    text = info.strip()
    dmr_id = ""
    m_id = _DMR_ID_RE.search(text)
    if m_id:
        dmr_id = m_id.group(1)

    # Remove ID portion before tone scan to avoid grabbing digits from the ID.
    tone_src = _DMR_ID_RE.sub(" ", text)
    tone_src = re.sub(r"\bQRT\b", " ", tone_src, flags=re.IGNORECASE)
    tone_src = re.sub(r"TX-RX|RX-TX", " ", tone_src, flags=re.IGNORECASE)

    tone = ""
    # Prefer explicit DCS or dotted CTCSS, then 1750, then integer CTCSS.
    m_dcs = re.search(r"DCS-?(\d{2,3})", tone_src, re.IGNORECASE)
    if m_dcs:
        tone = f"DCS-{m_dcs.group(1)}"
    else:
        m_dot = re.search(r"\b(\d{2,3}\.\d)\b", tone_src)
        if m_dot:
            tone = m_dot.group(1)
        elif re.search(r"\b1750\b", tone_src):
            tone = "1750"
        else:
            m_int = re.search(r"\b(\d{2,3})\b", tone_src)
            if m_int:
                # Avoid treating DMR-ish leftovers as tones; common CTCSS set.
                cand = m_int.group(1)
                common = {
                    "67", "69", "71", "74", "77", "79", "82", "85", "88", "91",
                    "94", "97", "100", "103", "107", "110", "114", "118", "123",
                    "127", "131", "136", "141", "146", "151", "156", "162",
                    "167", "173", "179", "186", "192", "203", "210", "218",
                    "225", "233", "241", "250",
                }
                # Also accept xx.x truncated integers that appear bare.
                if cand in common or cand in {"71", "74", "88", "91"}:
                    tone = cand
                elif len(cand) <= 3 and int(cand) <= 254:
                    # Accept other plausible CTCSS integers.
                    tone = cand

    return tone, dmr_id
