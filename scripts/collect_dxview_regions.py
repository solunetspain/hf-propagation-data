#!/usr/bin/env python3
"""Region-sampled DXView HF activity for Península, Baleares and Canarias."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from scripts.collect_dxview import (
    ENDPOINT,
    fetch_band,
    perspective_bucket,
    response_signature,
    summarize_zones,
)

BANDS = (0, 3, 7, 14, 18, 21, 24, 28)
REGION_SAMPLES = {
    "peninsula": (
        ("northwest", 43.0, -8.0),
        ("north", 43.2, -3.5),
        ("centre", 40.4, -3.7),
        ("east", 40.3, 0.3),
        ("southwest", 37.3, -6.5),
        ("south", 37.0, -3.5),
    ),
    "baleares": (("archipelago", 39.5, 2.7),),
    "canarias": (("archipelago", 28.3, -15.8),),
}
PREVIOUS_URL = (
    "https://raw.githubusercontent.com/solunetspain/hf-propagation-data/"
    "generated-data/public/data/dxview-regions-summary.json"
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sample_definitions() -> tuple[dict[str, Any], dict[int, tuple[float, float]]]:
    regions: dict[str, Any] = {}
    unique: dict[int, tuple[float, float]] = {}
    for region, samples in REGION_SAMPLES.items():
        region_rows = []
        for name, lat, lon in samples:
            bucket = perspective_bucket(lat, lon)
            bucket_id = int(bucket["id"])
            unique.setdefault(bucket_id, (lat, lon))
            region_rows.append(
                {
                    "sample": name,
                    "bucket_id": bucket_id,
                    "bucket": bucket,
                    "origin": {"latitude": lat, "longitude": lon},
                }
            )
        regions[region] = region_rows
    return regions, unique


def numeric_summary(values: list[int]) -> dict[str, Any]:
    return {
        "minimum": min(values) if values else None,
        "median": round(statistics.median(values), 1) if values else None,
        "maximum": max(values) if values else None,
    }


def aggregate_region(
    samples: list[dict[str, Any]],
    fetched: dict[int, dict[int, dict[str, Any]]],
) -> dict[str, Any]:
    bands: dict[str, Any] = {}
    for band in BANDS:
        views = []
        for sample in samples:
            bucket_id = sample["bucket_id"]
            entry = fetched.get(bucket_id, {}).get(band)
            if entry:
                views.append(entry)
        activity = [
            int(view["summary"]["classification"].get("activity", 0))
            for view in views
        ]
        muf = [
            int(view["summary"]["classification"].get("muf", 0))
            for view in views
        ]
        modes = Counter()
        sectors = Counter()
        for view in views:
            for mode, count in view["summary"].get("mode_zone_counts", {}).items():
                if count:
                    modes[mode] += 1
            for sector, values in view["summary"].get("sectors", {}).items():
                if values.get("activity_zones", 0):
                    sectors[sector] += 1
        bands[str(band)] = {
            "view_count": len(views),
            "unique_response_count": len({view["signature"] for view in views}),
            "active_view_count": sum(value > 0 for value in activity),
            "activity_zone_count": numeric_summary(activity),
            "muf_zone_count": numeric_summary(muf),
            "mode_view_counts": dict(sorted(modes.items())),
            "main_sectors": [
                {"sector": name, "active_view_count": count}
                for name, count in sectors.most_common(6)
            ],
            "classification": "observed_regional_sample",
        }
    available_views = sum(
        1
        for sample in samples
        if sample["bucket_id"] in fetched and fetched[sample["bucket_id"]]
    )
    return {
        "status": "ok" if available_views == len(samples) else (
            "partial" if available_views else "error"
        ),
        "sample_count": len(samples),
        "available_sample_count": available_views,
        "coverage_method": "multiple DXView perspective buckets",
        "bands": bands,
    }


def load_previous() -> dict[str, Any] | None:
    try:
        response = requests.get(
            PREVIOUS_URL,
            params={"nocache": int(time.time())},
            timeout=20,
            headers={"Accept": "application/json"},
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def slot(value: datetime) -> str:
    minute = (value.minute // 15) * 15
    return value.replace(minute=minute, second=0, microsecond=0).isoformat()


def compact_snapshot(generated_at: str, regions: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "slot_start_utc": slot(datetime.fromisoformat(generated_at)),
        "regions": {
            region: {
                "bands": {
                    band: {
                        "active_view_count": values.get("active_view_count", 0),
                        "activity_zone_median": values.get("activity_zone_count", {}).get("median"),
                    }
                    for band, values in data.get("bands", {}).items()
                }
            }
            for region, data in regions.items()
        },
    }


def history_with(previous: dict[str, Any] | None, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    if previous and isinstance(previous.get("history"), list):
        rows = [row for row in previous["history"] if isinstance(row, dict)]
    by_slot = {
        row.get("slot_start_utc"): row
        for row in rows
        if row.get("slot_start_utc")
    }
    by_slot[snapshot["slot_start_utc"]] = snapshot
    return [by_slot[key] for key in sorted(by_slot)][-5:]


def history_quality(history: list[dict[str, Any]]) -> dict[str, Any]:
    slots = []
    for row in history:
        try:
            slots.append(datetime.fromisoformat(row["slot_start_utc"]))
        except (KeyError, TypeError, ValueError):
            pass
    intervals = [
        round((current - previous).total_seconds() / 60)
        for previous, current in zip(slots, slots[1:])
    ]
    contiguous = len(slots) >= 4 and all(value == 15 for value in intervals[-3:])
    return {
        "status": "valid" if contiguous else "insufficient_data",
        "valid_for_trend": contiguous,
        "samples": len(slots),
        "required_contiguous_samples": 4,
        "message": None if contiguous else "No hay datos suficientes para calcular la tendencia",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("public/data/dxview-regions-summary.json"),
    )
    parser.add_argument(
        "--diagnostic",
        type=Path,
        default=Path("public/diagnostics/dxview-regions-diagnostic.json"),
    )
    args = parser.parse_args()

    generated_dt = utcnow()
    generated_at = generated_dt.isoformat()
    region_samples, unique_buckets = sample_definitions()
    fetched: dict[int, dict[int, dict[str, Any]]] = {}
    errors: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}

    for bucket_id, (lat, lon) in unique_buckets.items():
        fetched[bucket_id] = {}
        for band in BANDS:
            try:
                payload, meta = fetch_band(band, bucket_id)
                summary = summarize_zones(payload["zones"], lat, lon)
                fetched[bucket_id][band] = {
                    "signature": response_signature(payload),
                    "summary": summary,
                }
                metadata[f"{bucket_id}:{band}"] = {
                    "status_code": meta.get("status_code"),
                    "content_type": meta.get("content_type"),
                    "cache_control": meta.get("cache_control"),
                }
            except Exception as exc:  # noqa: BLE001
                errors.append({"bucket_id": bucket_id, "band": band, "error": str(exc)})
            time.sleep(0.15)

    regions = {
        region: aggregate_region(samples, fetched)
        for region, samples in region_samples.items()
    }
    successful_responses = sum(len(values) for values in fetched.values())
    expected_responses = len(unique_buckets) * len(BANDS)
    previous = load_previous()
    snapshot = compact_snapshot(generated_at, regions)
    history = history_with(previous, snapshot)
    quality = history_quality(history)

    status = "ok" if successful_responses == expected_responses else (
        "partial" if successful_responses else "error"
    )
    national_bands: dict[str, Any] = {}
    for band in BANDS:
        regional_values = [
            region["bands"][str(band)]["activity_zone_count"]["median"]
            for region in regions.values()
            if region["bands"][str(band)]["activity_zone_count"]["median"] is not None
        ]
        national_bands[str(band)] = {
            "regional_sample_count": len(regional_values),
            "activity_zone_median": (
                round(statistics.median(regional_values), 1)
                if regional_values else None
            ),
        }

    output = {
        "source": "DXView",
        "generated_at": generated_at,
        "status": status,
        "endpoint": ENDPOINT,
        "scope": "Region-sampled observed HF activity",
        "regions": regions,
        "national_fallback": {
            "status": "available" if successful_responses else "error",
            "bands": national_bands,
            "use_only_when_regional_sampling_is_insufficient": True,
        },
        "history": history,
        "history_quality": quality,
        "limitations": [
            "DXView perspectives are coarse 4 by 6 degree buckets.",
            "Baleares shares a perspective bucket with part of eastern mainland Spain.",
            "Regional values are representative samples, not complete territorial censuses.",
        ],
    }
    diagnostic = {
        "generated_at": generated_at,
        "status": status,
        "errors": errors,
        "validation": {
            "response_received": successful_responses > 0,
            "format_parsed": successful_responses > 0,
            "multiple_perspectives_queried": len(unique_buckets) > 1,
            "peninsula_multiple_samples": len(region_samples["peninsula"]) > 1,
            "baleares_sample_available": regions["baleares"]["available_sample_count"] > 0,
            "canarias_sample_available": regions["canarias"]["available_sample_count"] > 0,
            "national_fallback_available": successful_responses > 0,
        },
        "response_count": successful_responses,
        "expected_response_count": expected_responses,
        "response_metadata": metadata,
        "sample_buckets": {
            region: [
                {
                    "sample": item["sample"],
                    "bucket_id": item["bucket_id"],
                    "bucket": item["bucket"],
                }
                for item in samples
            ]
            for region, samples in region_samples.items()
        },
        "output_signature": hashlib.sha256(
            json.dumps(output["regions"], sort_keys=True).encode("utf-8")
        ).hexdigest()[:16],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.diagnostic.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0 if successful_responses else 1


if __name__ == "__main__":
    raise SystemExit(main())
