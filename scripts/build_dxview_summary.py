#!/usr/bin/env python3
"""
Genera un JSON compacto para el informe horario a partir de
data/dxview-in91po.json.

No consulta Internet. Debe ejecutarse DESPUÉS de collect_dxview.py.
Solo usa la biblioteca estándar de Python.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_INPUT = Path("data/dxview-in91po.json")
DEFAULT_OUTPUT = Path("data/dxview-in91po-summary.json")
DEFAULT_DIAGNOSTIC = Path("diagnostics/dxview-summary-diagnostic.json")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("El JSON raíz de DXView no es un objeto.")
    return data


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_present(mapping: dict[str, Any], names: list[str], default: Any = None) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return default


def normalize_sector(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    az = first_present(item, ["azimuth", "azimuth_deg", "sector", "sector_center_deg"])
    count = first_present(item, ["count", "zone_count", "activity_count"], 0)
    minimum = first_present(item, ["min_distance_km", "nearest_km", "distance_min_km"])
    maximum = first_present(item, ["max_distance_km", "farthest_km", "distance_max_km"])
    return {
        "azimuth_deg": as_float(az),
        "count": as_int(count),
        "nearest_km": as_float(minimum),
        "farthest_km": as_float(maximum),
    }


def summarize_band(band_key: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "band_mhz": as_float(band_key),
            "activity_zone_count": 0,
            "muf_zone_count": 0,
            "active_sector_count": 0,
            "digital_sector_count": 0,
            "cw_sector_count": 0,
            "ssb_sector_count": 0,
            "nearest_km": None,
            "farthest_km": None,
            "main_sectors": [],
            "signature": None,
        }

    sectors = first_present(
        payload,
        ["main_sectors", "sectors", "active_sectors", "sector_summary"],
        [],
    )
    normalized_sectors = []
    if isinstance(sectors, list):
        for sector in sectors[:12]:
            normalized = normalize_sector(sector)
            if normalized:
                normalized_sectors.append(normalized)

    return {
        "band_mhz": as_float(first_present(payload, ["band", "band_mhz"], band_key)),
        "activity_zone_count": as_int(first_present(
            payload, ["activity_zone_count", "activity_zones", "active_zone_count", "zone_count"], 0
        )),
        "muf_zone_count": as_int(first_present(
            payload, ["muf_zone_count", "muf_zones", "is_muf_zone_count"], 0
        )),
        "active_sector_count": as_int(first_present(
            payload, ["active_sector_count", "sector_count"], len(normalized_sectors)
        )),
        "digital_sector_count": as_int(first_present(
            payload, ["digital_sector_count", "digital_sectors"], 0
        )),
        "cw_sector_count": as_int(first_present(
            payload, ["cw_sector_count", "cw_sectors"], 0
        )),
        "ssb_sector_count": as_int(first_present(
            payload, ["ssb_sector_count", "ssb_sectors"], 0
        )),
        "nearest_km": as_float(first_present(
            payload, ["nearest_km", "min_distance_km", "distance_min_km"]
        )),
        "farthest_km": as_float(first_present(
            payload, ["farthest_km", "max_distance_km", "distance_max_km"]
        )),
        "main_sectors": normalized_sectors,
        "signature": first_present(payload, ["signature", "activity_signature"]),
    }


def locate_bands(document: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        document.get("bands"),
        document.get("current", {}).get("bands") if isinstance(document.get("current"), dict) else None,
        document.get("activity", {}).get("bands") if isinstance(document.get("activity"), dict) else None,
        document.get("summary", {}).get("bands") if isinstance(document.get("summary"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
        if isinstance(candidate, list):
            result: dict[str, Any] = {}
            for item in candidate:
                if isinstance(item, dict):
                    key = first_present(item, ["band", "band_mhz", "frequency_mhz"])
                    if key is not None:
                        result[str(key)] = item
            if result:
                return result
    return {}


def compact_snapshot(snapshot: Any) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    bands = locate_bands(snapshot)
    compact_bands = {str(k): summarize_band(str(k), v) for k, v in bands.items()}
    return {
        "fetched_at_utc": first_present(
            snapshot, ["fetched_at_utc", "generated_at", "timestamp_utc", "captured_at"]
        ),
        "signature": first_present(snapshot, ["signature", "activity_signature"]),
        "unchanged_from_previous": bool(snapshot.get("unchanged_from_previous", False)),
        "interval_minutes_from_previous": as_float(snapshot.get("interval_minutes_from_previous")),
        "bands": compact_bands,
    }


def trend_direction(first: int, last: int) -> str:
    delta = last - first
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    return "stable"


def calculate_trends(history: list[dict[str, Any]]) -> dict[str, Any]:
    if len(history) < 2:
        return {
            "status": "insufficient_data",
            "message": "No hay datos suficientes para calcular la tendencia",
            "bands": {},
        }

    band_keys: set[str] = set()
    for sample in history:
        band_keys.update(sample.get("bands", {}).keys())

    trends: dict[str, Any] = {}
    for band in sorted(band_keys, key=lambda x: float(x) if str(x).replace(".", "", 1).isdigit() else 999):
        values = []
        sectors = []
        for sample in history:
            band_data = sample.get("bands", {}).get(band)
            if isinstance(band_data, dict):
                values.append(as_int(band_data.get("activity_zone_count")))
                sectors.append(as_int(band_data.get("active_sector_count")))
        if len(values) >= 2:
            trends[band] = {
                "samples": len(values),
                "activity_zone_delta": values[-1] - values[0],
                "active_sector_delta": sectors[-1] - sectors[0],
                "direction": trend_direction(values[0] + sectors[0], values[-1] + sectors[-1]),
            }
        else:
            trends[band] = {
                "samples": len(values),
                "direction": "insufficient_data",
            }

    return {
        "status": "ok",
        "message": None,
        "bands": trends,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--diagnostic", type=Path, default=DEFAULT_DIAGNOSTIC)
    args = parser.parse_args()

    diagnostic: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "status": "error",
        "input": str(args.input),
        "output": str(args.output),
        "errors": [],
        "validation": {
            "input_exists": args.input.exists(),
            "input_parsed": False,
            "bands_found": False,
            "summary_written": False,
        },
    }

    try:
        source = load_json(args.input)
        diagnostic["validation"]["input_parsed"] = True

        bands = locate_bands(source)
        compact_bands = {str(k): summarize_band(str(k), v) for k, v in bands.items()}
        diagnostic["validation"]["bands_found"] = bool(compact_bands)

        source_history = source.get("history", [])
        history: list[dict[str, Any]] = []
        if isinstance(source_history, list):
            for item in source_history[-5:]:
                compact = compact_snapshot(item)
                if compact:
                    history.append(compact)

        perspective = source.get("perspective", {})
        validation = source.get("validation", {})
        endpoint = source.get("endpoint", {})

        summary = {
            "source": "DXView compact summary for hourly HF report",
            "generated_at": utc_now_iso(),
            "source_generated_at": first_present(
                source, ["generated_at", "fetched_at_utc", "timestamp_utc"]
            ),
            "status": "ok" if bool(compact_bands) else "partial",
            "validation": {
                "endpoint_located": bool(validation.get("endpoint_located", False)),
                "response_received": bool(validation.get("response_received", False)),
                "format_parsed": bool(validation.get("format_parsed", False)),
                "current_endpoint_response_checked": bool(
                    validation.get("current_endpoint_response_checked", False)
                ),
                "perspective_bucket_obtained": bool(
                    validation.get("perspective_bucket_obtained", False)
                ),
                "exact_local_value_obtained": bool(
                    validation.get("exact_local_value_obtained", False)
                ),
                "band_variation_verified": bool(
                    validation.get("band_variation_verified", False)
                ),
            },
            "perspective": {
                "grid": first_present(perspective, ["grid", "locator"], "IN91PO"),
                "bucket": perspective.get("bucket"),
                "limitation": perspective.get("limitation"),
                "regional_not_exact_local": True,
            },
            "temporal_limitations": {
                "source_observation_timestamp_available": bool(
                    first_present(
                        endpoint if isinstance(endpoint, dict) else {},
                        ["source_observation_timestamp_available"],
                        source.get("source_observation_timestamp_available", False),
                    )
                ),
                "capture_time_used_as_reference": True,
            },
            "bands": compact_bands,
            "history": history,
            "trend": calculate_trends(history),
        }

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        diagnostic["validation"]["summary_written"] = True
        diagnostic["status"] = "ok" if compact_bands else "partial"
        diagnostic["band_count"] = len(compact_bands)
        diagnostic["history_samples"] = len(history)

    except Exception as exc:  # noqa: BLE001
        diagnostic["errors"].append(f"{type(exc).__name__}: {exc}")

    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return 0 if diagnostic["status"] in {"ok", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
