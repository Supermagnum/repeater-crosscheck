"""Map repeater/APRS types to OpenStreetMap amateur-radio tags.

Modulation values follow WARC-79 emission designations used by
communication:amateur_radio:repeater:modulation=*
(see https://wiki.openstreetmap.org/wiki/Key:communication:amateur_radio:repeater).
"""

from __future__ import annotations

import re

from .models import MergedRepeater

# Common OSM / wiki examples for amateur services.
EMISSION_FM_NARROW = "11K2F3E"  # narrow FM voice (12.5 kHz)
EMISSION_DMR = "7K60FXE"  # DMR 2-slot TDMA voice
EMISSION_DSTAR_VOICE = "6K00F7W"  # D-STAR digital voice
EMISSION_C4FM = "9K36F7W"  # Yaesu System Fusion
EMISSION_APRS_AFSK = "20K0F2D"  # 1200 baud AFSK packet / APRS
EMISSION_PACKET = "20K0F2D"
EMISSION_LORA = "12K5F1D"  # best-effort; LoRa CSS has no common WARC short form


def _type_blob(row: MergedRepeater) -> str:
    return (row.type or "").upper()


def emission_designations(row: MergedRepeater) -> list[str]:
    """Ordered unique WARC emission codes for this station."""
    blob = _type_blob(row)
    codes: list[str] = []

    def add(code: str) -> None:
        if code and code not in codes:
            codes.append(code)

    is_lora = "LORA" in blob or "LORA" in blob.replace("Ø", "O")
    is_aprs = "APRS" in blob
    is_packet = any(
        x in blob for x in ("PACKET", "WINLINK", "BPQ", "IVG")
    ) and not is_aprs

    if is_lora and is_aprs:
        add(EMISSION_LORA)
    elif is_aprs:
        add(EMISSION_APRS_AFSK)
    elif is_packet:
        add(EMISSION_PACKET)

    # Voice / digital voice modes (semicolon-separated when multi-mode).
    has_fm = bool(
        re.search(r"\bFM\b", blob)
        or "CROSSBAND" in blob
        or (
            "REPEATER" in blob
            and not any(
                x in blob for x in ("DMR", "DSTAR", "D-STAR", "C4FM", "APRS", "PACKET", "WINLINK")
            )
        )
    )
    # Pure digital repeaters still often include FM in "FM/DMR".
    if re.search(r"\bFM\b", blob) or "CROSSBAND" in blob:
        has_fm = True
    if "IVG" in blob:
        has_fm = True

    has_dmr = "DMR" in blob
    has_dstar = "DSTAR" in blob or "D-STAR" in blob or "D STAR" in blob
    has_c4fm = "C4FM" in blob or "FUSION" in blob or "YSF" in blob

    if has_fm:
        add(EMISSION_FM_NARROW)
    if has_dmr:
        add(EMISSION_DMR)
    if has_dstar:
        add(EMISSION_DSTAR_VOICE)
    if has_c4fm:
        add(EMISSION_C4FM)

    if not codes:
        # Fallback: treat unknown repeater-like rows as narrow FM.
        if any(x in blob for x in ("REPEATER", "LINK", "CROSSBAND")):
            add(EMISSION_FM_NARROW)
    return codes


def _freq_mhz(text: str) -> float | None:
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def _format_mhz(value: float) -> str:
    # OSM Map Features/Units: number, space, unit; '.' as decimal separator.
    s = f"{value:.4f}".rstrip("0").rstrip(".")
    return f"{s} MHz"


def _format_shift(tx_mhz: float, rx_mhz: float) -> str:
    """
    communication:amateur_radio:repeater:shift=*

    Offset added to frequency_out to get the repeater input (can be negative).
    Wiki allows bare Hz or SI units; we use MHz SI form, e.g. '-0.6 MHz'.
    """
    shift_mhz = rx_mhz - tx_mhz
    s = f"{shift_mhz:.6f}".rstrip("0").rstrip(".")
    return f"{s} MHz"


def osm_amateur_tags(row: MergedRepeater) -> dict[str, str]:
    """OSM keys/values for JOSM review (upload only after local verification)."""
    tags: dict[str, str] = {
        "communication:amateur_radio": "yes",
        "communication:amateur_radio:callsign": row.callsign,
    }
    emissions = emission_designations(row)
    if emissions:
        tags["communication:amateur_radio:repeater:modulation"] = ";".join(emissions)

    blob = _type_blob(row)
    is_aprs = "APRS" in blob
    is_link = "LINK" in blob or "CROSSBAND" in blob
    is_packet = any(x in blob for x in ("PACKET", "WINLINK", "BPQ", "IVG"))

    # Digis / packet / voice repeaters are all commonly tagged as repeater=yes
    # on Norwegian masts when they retransmit.
    if emissions or "REPEATER" in blob or is_aprs or is_packet or is_link:
        tags["communication:amateur_radio:repeater"] = "yes"

    tx = _freq_mhz(row.tx)
    rx = _freq_mhz(row.rx)
    if tx is not None:
        tags["communication:amateur_radio:repeater:frequency_out"] = _format_mhz(tx)
        if rx is not None and abs(rx - tx) > 1e-6:
            tags["communication:amateur_radio:repeater:shift"] = _format_shift(tx, rx)

    tone = (row.tone or "").strip()
    if tone.upper().startswith("DCS"):
        m = re.search(r"(\d{2,3})", tone)
        if m:
            tags["communication:amateur_radio:repeater:dcs"] = m.group(1)
    elif tone == "1750":
        tags["communication:amateur_radio:repeater:toneburst"] = "1750"
    elif tone:
        try:
            float(tone)
            tags["communication:amateur_radio:repeater:ctcss"] = tone
        except ValueError:
            pass

    return tags
