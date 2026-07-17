#!/usr/bin/env python3
"""Conservative, locally filtered PSKReporter observations for IN91 HF."""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HF_BANDS = [
    (1_800_000, 2_000_000, "160m"),
    (3_500_000, 4_000_000, "80m"),
    (5_000_000, 5_500_000, "60m"),
    (7_000_000, 7_300_000, "40m"),
    (10_100_000, 10_150_000, "30m"),
    (14_000_000, 14_350_000, "20m"),
    (18_068_000, 18_168_000, "17m"),
    (21_000_000, 21_450_000, "15m"),
    (24_890_000, 24_990_000, "12m"),
    (28_000_000, 29_700_000, "10m"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_query_url() -> tuple[str, dict[str, Any]]:
    """Build the documented PSKReporter grid query for IN91.

    PSKReporter's retrieval API does not define a ``receiverLocator``
    parameter. Grid searches use ``callsign=<grid>&modify=grid`` and may return
    reports where either endpoint is in the requested grid. We still apply the
    strict local, time and amateur-band filters after parsing.
    """
    params: dict[str, Any] = {
        "callsign": "IN91",
        "modify": "grid",
        "flowStartSeconds": -3600,
        "frange": "1800000-30000000",
        "rptlimit": 5000,
        "rronly": 1,
        "noactive": 1,
    }
    return (
        "https://retrieve.pskreporter.info/query?"
        + urllib.parse.urlencode(params),
        params,
    )


def band_for(frequency_hz: float) -> str | None:
    for minimum, maximum, name in HF_BANDS:
        if minimum <= frequency_hz <= maximum:
            return name
    return None


def maidenhead_center(locator: str) -> tuple[float, float] | None:
    """Return the centre of a four- or six-character Maidenhead locator."""
    locator = str(locator or "").strip().upper()
    if len(locator) < 4:
        return None
    locator = locator[:6] if len(locator) >= 6 else locator[:4]
    if not ("A" <= locator[0] <= "R" and "A" <= locator[1] <= "R"):
        return None
    if not locator[2:4].isdigit():
        return None
    lon = (ord(locator[0]) - ord("A")) * 20.0 - 180.0 + int(locator[2]) * 2.0
    lat = (ord(locator[1]) - ord("A")) * 10.0 - 90.0 + int(locator[3])
    lon_size, lat_size = 2.0, 1.0
    if len(locator) == 6:
        if not ("A" <= locator[4] <= "X" and "A" <= locator[5] <= "X"):
            return None
        lon += (ord(locator[4]) - ord("A")) / 12.0
        lat += (ord(locator[5]) - ord("A")) / 24.0
        lon_size, lat_size = 1.0 / 12.0, 1.0 / 24.0
    return lat + lat_size / 2.0, lon + lon_size / 2.0


def distance_km(first: str, second: str) -> float | None:
    a = maidenhead_center(first)
    b = maidenhead_center(second)
    if a is None or b is None:
        return None
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return round(6371.0088 * 2 * math.asin(min(1.0, math.sqrt(value))), 1)


def filter_reports(
    reports: list[dict[str, Any]], now_seconds: float | None = None
) -> tuple[list[dict[str, Any]], Counter[str]]:
    """Apply local locator, time and amateur-HF filters locally."""
    now_seconds = time.time() if now_seconds is None else now_seconds
    accepted: list[dict[str, Any]] = []
    rejected: Counter[str] = Counter()
    for report in reports:
        sender_locator = str(report.get("senderLocator", "")).strip().upper()
        receiver_locator = str(report.get("receiverLocator", "")).strip().upper()
        sender_local = sender_locator.startswith("IN91")
        receiver_local = receiver_locator.startswith("IN91")
        if not sender_local and not receiver_local:
            rejected["outside_IN91"] += 1
            continue
        try:
            timestamp = float(report.get("flowStartSeconds"))
        except (TypeError, ValueError):
            rejected["invalid_timestamp"] += 1
            continue
        if timestamp < now_seconds - 3600 or timestamp > now_seconds + 300:
            rejected["outside_one_hour_window"] += 1
            continue
        try:
            frequency = float(report.get("frequency"))
        except (TypeError, ValueError):
            rejected["invalid_frequency"] += 1
            continue
        if not 1_800_000 <= frequency <= 30_000_000:
            rejected["outside_HF"] += 1
            continue
        band = band_for(frequency)
        if band is None:
            rejected["outside_supported_amateur_band"] += 1
            continue

        if sender_local and receiver_local:
            direction = "both_endpoints_IN91"
        elif receiver_local:
            direction = "received_in_IN91"
        else:
            direction = "transmitted_from_IN91"
        accepted.append(
            {
                "timestamp_utc": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                "frequency_hz": frequency,
                "band": band,
                "mode": str(report.get("mode", "unknown")).strip().upper() or "UNKNOWN",
                "direction": direction,
                "sender_callsign": report.get("senderCallsign"),
                "sender_locator": sender_locator or None,
                "receiver_callsign": report.get("receiverCallsign"),
                "receiver_locator": receiver_locator or None,
                "snr_db": report.get("sNR"),
                "distance_km": distance_km(sender_locator, receiver_locator),
            }
        )
    accepted.sort(key=lambda item: item["timestamp_utc"], reverse=True)
    return accepted, rejected


def aggregate_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for report in reports:
        grouped[report["band"]].append(report)
    result: dict[str, Any] = {}
    for band, rows in grouped.items():
        distances = [row["distance_km"] for row in rows if row["distance_km"] is not None]
        callsigns = {
            str(value)
            for row in rows
            for value in (row.get("sender_callsign"), row.get("receiver_callsign"))
            if value
        }
        result[band] = {
            "report_count": len(rows),
            "station_count": len(callsigns),
            "modes": dict(sorted(Counter(row["mode"] for row in rows).items())),
            "directions": dict(sorted(Counter(row["direction"] for row in rows).items())),
            "distance_km": {
                "minimum": min(distances) if distances else None,
                "median": round(statistics.median(distances), 1) if distances else None,
                "maximum": max(distances) if distances else None,
            },
            "latest_observation_utc": max(row["timestamp_utc"] for row in rows),
        }
    return dict(sorted(result.items(), key=lambda item: HF_BANDS.index(next(row for row in HF_BANDS if row[2] == item[0]))))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("public/data/pskreporter-hf-summary.json"))
    parser.add_argument("--diagnostic", type=Path, default=Path("public/diagnostics/pskreporter-diagnostic.json"))
    args = parser.parse_args()

    url, params = build_query_url()
    output: dict[str, Any] = {
        "source": "PSKReporter",
        "generated_at": now_iso(),
        "status": "partial",
        "query_url": url,
        "query_parameters": params,
        "query_strategy": "Official PSKReporter grid query: callsign=IN91 and modify=grid",
        "scope": "Reports with sender or receiver locator beginning IN91, amateur HF, last hour",
        "classification": "Observed reports after strict local post-filtering",
        "bands": {},
        "examples": [],
    }
    diagnostic: dict[str, Any] = {
        "generated_at": now_iso(),
        "status": "partial",
        "errors": [],
        "validation": {
            "response_received": False,
            "format_parsed": False,
            "local_filter_applied": False,
            "hf_filter_applied": False,
            "one_hour_filter_applied": False,
            "local_hf_reports_obtained": False,
            "official_grid_query_used": True,
        },
    }
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "SOLUNET-HF-PSKReporter/2.0"})
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
        diagnostic["validation"]["response_received"] = True
        root = ET.fromstring(body)
        diagnostic["validation"]["format_parsed"] = True
        raw_reports = [
            dict(element.attrib)
            for element in root.iter()
            if element.tag.split("}")[-1] == "receptionReport"
        ]
        reports, rejected = filter_reports(raw_reports)
        diagnostic["validation"].update(
            {
                "local_filter_applied": True,
                "hf_filter_applied": True,
                "one_hour_filter_applied": True,
                "local_hf_reports_obtained": bool(reports),
            }
        )
        query_honored = all(
            str(report.get("receiverLocator", "")).upper().startswith("IN91")
            or str(report.get("senderLocator", "")).upper().startswith("IN91")
            for report in raw_reports
        ) if raw_reports else False
        output.update(
            {
                "content_type": content_type,
                "upstream_report_count": len(raw_reports),
                "accepted_report_count": len(reports),
                "rejected_report_counts": dict(sorted(rejected.items())),
                "upstream_grid_filter_honored": query_honored,
                "upstream_filter_note": (
                    "Grid query may match either sender or receiver in IN91; "
                    "strict local post-filter remains mandatory."
                ),
                "bands": aggregate_reports(reports),
                "examples": reports[:20],
                "status": "ok" if reports else "partial",
                "weight_recommendation": 1 if reports else 0,
                "limitation": None if reports else "No local IN91 amateur-HF reports found after strict filtering.",
            }
        )
    except Exception as exc:  # noqa: BLE001
        diagnostic["errors"].append(f"{type(exc).__name__}: {exc}")
        output["weight_recommendation"] = 0
        output["limitation"] = "PSKReporter response was unavailable or unparseable."

    diagnostic["status"] = output["status"]
    diagnostic["weight_recommendation"] = output.get("weight_recommendation", 0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.diagnostic.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
