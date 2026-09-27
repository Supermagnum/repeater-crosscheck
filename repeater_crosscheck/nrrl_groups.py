"""Map NRRL Gruppe names to group pages and club websites from nrrl.no/grupper/."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .cache import RateLimitedSession, ResponseCache
from .util import normalize_qth_name

NRRL_GROUPS_URL = "https://nrrl.no/grupper/"

# Explicit NRRL CSV Gruppe -> directory display name when auto-match is weak.
ALIASES: dict[str, str] = {
    "asker og baerum": "asker og baerum",
    "follo": "follo",
    "gardermoen": "gardermo",
    "romerike": "nedre romerike",
    "ringerike": "ringeriks",
    "alta": "alta",
    "kirkenes": "varanger",
    "gjovik og toten": "gjovik og toten",
    "innlandsnettet": "sambandstjenesten innlandet",
    "mo i rana": "mo",
    "sandnessjoeen": "sandnessjoe",
    "sandnessjoen": "sandnessjoe",
    "haugaland/sunnhordland": "haugaland",
    "haugaland": "haugaland",
    "moss": "mosse",
    "midt-troms": "midt-troms",
    "sore sunnmore": "sore sunnmore",
    "trc trondheim": "trondheims",
    "trondheim": "trondheims",
    "jaeren": "jaer",
    "vesteraalen": "vesteraals",
    "vesteralen": "vesteraals",
}


@dataclass(frozen=True)
class NrrlGroup:
    name: str
    nrrl_url: str
    website: str = ""


def _norm_group(name: str) -> str:
    s = normalize_qth_name(name)
    # "Jærgruppen" has no word boundary before gruppen — strip as suffix too.
    s = re.sub(r"\b(gruppen|gruppa|gruppe|group)\b", " ", s)
    s = re.sub(r"(gruppen|gruppa|gruppe|group)$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_group_directory(session: RateLimitedSession, cache: ResponseCache) -> list[tuple[str, str]]:
    """Return [(display_name, nrrl_page_url), ...] from the groups index."""
    cache_key = "nrrl_groups_index"
    cached = cache.get_json(cache_key)
    if cached is not None:
        return [(str(n), str(u)) for n, u in cached]

    resp = session.request("GET", NRRL_GROUPS_URL)
    resp.raise_for_status()
    html = resp.text
    cache.put_text(cache_key + "_raw", html)
    pairs = re.findall(
        r'<a href=["\'](https://nrrl\.no/grupper/[^"\']+)["\'][^>]*>([^<]+)</a>',
        html,
    )
    # Prefer first occurrence of each URL (skip duplicate Haugaland label).
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for url, name in pairs:
        url = url.rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        clean = re.sub(r"\s+", " ", name).strip()
        # Drop parenthetical from display name for matching, keep URL.
        clean = re.sub(r"\s*\([^)]*\)\s*$", "", clean).strip()
        if clean:
            out.append((clean, url + "/"))
    cache.put_json(cache_key, out)
    return out


def fetch_group_website(
    session: RateLimitedSession,
    cache: ResponseCache,
    nrrl_url: str,
) -> str:
    """Parse Nettside link from an NRRL group detail page."""
    cache_key = "nrrl_group_site_" + re.sub(r"\W+", "_", nrrl_url)[-80:]
    cached = cache.get_json(cache_key)
    if cached is not None:
        return str(cached.get("website") or "")

    resp = session.request("GET", nrrl_url)
    resp.raise_for_status()
    html = resp.text
    cache.put_text(cache_key + "_raw", html)
    website = ""
    m = re.search(
        r"Nettside</strong>\s*:\s*<a href=[\"'](https?://[^\"']+)[\"']",
        html,
        re.IGNORECASE,
    )
    if m:
        website = m.group(1).strip()
    cache.put_json(cache_key, {"nrrl_url": nrrl_url, "website": website})
    return website


def load_nrrl_groups(
    session: RateLimitedSession,
    cache: ResponseCache,
    *,
    fetch_websites: bool = True,
) -> list[NrrlGroup]:
    directory = fetch_group_directory(session, cache)
    groups: list[NrrlGroup] = []
    for name, url in directory:
        website = ""
        if fetch_websites:
            try:
                website = fetch_group_website(session, cache, url)
            except Exception:
                website = ""
        groups.append(NrrlGroup(name=name, nrrl_url=url, website=website))
    return groups


def build_group_lookup(groups: list[NrrlGroup]) -> dict[str, NrrlGroup]:
    """Index by normalized directory name and aliases."""
    by_norm: dict[str, NrrlGroup] = {}
    for g in groups:
        by_norm[_norm_group(g.name)] = g
        # Also index without trailing group word already stripped.
        by_norm[normalize_qth_name(g.name)] = g
    for alias, target in ALIASES.items():
        if target in by_norm:
            by_norm[alias] = by_norm[target]
    return by_norm


def match_gruppe(gruppe: str, lookup: dict[str, NrrlGroup]) -> NrrlGroup | None:
    if not gruppe or not lookup:
        return None
    key = _norm_group(gruppe)
    if key in lookup:
        return lookup[key]
    # Alias table uses same normalization.
    alias_key = normalize_qth_name(gruppe)
    if alias_key in ALIASES:
        target = ALIASES[alias_key]
        if target in lookup:
            return lookup[target]
    if alias_key in lookup:
        return lookup[alias_key]

    # Prefix / containment fallback (deterministic: shortest name win).
    candidates: list[tuple[int, str, NrrlGroup]] = []
    for norm, g in lookup.items():
        if not norm:
            continue
        if key == norm or key in norm or norm in key:
            candidates.append((len(norm), norm, g))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][2]
