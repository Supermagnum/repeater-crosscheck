"""Map NRRL groups / callsigns to Norwegian fylke (county) names."""

from __future__ import annotations

from .models import MergedRepeater

# NRRL group URL path segment -> fylkesnavn
FYLKE_FROM_SLUG: dict[str, str] = {
    "ostfold": "Østfold",
    "akershus": "Akershus",
    "oslo": "Oslo",
    "innlandet": "Innlandet",
    "buskerud": "Buskerud",
    "vestfold": "Vestfold",
    "telemark": "Telemark",
    "agder": "Agder",
    "rogaland": "Rogaland",
    "vestland": "Vestland",
    "more-og-romsdal": "Møre og Romsdal",
    "trondelag": "Trøndelag",
    "nordland": "Nordland",
    "troms": "Troms",
    "finnmark": "Finnmark",
    "svalbard-og-jan-mayen": "Svalbard og Jan Mayen",
}

# NRRL Gruppe label when no group_nrrl_url
FYLKE_FROM_GROUP: dict[str, str] = {
    "la1trf": "Akershus",
    "viken radioforening": "Akershus",
    "stf sauda": "Rogaland",
    "risør": "Agder",
    "risor": "Agder",
    "nord-hordland": "Vestland",
    "voss": "Vestland",
    "andøy": "Nordland",
    "andoy": "Nordland",
    "orf": "Troms",
    "florø": "Vestland",
    "floro": "Vestland",
    "ham-tech": "Oslo",
}

# Layer order (Innlandet first for review focus).
FYLKE_LAYER_ORDER: list[str] = [
    "Innlandet",
    "Østfold",
    "Akershus",
    "Oslo",
    "Buskerud",
    "Vestfold",
    "Telemark",
    "Agder",
    "Rogaland",
    "Vestland",
    "Møre og Romsdal",
    "Trøndelag",
    "Nordland",
    "Troms",
    "Finnmark",
    "Svalbard og Jan Mayen",
    "Unknown",
]


def fylke_slug(name: str) -> str:
    """Filesystem-safe layer folder name."""
    table = str.maketrans(
        {
            "Ø": "o",
            "ø": "o",
            "Å": "a",
            "å": "a",
            "Æ": "ae",
            "æ": "ae",
            " ": "-",
        }
    )
    return name.translate(table).lower()


def fylke_for_row(row: MergedRepeater) -> str:
    url = row.group_nrrl_url or ""
    parts = url.rstrip("/").split("/")
    if "grupper" in parts:
        i = parts.index("grupper")
        if i + 1 < len(parts):
            slug = parts[i + 1]
            return FYLKE_FROM_SLUG.get(slug, slug.replace("-", " ").title())
    g = (row.group or "").strip().lower()
    return FYLKE_FROM_GROUP.get(g, "Unknown")


def callsign_fylke_map(rows: list[MergedRepeater]) -> dict[str, str]:
    """callsign -> fylkesnavn (first non-portable row wins)."""
    out: dict[str, str] = {}
    for row in rows:
        if "portable" in row.flags:
            continue
        cs = (row.callsign or "").upper()
        if not cs or cs in out:
            continue
        out[cs] = fylke_for_row(row)
    return out
