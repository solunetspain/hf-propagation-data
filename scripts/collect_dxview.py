from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

ENDPOINT = "https://hf.dxview.org/map/refresh"
PERSPECTIVE_URL = "https://hf.dxview.org/perspective/IN91PO"
PREVIOUS_URL = (
    "https://solunetspain.github.io/hf-propagation-data/"
    "data/dxview-in91po.json"
)

GRID = "IN91PO"
BANDS = [0, 1, 3, 5, 7, 10, 14, 18, 21, 24, 28, 50]
PUBLIC = Path("public")
DATA = PUBLIC / "data"
DIAG = PUBLIC / "diagnostics"
HISTORY_SLOT_MINUTES = 15
HISTORY_MAX_SAMPLES = 5
HISTORY_MIN_TREND_SAMPLES = 4

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": (
            "solunetspain-hf-propagation-data/1.2 "
            "(+https://github.com/solunetspain/hf-propagation-data)"
        ),
        "Accept": "application/json",
        "Referer": PERSPECTIVE_URL,
    }
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def maidenhead_center(grid: str) -> tuple[float, float]:
    """Devuelve el centro de un locator Maidenhead de 2, 4 o 6 caracteres."""
    grid = grid.strip().upper()
    if len(grid) not in (2, 4, 6):
        raise ValueError("El locator debe tener 2, 4 o 6 caracteres")
    if not ("A" <= grid[0] <= "R" and "A" <= grid[1] <= "R"):
        raise ValueError(f"Locator inválido: {grid}")

    lon = (ord(grid[0]) - ord("A")) * 20.0 - 180.0
    lat = (ord(grid[1]) - ord("A")) * 10.0 - 90.0
    lon_size, lat_size = 20.0, 10.0

    if len(grid) >= 4:
        if not grid[2:4].isdigit():
            raise ValueError(f"Locator inválido: {grid}")
        lon += int(grid[2]) * 2.0
        lat += int(grid[3]) * 1.0
        lon_size, lat_size = 2.0, 1.0

    if len(grid) == 6:
        if not ("A" <= grid[4] <= "X" and "A" <= grid[5] <= "X"):
            raise ValueError(f"Locator inválido: {grid}")
        lon += (ord(grid[4]) - ord("A")) / 12.0
        lat += (ord(grid[5]) - ord("A")) / 24.0
        lon_size, lat_size = 1.0 / 12.0, 1.0 / 24.0

    return lat + lat_size / 2.0, lon + lon_size / 2.0


def perspective_bucket(lat: float, lon: float) -> dict[str, Any]:
    """
    Reproduce la fórmula observada en map.min.js:
    1000*floor((lat+90)/4)+floor((lon+180)/6).
    """
    lat_index = math.floor((lat + 90.0) / 4.0)
    lon_index = math.floor((lon + 180.0) / 6.0)
    bucket_id = 1000 * lat_index + lon_index
    return {
        "id": bucket_id,
        "latitude_min": -90.0 + lat_index * 4.0,
        "latitude_max": -90.0 + (lat_index + 1) * 4.0,
        "longitude_min": -180.0 + lon_index * 6.0,
        "longitude_max": -180.0 + (lon_index + 1) * 6.0,
        "resolution_degrees": {"latitude": 4.0, "longitude": 6.0},
    }


def fetch_band(band: int, bucket_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    response = SESSION.get(
        ENDPOINT,
        params={
            "alert": 0,
            "band": band,
            "active": 1,
            "id": bucket_id,
        },
        timeout=45,
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        raise RuntimeError(
            f"Banda {band}: tipo de contenido inesperado {content_type!r}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError(f"Banda {band}: la respuesta no es un objeto JSON")
    if not isinstance(payload.get("zones"), list):
        raise TypeError(f"Banda {band}: 'zones' no es una lista")
    if not isinstance(payload.get("bands"), list):
        raise TypeError(f"Banda {band}: 'bands' no es una lista")

    meta = {
        "status_code": response.status_code,
        "content_type": content_type,
        "cache_control": response.headers.get("cache-control"),
        "http_date": response.headers.get("date"),
        "age_seconds": response.headers.get("age"),
        "etag": response.headers.get("etag"),
        "last_modified": response.headers.get("last-modified"),
        "url": response.url,
        "bytes": len(response.content),
    }
    return payload, meta


def mode_polygons(zone: dict[str, Any], field: str) -> list[list[list[float]]]:
    value = zone.get(field)
    if not isinstance(value, list) or not value:
        return []

    # digital_perimeter es un polígono; CW/SSB suelen ser listas de polígonos.
    if (
        len(value) > 0
        and isinstance(value[0], list)
        and len(value[0]) == 2
        and all(isinstance(x, (int, float)) for x in value[0])
    ):
        return [value]

    polygons = []
    for polygon in value:
        if isinstance(polygon, list) and polygon:
            polygons.append(polygon)
    return polygons


def valid_point(point: Any) -> bool:
    return (
        isinstance(point, list)
        and len(point) == 2
        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)
        and -math.pi / 2 - 1e-6 <= float(point[0]) <= math.pi / 2 + 1e-6
        and -math.pi * 2 - 1e-6 <= float(point[1]) <= math.pi * 2 + 1e-6
    )


def zone_points(zone: dict[str, Any]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for field in ("digital_perimeter", "cw_perimeter", "ssb_perimeter"):
        for polygon in mode_polygons(zone, field):
            for point in polygon:
                if valid_point(point):
                    points.append((float(point[0]), float(point[1])))
    return points


def spherical_centroid(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not points:
        return None
    x = y = z = 0.0
    for lat, lon in points:
        clat = math.cos(lat)
        x += clat * math.cos(lon)
        y += clat * math.sin(lon)
        z += math.sin(lat)
    magnitude = math.sqrt(x * x + y * y + z * z)
    if magnitude < 1e-12:
        return None
    x, y, z = x / magnitude, y / magnitude, z / magnitude
    return math.atan2(z, math.sqrt(x * x + y * y)), math.atan2(y, x)


def distance_bearing(
    lat1_deg: float,
    lon1_deg: float,
    lat2_rad: float,
    lon2_rad: float,
) -> tuple[float, float]:
    lat1, lon1 = math.radians(lat1_deg), math.radians(lon1_deg)
    dlat = lat2_rad - lat1
    dlon = lon2_rad - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    distance = 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(a)))

    y = math.sin(dlon) * math.cos(lat2_rad)
    x = (
        math.cos(lat1) * math.sin(lat2_rad)
        - math.sin(lat1) * math.cos(lat2_rad) * math.cos(dlon)
    )
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return distance, bearing


def sector_name(bearing: float) -> str:
    start = int(math.floor(bearing / 30.0) * 30) % 360
    end = (start + 29) % 360
    return f"{start:03d}-{end:03d}"


def distance_bin(distance_km: float) -> str:
    if distance_km < 500:
        return "0-499"
    if distance_km < 1500:
        return "500-1499"
    if distance_km < 3000:
        return "1500-2999"
    if distance_km < 6000:
        return "3000-5999"
    if distance_km < 10000:
        return "6000-9999"
    return "10000+"


def summarize_zones(
    zones: list[dict[str, Any]],
    origin_lat: float,
    origin_lon: float,
) -> dict[str, Any]:
    mode_zone_counts = Counter()
    mode_polygon_counts = Counter()
    classifications = Counter()
    sector_data: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "zones": 0,
            "activity_zones": 0,
            "muf_zones": 0,
            "modes": Counter(),
            "distances_km": [],
        }
    )
    distance_bins = Counter()
    parsed_zones = 0
    invalid_zones = 0

    for zone in zones:
        if not isinstance(zone, dict):
            invalid_zones += 1
            continue

        modes = []
        for mode, field in (
            ("digital", "digital_perimeter"),
            ("cw", "cw_perimeter"),
            ("ssb", "ssb_perimeter"),
        ):
            polygons = mode_polygons(zone, field)
            if polygons:
                modes.append(mode)
                mode_zone_counts[mode] += 1
                mode_polygon_counts[mode] += len(polygons)

        classifications["muf" if zone.get("is_muf") else "activity"] += 1
        points = zone_points(zone)
        centroid = spherical_centroid(points)
        if centroid is None:
            invalid_zones += 1
            continue

        distance, bearing = distance_bearing(
            origin_lat, origin_lon, centroid[0], centroid[1]
        )
        sector = sector_name(bearing)
        slot = sector_data[sector]
        slot["zones"] += 1
        if zone.get("is_muf"):
            slot["muf_zones"] += 1
        else:
            slot["activity_zones"] += 1
        slot["modes"].update(modes)
        slot["distances_km"].append(distance)
        distance_bins[distance_bin(distance)] += 1
        parsed_zones += 1

    sectors = {}
    for sector, values in sorted(sector_data.items()):
        distances = values.pop("distances_km")
        sectors[sector] = {
            **values,
            "modes": dict(values["modes"]),
            "distance_km": {
                "min": round(min(distances), 1),
                "median": round(statistics.median(distances), 1),
                "max": round(max(distances), 1),
            },
        }

    return {
        "zone_count": len(zones),
        "parsed_zone_count": parsed_zones,
        "invalid_zone_count": invalid_zones,
        "classification": dict(classifications),
        "mode_zone_counts": dict(mode_zone_counts),
        "mode_polygon_counts": dict(mode_polygon_counts),
        "active_sector_count": sum(
            1 for value in sectors.values() if value["activity_zones"] > 0
        ),
        "muf_sector_count": sum(
            1 for value in sectors.values() if value["muf_zones"] > 0
        ),
        "distance_bins_km": dict(distance_bins),
        "sectors": sectors,
    }


def response_signature(payload: dict[str, Any]) -> str:
    compact = json.dumps(payload.get("zones", []), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(compact.encode("utf-8")).hexdigest()[:16]


def load_previous() -> dict[str, Any] | None:
    try:
        response = SESSION.get(
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


def trend_label(values: list[int]) -> str:
    if len(values) < 2:
        return "unknown"
    first, last = values[0], values[-1]
    tolerance = max(1, round(max(first, last) * 0.20))
    if last > first + tolerance:
        return "rising"
    if last < first - tolerance:
        return "falling"
    return "stable"


def parse_iso_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def history_slot_start(value: Any) -> str | None:
    dt = parse_iso_utc(value)
    if dt is None:
        return None
    minute = (dt.minute // HISTORY_SLOT_MINUTES) * HISTORY_SLOT_MINUTES
    return dt.replace(minute=minute, second=0, microsecond=0).isoformat()


def append_history(
    previous: dict[str, Any] | None,
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Conserva una captura por cuarto de hora, aunque la firma no cambie.

    La firma describe el contenido, no la identidad temporal de la muestra.
    Eliminar por firma hacía desaparecer intervalos reales sin cambios. Las
    ejecuciones manuales o por push dentro del mismo cuarto de hora se sustituyen
    por la captura más reciente de ese slot y no deforman la tendencia.
    """
    history: list[dict[str, Any]] = []
    if previous and isinstance(previous.get("history"), list):
        history = [row for row in previous["history"] if isinstance(row, dict)]

    by_slot: dict[str, dict[str, Any]] = {}
    for row in [*history, snapshot]:
        slot = history_slot_start(row.get("fetched_at_utc"))
        timestamp = parse_iso_utc(row.get("fetched_at_utc"))
        if slot is None or timestamp is None:
            continue
        normalized = dict(row)
        normalized["slot_start_utc"] = slot
        previous_row = by_slot.get(slot)
        previous_timestamp = (
            parse_iso_utc(previous_row.get("fetched_at_utc"))
            if previous_row else None
        )
        if previous_timestamp is None or timestamp >= previous_timestamp:
            by_slot[slot] = normalized

    return [by_slot[key] for key in sorted(by_slot)][-HISTORY_MAX_SAMPLES:]


def enrich_history_intervals(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add slot continuity plus the real separation between HTTP captures."""
    enriched: list[dict[str, Any]] = []
    previous_row: dict[str, Any] | None = None
    previous_capture_dt: datetime | None = None
    previous_slot_dt: datetime | None = None

    for row in history:
        current = dict(row)
        capture_dt = parse_iso_utc(current.get("fetched_at_utc"))
        slot = current.get("slot_start_utc") or history_slot_start(
            current.get("fetched_at_utc")
        )
        current["slot_start_utc"] = slot
        slot_dt = parse_iso_utc(slot)
        if previous_row is None:
            current["unchanged_from_previous"] = None
            current["interval_minutes_from_previous"] = None
            current["capture_interval_minutes_from_previous"] = None
        else:
            current["unchanged_from_previous"] = (
                current.get("signature") == previous_row.get("signature")
            )
            if slot_dt is not None and previous_slot_dt is not None:
                current["interval_minutes_from_previous"] = round(
                    (slot_dt - previous_slot_dt).total_seconds() / 60.0, 2
                )
            else:
                current["interval_minutes_from_previous"] = None
            if capture_dt is not None and previous_capture_dt is not None:
                current["capture_interval_minutes_from_previous"] = round(
                    (capture_dt - previous_capture_dt).total_seconds() / 60.0, 2
                )
            else:
                current["capture_interval_minutes_from_previous"] = None

        enriched.append(current)
        previous_row = current
        previous_capture_dt = capture_dt
        previous_slot_dt = slot_dt

    return enriched


def assess_history_quality(history: list[dict[str, Any]]) -> dict[str, Any]:
    slot_times = [
        parse_iso_utc(row.get("slot_start_utc"))
        for row in history
    ]
    valid_times = [value for value in slot_times if value is not None]
    intervals = [
        round((current - previous).total_seconds() / 60.0, 2)
        for previous, current in zip(valid_times, valid_times[1:])
    ]
    contiguous_tail = 1 if valid_times else 0
    for interval in reversed(intervals):
        if interval == HISTORY_SLOT_MINUTES:
            contiguous_tail += 1
        else:
            break
    coverage = (
        round((valid_times[-1] - valid_times[0]).total_seconds() / 60.0, 2)
        if len(valid_times) >= 2 else 0.0
    )
    valid_for_trend = (
        len(valid_times) >= HISTORY_MIN_TREND_SAMPLES
        and contiguous_tail >= HISTORY_MIN_TREND_SAMPLES
        and coverage >= HISTORY_SLOT_MINUTES * (HISTORY_MIN_TREND_SAMPLES - 1)
    )
    ready_samples_needed = max(0, HISTORY_MIN_TREND_SAMPLES - contiguous_tail)
    earliest_ready = (
        valid_times[-1] + timedelta(
            minutes=HISTORY_SLOT_MINUTES * ready_samples_needed
        )
        if valid_times and ready_samples_needed
        else valid_times[-1] if valid_times else None
    )
    return {
        "status": "valid" if valid_for_trend else "insufficient_data",
        "valid_for_trend": valid_for_trend,
        "samples": len(valid_times),
        "contiguous_tail_samples": contiguous_tail,
        "coverage_minutes": coverage,
        "slot_intervals_minutes": intervals,
        "required_contiguous_samples": HISTORY_MIN_TREND_SAMPLES,
        "trend_ready_samples_needed": ready_samples_needed,
        "earliest_trend_ready_utc": earliest_ready.isoformat() if earliest_ready else None,
        "estimate_assumes_contiguous_future_slots": bool(ready_samples_needed),
        "friendly_message": (
            "Tendencia disponible"
            if valid_for_trend
            else f"Tendencia en preparación: {contiguous_tail}/"
            f"{HISTORY_MIN_TREND_SAMPLES} muestras consecutivas; "
            f"faltan {ready_samples_needed}."
        ),
        "message": None if valid_for_trend else "No hay datos suficientes para calcular la tendencia",
    }


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    DIAG.mkdir(parents=True, exist_ok=True)

    generated_at = utcnow().isoformat()
    lat, lon = maidenhead_center(GRID)
    bucket = perspective_bucket(lat, lon)

    validation = {
        "endpoint_located": True,
        "response_received": False,
        "format_parsed": False,
        "current_endpoint_response_checked": False,
        "perspective_bucket_obtained": True,
        "exact_local_value_obtained": False,
        "band_variation_verified": False,
    }

    diagnostic: dict[str, Any] = {
        "generated_at": generated_at,
        "target": PERSPECTIVE_URL,
        "endpoint": ENDPOINT,
        "grid": GRID,
        "grid_center": {"latitude": lat, "longitude": lon},
        "bucket": bucket,
        "validation": validation,
        "errors": [],
    }

    raw_responses: dict[str, Any] = {}
    band_summaries: dict[str, Any] = {}
    signatures: dict[str, str] = {}
    metadata: dict[str, Any] = {}

    try:
        for band in BANDS:
            try:
                payload, meta = fetch_band(band, bucket["id"])
                raw_responses[str(band)] = payload
                metadata[str(band)] = meta
                signatures[str(band)] = response_signature(payload)

                if band != 0:
                    wrong_bands = sorted(
                        {
                            zone.get("band")
                            for zone in payload["zones"]
                            if isinstance(zone, dict)
                            and zone.get("band") not in (None, band)
                        }
                    )
                    if wrong_bands:
                        raise RuntimeError(
                            f"Banda {band}: zonas con bandas distintas {wrong_bands}"
                        )

                band_summaries[str(band)] = {
                    "requested_band_mhz": band,
                    "response_version": payload.get("v"),
                    "reported_active_bands_mhz": payload.get("bands"),
                    "alert": payload.get("alert"),
                    "signature": signatures[str(band)],
                    **summarize_zones(payload["zones"], lat, lon),
                }
                time.sleep(0.20)
            except Exception as exc:
                diagnostic["errors"].append(
                    {"band": band, "error": str(exc)}
                )

        validation["response_received"] = len(raw_responses) > 0
        validation["format_parsed"] = len(band_summaries) > 0
        validation["current_endpoint_response_checked"] = all(
            meta.get("status_code") == 200
            and "application/json" in str(meta.get("content_type", "")).lower()
            for meta in metadata.values()
        )

        specific_signatures = {
            signature
            for band, signature in signatures.items()
            if band != "0"
        }
        validation["band_variation_verified"] = len(specific_signatures) >= 3

        if "0" not in raw_responses:
            raise RuntimeError("No se obtuvo la vista MUF (band=0)")
        if len(band_summaries) < 6:
            raise RuntimeError(
                f"Solo se obtuvieron {len(band_summaries)} bandas válidas"
            )

        # La respuesta carece de timestamp de observación; la actualidad que se
        # acredita es la de la consulta HTTP y su caché corta, no la de cada spot.
        cache_controls = [
            meta.get("cache_control") for meta in metadata.values()
            if meta.get("cache_control")
        ]
        http_dates = sorted(
            {
                str(meta.get("http_date"))
                for meta in metadata.values()
                if meta.get("http_date")
            }
        )
        cache_max_ages = []
        for value in cache_controls:
            for item in str(value).split(","):
                item = item.strip().lower()
                if item.startswith("max-age="):
                    try:
                        cache_max_ages.append(int(item.split("=", 1)[1]))
                    except ValueError:
                        pass

        snapshot_bands = {
            band: {
                "zone_count": summary["zone_count"],
                "activity_zones": summary["classification"].get("activity", 0),
                "muf_zones": summary["classification"].get("muf", 0),
                "active_sector_count": summary["active_sector_count"],
                "muf_sector_count": summary["muf_sector_count"],
                "mode_zone_counts": summary["mode_zone_counts"],
            }
            for band, summary in band_summaries.items()
        }
        snapshot_signature = hashlib.sha256(
            json.dumps(snapshot_bands, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        snapshot = {
            "fetched_at_utc": generated_at,
            "signature": snapshot_signature,
            "bands": snapshot_bands,
        }

        previous = load_previous()
        history = enrich_history_intervals(append_history(previous, snapshot))
        history_quality = assess_history_quality(history)
        trend_history = (
            history[-history_quality["contiguous_tail_samples"]:]
            if history_quality["valid_for_trend"] else []
        )

        trend = {}
        for band in band_summaries:
            rows = [
                row["bands"][band]
                for row in trend_history
                if isinstance(row.get("bands"), dict)
                and isinstance(row["bands"].get(band), dict)
            ]
            activity_values = [int(row.get("activity_zones", 0)) for row in rows]
            sector_values = [int(row.get("active_sector_count", 0)) for row in rows]
            trend[band] = {
                "samples": len(rows),
                "activity_zones": trend_label(activity_values),
                "active_sectors": trend_label(sector_values),
                "activity_zone_series": activity_values,
                "active_sector_series": sector_values,
                "status": "ok" if history_quality["valid_for_trend"] else "insufficient_data",
            }

        payload = {
            "source": "DXView direct JSON endpoint",
            "generated_at": generated_at,
            "perspective": {
                "grid": GRID,
                "grid_center": {"latitude": lat, "longitude": lon},
                "bucket": bucket,
                "limitation": (
                    "DXView agrupa la perspectiva en una celda de 4° x 6°. "
                    "El dato corresponde al bucket regional que contiene IN91PO; "
                    "KC2G proporciona la referencia específica para el locator."
                ),
            },
            "endpoint": {
                "url": ENDPOINT,
                "parameters": {
                    "alert": 0,
                    "active": 1,
                    "id": bucket["id"],
                    "band": BANDS,
                },
                "cache_control_observed": sorted(set(cache_controls)),
                "http_date_observed": http_dates,
                "cache_max_age_seconds": max(cache_max_ages) if cache_max_ages else None,
                "capture_timestamp_utc": generated_at,
                "observation_time_basis": (
                    "UTC capture time plus HTTP Date/cache metadata; the source "
                    "does not publish a native observation timestamp."
                ),
                "source_observation_timestamp_available": False,
            },
            "validation": validation,
            "bands": band_summaries,
            "history": history,
            "history_quality": history_quality,
            "history_policy": {
                "maximum_samples": HISTORY_MAX_SAMPLES,
                "unchanged_samples_are_preserved": True,
                "deduplication_key": "15-minute UTC slot; newest capture wins within a slot",
                "expected_schedule_minutes": HISTORY_SLOT_MINUTES,
                "minimum_contiguous_samples_for_trend": HISTORY_MIN_TREND_SAMPLES,
                "note": (
                    "Trend uses slot time only. Actual capture separation is retained "
                    "separately as capture_interval_minutes_from_previous."
                ),
            },
            "trend": trend,
            "interpretation_notes": {
                "band_0": (
                    "Vista MUF. Sus zonas pueden pertenecer a diferentes bandas."
                ),
                "is_muf_true": (
                    "Zona empleada por DXView en la capa MUF, según el "
                    "comportamiento de map.min.js."
                ),
                "is_muf_false": (
                    "Zona de actividad de la banda seleccionada."
                ),
                "coordinates": (
                    "Los perímetros llegan en radianes [latitud, longitud] y "
                    "se convierten a sectores y distancias desde IN91PO."
                ),
                "modes": {
                    "digital": "digital_perimeter",
                    "cw": "cw_perimeter",
                    "ssb": "ssb_perimeter",
                },
            },
        }

        raw_payload = {
            "source": "DXView direct JSON endpoint",
            "generated_at": generated_at,
            "perspective_grid": GRID,
            "bucket_id": bucket["id"],
            "responses": raw_responses,
        }

        diagnostic.update(
            {
                "status": "ok",
                "validation": validation,
                "http_metadata": metadata,
                "response_signatures": signatures,
                "valid_band_count": len(band_summaries),
                "history_samples": len(history),
                "history_quality": history_quality,
                "history_policy": (
                    "one newest capture per 15-minute UTC slot; preserve identical "
                    "signatures across different slots"
                ),
                "history_intervals_minutes": [
                    row.get("interval_minutes_from_previous")
                    for row in history
                    if row.get("interval_minutes_from_previous") is not None
                ],
            }
        )

        write_json(DATA / "dxview-in91po.json", payload)
        write_json(DATA / "dxview-in91po-raw.json", raw_payload)
        write_json(DIAG / "dxview-diagnostic.json", diagnostic)

        print(
            json.dumps(
                {
                    "status": "ok",
                    "validation": validation,
                    "bucket_id": bucket["id"],
                    "valid_band_count": len(band_summaries),
                    "history_samples": len(history),
                },
                indent=2,
            )
        )
        return 0

    except Exception as exc:
        diagnostic["status"] = "error"
        diagnostic["errors"].append({"stage": "fatal", "error": str(exc)})
        write_json(DIAG / "dxview-diagnostic.json", diagnostic)
        write_json(
            DATA / "dxview-in91po.json",
            {
                "source": "DXView direct JSON endpoint",
                "generated_at": generated_at,
                "status": "error",
                "validation": validation,
                "error": str(exc),
                "diagnostic": "../diagnostics/dxview-diagnostic.json",
            },
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
