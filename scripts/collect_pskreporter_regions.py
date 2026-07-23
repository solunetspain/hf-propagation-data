#!/usr/bin/env python3
"""Regional PSKReporter HF observations for Península, Baleares and Canarias."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.collect_pskreporter import HF_BANDS, band_for, distance_km, maidenhead_center

QUERY_FIELDS = ("IL", "IM", "IN", "JM", "JN")
SPANISH_PREFIXES = ("EA", "EB", "EC", "ED", "EE", "EF", "AM", "AN", "AO")
REGION_ORDER = ("peninsula", "baleares", "canarias")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalized_callsign(value: Any) -> str:
    call = re.sub(r"[^A-Z0-9/]", "", str(value or "").upper())
    parts = [part for part in call.split("/") if part]
    spanish = [part for part in parts if part.startswith(SPANISH_PREFIXES)]
    return spanish[0] if spanish else (parts[0] if parts else "")


def call_area(callsign: Any) -> int | None:
    call = normalized_callsign(callsign)
    for prefix in SPANISH_PREFIXES:
        if call.startswith(prefix):
            suffix = call[len(prefix):]
            match = re.match(r"(\d)", suffix)
            return int(match.group(1)) if match else None
    return None


def locator_region(locator: Any) -> str | None:
    centre = maidenhead_center(str(locator or ""))
    if centre is None:
        return None
    lat, lon = centre
    if 26.0 <= lat <= 30.5 and -19.0 <= lon <= -12.5:
        return "canarias"
    if 38.0 <= lat <= 40.5 and 0.8 <= lon <= 4.8:
        return "baleares"
    if 35.0 <= lat <= 44.5 and -10.0 <= lon <= 4.8:
        return "peninsula"
    return None


def endpoint_region(callsign: Any, locator: Any) -> str | None:
    geographic = locator_region(locator)
    area = call_area(callsign)
    if geographic in ("baleares", "canarias"):
        return geographic
    if area == 6:
        return "baleares"
    if area == 8:
        return "canarias"
    if area in (1, 2, 3, 4, 5, 7):
        return "peninsula"
    return geographic if geographic == "peninsula" and area != 9 else None


def query_url(field: str) -> str:
    params = {
        "callsign": field,
        "modify": "grid",
        "flowStartSeconds": -3600,
        "frange": "1800000-30000000",
        "rptlimit": 5000,
        "rronly": 1,
        "noactive": 1,
        "appcontact": "github.com/solunetspain/hf-propagation-data",
    }
    return "https://retrieve.pskreporter.info/query?" + urllib.parse.urlencode(params)


def dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("flowStartSeconds"),
        row.get("frequency"),
        row.get("senderCallsign"),
        row.get("senderLocator"),
        row.get("receiverCallsign"),
        row.get("receiverLocator"),
        row.get("mode"),
    )


def normalize_report(row: dict[str, Any], now_seconds: float) -> dict[str, Any] | None:
    try:
        timestamp = float(row.get("flowStartSeconds"))
        frequency = float(row.get("frequency"))
    except (TypeError, ValueError):
        return None
    if timestamp < now_seconds - 3600 or timestamp > now_seconds + 300:
        return None
    band = band_for(frequency)
    if band is None:
        return None

    sender_region = endpoint_region(row.get("senderCallsign"), row.get("senderLocator"))
    receiver_region = endpoint_region(row.get("receiverCallsign"), row.get("receiverLocator"))
    regions = sorted({value for value in (sender_region, receiver_region) if value})
    if not regions:
        return None

    if sender_region and receiver_region:
        direction = "internal" if sender_region == receiver_region else "interregional"
    elif receiver_region:
        direction = "received_in_region"
    else:
        direction = "transmitted_from_region"

    return {
        "timestamp_utc": datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
        "frequency_hz": frequency,
        "band": band,
        "mode": str(row.get("mode") or "UNKNOWN").upper(),
        "regions": regions,
        "sender_region": sender_region,
        "receiver_region": receiver_region,
        "direction": direction,
        "sender_callsign": normalized_callsign(row.get("senderCallsign")),
        "receiver_callsign": normalized_callsign(row.get("receiverCallsign")),
        "distance_km": distance_km(
            str(row.get("senderLocator") or ""),
            str(row.get("receiverLocator") or ""),
        ),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_band: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_band[row["band"]].append(row)
    result: dict[str, Any] = {}
    band_order = {name: index for index, (_, _, name) in enumerate(HF_BANDS)}
    for band, items in sorted(by_band.items(), key=lambda pair: band_order[pair[0]]):
        distances = [item["distance_km"] for item in items if item["distance_km"] is not None]
        stations = {
            call
            for item in items
            for call in (item["sender_callsign"], item["receiver_callsign"])
            if call
        }
        routes = {
            (item["sender_callsign"], item["receiver_callsign"])
            for item in items
        }
        result[band] = {
            "report_count": len(items),
            "station_count": len(stations),
            "route_count": len(routes),
            "modes": dict(sorted(Counter(item["mode"] for item in items).items())),
            "directions": dict(sorted(Counter(item["direction"] for item in items).items())),
            "distance_km": {
                "minimum": min(distances) if distances else None,
                "median": round(statistics.median(distances), 1) if distances else None,
                "maximum": max(distances) if distances else None,
            },
            "latest_observation_utc": max(item["timestamp_utc"] for item in items),
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("public/data/pskreporter-hf-regions.json"),
    )
    parser.add_argument(
        "--diagnostic",
        type=Path,
        default=Path("public/diagnostics/pskreporter-regions-diagnostic.json"),
    )
    args = parser.parse_args()

    generated_at = now_iso()
    raw: dict[tuple[Any, ...], dict[str, Any]] = {}
    query_results: dict[str, Any] = {}
    errors: list[str] = []

    for field in QUERY_FIELDS:
        url = query_url(field)
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "SOLUNET-HF-PSKReporter-Regions/1.0"},
            )
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "")
            root = ET.fromstring(body)
            rows = [
                dict(element.attrib)
                for element in root.iter()
                if element.tag.split("}")[-1] == "receptionReport"
            ]
            for row in rows:
                raw[dedupe_key(row)] = row
            query_results[field] = {
                "status": "ok",
                "report_count": len(rows),
                "content_type": content_type,
            }
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{field}: {type(exc).__name__}: {exc}")
            query_results[field] = {"status": "error", "report_count": 0}
        time.sleep(0.25)

    now_seconds = time.time()
    accepted = [
        normalized
        for row in raw.values()
        if (normalized := normalize_report(row, now_seconds)) is not None
    ]
    regions: dict[str, Any] = {}
    successful_queries = sum(1 for item in query_results.values() if item["status"] == "ok")
    consultation_reliability = round(100 * successful_queries / len(QUERY_FIELDS))
    for region in REGION_ORDER:
        rows = [row for row in accepted if region in row["regions"]]
        regions[region] = {
            "status": "ok" if rows else "no_activity_observed",
            "report_count": len(rows),
            "bands": aggregate(rows),
            "observations": rows[:200],
            "consultation_reliability_pct": consultation_reliability,
            "evidence_weight_recommendation": 2 if len(rows) >= 3 else (1 if rows else 0),
        }

    status = "ok" if successful_queries == len(QUERY_FIELDS) else (
        "partial" if successful_queries else "error"
    )
    output = {
        "source": "PSKReporter",
        "generated_at": generated_at,
        "status": status,
        "scope": "Observed HF reports attributable to Península, Baleares or Canarias",
        "classification_method": "Spanish call area with locator cross-check",
        "query_fields": list(QUERY_FIELDS),
        "raw_report_count": len(raw),
        "accepted_report_count": len(accepted),
        "regions": regions,
        "national_fallback": {
            "status": "available" if accepted else "no_activity_observed",
            "report_count": len(accepted),
            "bands": aggregate(accepted),
            "use_only_when_regional_attribution_is_insufficient": True,
        },
    }
    diagnostic = {
        "generated_at": generated_at,
        "status": status,
        "errors": errors,
        "queries": query_results,
        "validation": {
            "response_received": successful_queries > 0,
            "format_parsed": successful_queries > 0,
            "multiple_grid_fields_queried": True,
            "deduplication_applied": True,
            "regional_classification_applied": True,
            "national_fallback_available": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.diagnostic.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if successful_queries else 1


if __name__ == "__main__":
    raise SystemExit(main())
