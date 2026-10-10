"""Linked Norwegian repeater networks (OSM type=network relations)."""

from __future__ import annotations

from typing import TypedDict


class NetworkDef(TypedDict):
    relation: str  # existing osm ref, or "" to create new in review
    title: str
    comment_tag: str
    callsigns: set[str]
    # Preferred OSM member refs when known (callsign -> node/way/…).
    member_osm: dict[str, str]


# OSM type=network relations for Norwegian linked repeater systems.
NETWORK_RELATIONS: dict[str, NetworkDef] = {
    "LA5MR": {
        "relation": "relation/18780801",
        "title": "LA5MR / Innlandsnettet",
        "comment_tag": "LA5MR",
        "callsigns": {
            "LA5MR",
            "LA5TRR",
            "LA6NR",
            "LA6GR",
            "LA9AR",
            "LA2KRR",
        },
        "member_osm": {},
    },
    "Fylkesnettet": {
        "relation": "relation/18788322",
        "title": "Fylkesnettet Vestfold/Telemark",
        "comment_tag": "Fylkesnettet",
        "callsigns": {
            "LA3GRR",
            "LA5ER",
            "LA3BRR",
            "LA5GR",
            "LA3SRR",
            "LA3XRR",
            "LA6HR",
        },
        "member_osm": {
            "LA3XRR": "node/1964429111",
            "LA3SRR": "node/11540556760",
            "LA3BRR": "node/5417275137",
            "LA3GRR": "node/12640560650",
            "LA5ER": "node/5008797918",
            "LA6HR": "node/14266675291",
            # LA5GR: co-located Vealøs synthetic / local pin (no dedicated OSM yet)
        },
    },
    "Agder net": {
        "relation": "",  # create in review until uploaded
        "title": "Agder net",
        "comment_tag": "Agder net",
        "callsigns": {
            "LA6KR",
            "LA4ARR",
            "LA4ORR",
            "LA6JR",
            "LA6SR",
            "LA5AR",
        },
        "member_osm": {
            "LA6KR": "node/14266724839",
            "LA4ARR": "node/14266724832",
            "LA4ORR": "node/14266724834",
            "LA6JR": "node/14266724838",
            "LA5AR": "node/14266724836",
            "LA6SR": "node/14270013283",
        },
    },
    "Sandnes net": {
        "relation": "",  # create in review until uploaded
        "title": "Sandnes net",
        "comment_tag": "Sandnes net",
        "callsigns": {
            "LA4WRR",
            "LA4SRR",
            "LA4ERR",
        },
        "member_osm": {
            "LA4WRR": "way/628543477",
            "LA4SRR": "node/14266618786",
            "LA4ERR": "node/14266618782",
        },
    },
    "Bergen-voss": {
        "relation": "",  # create in review until uploaded
        "title": "Bergen-voss",
        "comment_tag": "Bergen-voss",
        "callsigns": {
            "LA5CRR",
            "LA5LRR",
            "LA6WR",
        },
        "member_osm": {
            "LA5CRR": "node/14262214693",
            "LA5LRR": "node/5583842945",
            "LA6WR": "node/12666540760",
        },
    },
}


def network_for_callsign(callsign: str) -> tuple[str, NetworkDef] | None:
    cs = (callsign or "").strip().upper()
    if not cs:
        return None
    for key, meta in NETWORK_RELATIONS.items():
        if cs in meta["callsigns"]:
            return key, meta
    return None


def all_network_relation_refs() -> set[str]:
    return {m["relation"] for m in NETWORK_RELATIONS.values() if m.get("relation")}
