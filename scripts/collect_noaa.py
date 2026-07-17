#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import statistics
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

URLS = {
    "scales": "https://services.swpc.noaa.gov/products/noaa-scales.json",
    "kp": "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "kp_estimated_1m": "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
    "plasma": "https://services.swpc.noaa.gov/json/rtsw/rtsw_wind_1m.json",
    "mag": "https://services.swpc.noaa.gov/json/rtsw/rtsw_mag_1m.json",
    "xray": "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json",
    "protons": "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-6-hour.json",
    "electrons": "https://services.swpc.noaa.gov/json/goes/primary/integral-electrons-6-hour.json",
    "solar_flux": "https://services.swpc.noaa.gov/json/f107_cm_flux.json",
    "solar_indices": "https://services.swpc.noaa.gov/text/daily-solar-indices.txt",
    "drap": "https://services.swpc.noaa.gov/text/drap_global_frequencies.txt",
}

DRAP_POINTS = {
    "IN91PO": (41.6041667, -0.7083333),
    "Galicia": (42.75, -8.40),
    "Cantabrico": (43.30, -3.00),
    "Centro": (40.42, -3.70),
    "Mediterraneo": (39.47, -0.38),
    "Andalucia": (37.39, -5.99),
    "Baleares": (39.57, 2.65),
    "Canarias": (28.10, -15.42),
}

REQUIRED_SECTIONS = {
    "scales",
    "kp",
    "solar_wind",
    "magnetic_field",
    "xray",
    "solar_flux",
    "sunspots",
    "drap",
}

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def fetch(url: str, timeout: int = 30) -> tuple[bytes, dict[str, Any]]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SOLUNET-HF-NOAA-Collector/1.0",
            "Accept": "application/json,text/plain;q=0.9,*/*;q=0.1",
            "Cache-Control": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
        meta = {
            "url": url,
            "http_status": getattr(response, "status", 200),
            "content_type": response.headers.get("Content-Type", ""),
            "bytes": len(body),
        }
    return body, meta

def parse_dt(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return str(value)

def finite(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None

def latest_dict(items: Any, time_keys=("time_tag", "timestamp", "date", "observed_date")) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    valid = [x for x in items if isinstance(x, dict)]
    if not valid:
        return None
    def key(item: dict[str, Any]) -> str:
        for name in time_keys:
            if item.get(name):
                return str(item[name])
        return ""
    return sorted(valid, key=key)[-1]

def tabular_latest(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[0], list):
        return None
    header = [str(x) for x in data[0]]
    rows = [r for r in data[1:] if isinstance(r, list) and len(r) == len(header)]
    if not rows:
        return None
    return dict(zip(header, rows[-1]))

def json_body(body: bytes) -> Any:
    return json.loads(body.decode("utf-8"))

def fetch_json(url: str, diagnostic: dict[str, Any]) -> Any:
    body, meta = fetch(url)
    data = json_body(body)
    diagnostic["requests"].append({**meta, "usable": data is not None})
    return data

def parse_datetime(value: Any) -> datetime | None:
    parsed = parse_dt(value)
    if not parsed:
        return None
    try:
        return datetime.fromisoformat(parsed)
    except ValueError:
        return None

def rtsw_summary(data: Any, field_map: dict[str, str]) -> dict[str, Any] | None:
    """Select the active, good-quality RTSW source and median its last 5 minutes."""
    if not isinstance(data, list):
        return None
    candidates = []
    for item in data:
        if not isinstance(item, dict) or item.get("active") is not True:
            continue
        if finite(item.get("overall_quality")) != 0:
            continue
        timestamp = parse_datetime(item.get("time_tag"))
        if timestamp is None:
            continue
        if not any(finite(item.get(source)) is not None for source in field_map):
            continue
        candidates.append((timestamp, item))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    latest_time, latest = candidates[-1]
    source = latest.get("source")
    recent = [
        item
        for timestamp, item in candidates
        if item.get("source") == source
        and 0 <= (latest_time - timestamp).total_seconds() <= 5 * 60
    ]
    result: dict[str, Any] = {
        "timestamp_utc": latest_time.isoformat(),
        "source": source,
        "samples": len(recent),
        "overall_quality": 0,
        "selection": "active=true, overall_quality=0, median of up to 5 minutes",
    }
    for source_field, output_field in field_map.items():
        values = [finite(item.get(source_field)) for item in recent]
        usable = [value for value in values if value is not None]
        result[output_field] = round(statistics.median(usable), 3) if usable else None
    return result

def parse_daily_solar_indices(text: str) -> dict[str, Any] | None:
    """Parse the latest complete NOAA daily solar row (F10.7 and SESC SSN)."""
    rows = []
    for line in text.splitlines():
        if not re.match(r"^\s*\d{4}\s+\d{2}\s+\d{2}\s+", line):
            continue
        fields = line.split()
        if len(fields) < 5:
            continue
        try:
            date = datetime(
                int(fields[0]), int(fields[1]), int(fields[2]), tzinfo=timezone.utc
            )
        except ValueError:
            continue
        rows.append(
            {
                "timestamp_utc": date.isoformat(),
                "date": date.date().isoformat(),
                "f107_sfu": finite(fields[3]),
                "sunspot_number": finite(fields[4]),
            }
        )
    return rows[-1] if rows else None

def parse_drap(text: str, points: dict[str, tuple[float, float]] | None = None) -> dict[str, Any] | None:
    """Parse NOAA's global highest-frequency-affected-by-1-dB grid."""
    points = points or DRAP_POINTS
    lines = text.splitlines()
    timestamp = None
    messages: dict[str, str] = {}
    for line in lines:
        match = re.search(r"Product Valid At\s*:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+UTC", line)
        if match:
            timestamp = parse_dt(f"{match.group(1)}T{match.group(2)}:00Z")
        message = re.match(r"^#\s*(X-RAY|Proton) (Message|Warning)\s*:\s*(.*)$", line, re.I)
        if message:
            key = f"{message.group(1).lower().replace('-', '_')}_{message.group(2).lower()}"
            messages[key] = message.group(3).strip()

    longitudes: list[float] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "|" in line:
            continue
        tokens = stripped.split()
        if len(tokens) >= 80:
            try:
                candidate = [float(token) for token in tokens]
            except ValueError:
                continue
            if min(candidate) <= -170 and max(candidate) >= 170:
                longitudes = candidate
                break
    if not longitudes:
        return None

    grid: list[tuple[float, list[float]]] = []
    for line in lines:
        match = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*\|\s*(.*)$", line)
        if not match:
            continue
        try:
            latitude = float(match.group(1))
            values = [float(value) for value in match.group(2).split()]
        except ValueError:
            continue
        if len(values) == len(longitudes):
            grid.append((latitude, values))
    if not grid:
        return None

    sampled: dict[str, Any] = {}
    for name, (latitude, longitude) in points.items():
        row_latitude, row_values = min(grid, key=lambda row: abs(row[0] - latitude))
        longitude_index = min(
            range(len(longitudes)), key=lambda index: abs(longitudes[index] - longitude)
        )
        sampled[name] = {
            "latitude": latitude,
            "longitude": longitude,
            "grid_latitude": row_latitude,
            "grid_longitude": longitudes[longitude_index],
            "highest_frequency_affected_1db_mhz": row_values[longitude_index],
        }

    spain_values = [
        item["highest_frequency_affected_1db_mhz"] for item in sampled.values()
    ]
    return {
        "status": "parsed",
        "timestamp_utc": timestamp,
        "metric": "highest frequency affected by at least 1 dB of D-region absorption",
        "units": "MHz",
        "grid": {
            "longitude_count": len(longitudes),
            "latitude_count": len(grid),
            "sampling": "nearest NOAA grid point",
        },
        "messages": messages,
        "points": sampled,
        "spain": {
            "median_mhz": round(statistics.median(spain_values), 2),
            "maximum_mhz": round(max(spain_values), 2),
        },
    }

def xray_class(flux: float | None) -> str | None:
    if flux is None or flux <= 0:
        return None
    levels = [("X", 1e-4), ("M", 1e-5), ("C", 1e-6), ("B", 1e-7), ("A", 1e-8)]
    for letter, base in levels:
        if flux >= base:
            return f"{letter}{flux/base:.1f}"
    return f"A{flux/1e-8:.1f}"

def latest_energy_flux(data: Any, energy_contains: str | None = None) -> dict[str, Any] | None:
    if not isinstance(data, list):
        return None
    candidates = []
    for item in data:
        if not isinstance(item, dict):
            continue
        energy = str(item.get("energy", item.get("energy_range", "")))
        if energy_contains and energy_contains not in energy:
            continue
        flux = finite(item.get("flux"))
        if flux is None:
            continue
        candidates.append(item)
    return latest_dict(candidates)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("public/data/noaa-summary.json"))
    parser.add_argument("--diagnostic", type=Path, default=Path("public/diagnostics/noaa-diagnostic.json"))
    parser.add_argument("--last-good", type=Path, default=Path("public/data/noaa-last-good.json"))
    args = parser.parse_args()

    diagnostic: dict[str, Any] = {
        "generated_at": now_iso(),
        "status": "error",
        "requests": [],
        "errors": [],
        "validation": {},
    }
    summary: dict[str, Any] = {
        "source": "NOAA/SWPC compact summary",
        "generated_at": now_iso(),
        "status": "partial",
        "current": {},
        "forecast": {},
        "drap": {},
        "source_urls": URLS,
    }

    # NOAA scales
    try:
        body, meta = fetch(URLS["scales"])
        scales = json_body(body)
        diagnostic["requests"].append({**meta, "usable": isinstance(scales, dict)})
        current = scales.get("0", {}) if isinstance(scales, dict) else {}
        summary["current"]["scales"] = {
            "timestamp_utc": parse_dt(f"{current.get('DateStamp')}T{current.get('TimeStamp')}"),
            "R": current.get("R"),
            "S": current.get("S"),
            "G": current.get("G"),
        }
        summary["forecast"]["scales"] = {k: scales.get(k) for k in ("1", "2", "3")} if isinstance(scales, dict) else {}
        diagnostic["validation"]["scales"] = bool(current)
    except Exception as exc:
        diagnostic["errors"].append(f"scales: {exc}")
        diagnostic["validation"]["scales"] = False

    # Kp and A
    try:
        data = fetch_json(URLS["kp"], diagnostic)
        row = latest_dict(data)
        if row:
            summary["current"]["geomagnetic"] = {
                "timestamp_utc": parse_dt(row.get("time_tag")),
                "kp": finite(row.get("Kp")),
                "a_index": finite(row.get("a_running")),
                "station_count": row.get("station_count"),
                "classification": "official 3-hour planetary Kp",
            }
        diagnostic["validation"]["kp"] = row is not None
    except Exception as exc:
        diagnostic["errors"].append(f"kp: {exc}")
        diagnostic["validation"]["kp"] = False

    # One-minute estimated Kp is useful for recency, but never replaces the
    # official 3-hour value above.
    try:
        data = fetch_json(URLS["kp_estimated_1m"], diagnostic)
        row = latest_dict(data)
        if row:
            summary["current"]["geomagnetic_estimated_1m"] = {
                "timestamp_utc": parse_dt(row.get("time_tag")),
                "kp": finite(row.get("kp_index")),
                "estimated_kp": finite(row.get("estimated_kp")),
                "kp_code": row.get("kp"),
                "classification": "estimated 1-minute Kp; auxiliary, not official 3-hour Kp",
            }
        diagnostic["validation"]["kp_estimated_1m"] = row is not None
    except Exception as exc:
        diagnostic["errors"].append(f"kp_estimated_1m: {exc}")
        diagnostic["validation"]["kp_estimated_1m"] = False

    # Real-time solar wind. NOAA currently publishes object arrays, not the
    # older tabular /products/solar-wind endpoints.
    plasma = None
    mag = None
    try:
        data = fetch_json(URLS["plasma"], diagnostic)
        plasma = rtsw_summary(
            data,
            {
                "proton_density": "density_p_cm3",
                "proton_speed": "speed_km_s",
                "proton_temperature": "temperature_k",
            },
        )
    except Exception as exc:
        diagnostic["errors"].append(f"solar_wind: {exc}")
    try:
        data = fetch_json(URLS["mag"], diagnostic)
        mag = rtsw_summary(
            data,
            {
                "bx_gsm": "bx_gsm_nt",
                "by_gsm": "by_gsm_nt",
                "bz_gsm": "bz_gsm_nt",
                "bt": "bt_nt",
            },
        )
    except Exception as exc:
        diagnostic["errors"].append(f"magnetic_field: {exc}")
    if plasma or mag:
        summary["current"]["solar_wind"] = {
            "plasma": plasma,
            "magnetic_field": mag,
            "plasma_url_used": URLS["plasma"] if plasma else None,
            "mag_url_used": URLS["mag"] if mag else None,
        }
    diagnostic["validation"]["solar_wind"] = plasma is not None
    diagnostic["validation"]["magnetic_field"] = mag is not None

    # GOES X-ray
    try:
        body, meta = fetch(URLS["xray"])
        data = json_body(body)
        row = latest_energy_flux(data, "0.1-0.8nm") or latest_energy_flux(data)
        diagnostic["requests"].append({**meta, "usable": row is not None})
        if row:
            flux = finite(row.get("flux"))
            summary["current"]["xray"] = {
                "timestamp_utc": parse_dt(row.get("time_tag")),
                "flux_w_m2": flux,
                "class": xray_class(flux),
                "energy": row.get("energy"),
                "satellite": row.get("satellite"),
            }
        diagnostic["validation"]["xray"] = row is not None
    except Exception as exc:
        diagnostic["errors"].append(f"xray: {exc}")
        diagnostic["validation"]["xray"] = False

    for name, energy in (("protons", ">=10 MeV"), ("electrons", ">=2 MeV")):
        try:
            body, meta = fetch(URLS[name])
            data = json_body(body)
            row = latest_energy_flux(data, energy) or latest_energy_flux(data)
            diagnostic["requests"].append({**meta, "usable": row is not None})
            if row:
                summary["current"][name] = {
                    "timestamp_utc": parse_dt(row.get("time_tag")),
                    "flux": finite(row.get("flux")),
                    "energy": row.get("energy"),
                    "units": row.get("units"),
                    "satellite": row.get("satellite"),
                }
            diagnostic["validation"][name] = row is not None
        except Exception as exc:
            diagnostic["errors"].append(f"{name}: {exc}")
            diagnostic["validation"][name] = False

    # Official F10.7 at 2800 MHz.
    try:
        data = fetch_json(URLS["solar_flux"], diagnostic)
        row = latest_dict(data, ("time_tag", "timestamp", "date"))
        flux = finite(row.get("flux")) if row else None
        frequency = finite(row.get("frequency")) if row else None
        usable = row is not None and flux is not None and frequency == 2800
        if usable and row:
            summary["current"]["solar_flux"] = {
                "timestamp_utc": parse_dt(row.get("time_tag") or row.get("timestamp") or row.get("date")),
                "observed_flux_sfu": flux,
                "frequency_mhz": frequency,
                "reporting_schedule": row.get("reporting_schedule"),
                "ninety_day_mean_sfu": finite(row.get("ninety_day_mean")),
                "classification": "official NOAA F10.7 observation",
            }
        diagnostic["validation"]["solar_flux"] = usable
    except Exception as exc:
        diagnostic["errors"].append(f"solar_flux: {exc}")
        diagnostic["validation"]["solar_flux"] = False

    # NOAA daily solar indices explicitly include SESC Sunspot Number. This is
    # not derived from region/group records.
    try:
        body, meta = fetch(URLS["solar_indices"])
        text = body.decode("utf-8", errors="replace")
        row = parse_daily_solar_indices(text)
        usable = row is not None and row.get("sunspot_number") is not None
        diagnostic["requests"].append({**meta, "usable": usable})
        if row:
            summary["current"]["sunspots"] = {
                "timestamp_utc": row.get("timestamp_utc"),
                "date": row.get("date"),
                "sunspot_number": row.get("sunspot_number"),
                "classification": "NOAA SESC daily sunspot number",
                "daily_f107_sfu_cross_check": row.get("f107_sfu"),
            }
        diagnostic["validation"]["sunspots"] = usable
    except Exception as exc:
        diagnostic["errors"].append(f"sunspots: {exc}")
        diagnostic["validation"]["sunspots"] = False

    # D-RAP: parse the global 1 dB highest-affected-frequency grid and expose
    # Spain/IN91PO values. Do not mark raw text alone as validated.
    try:
        body, meta = fetch(URLS["drap"])
        text = body.decode("utf-8", errors="replace").strip()
        drap = parse_drap(text)
        diagnostic["requests"].append({**meta, "usable": drap is not None})
        summary["drap"] = drap or {
            "status": "unparsed",
            "note": "NOAA replied, but the D-RAP grid schema was not recognized.",
        }
        diagnostic["validation"]["drap"] = drap is not None
    except Exception as exc:
        diagnostic["errors"].append(f"drap: {exc}")
        diagnostic["validation"]["drap"] = False

    valid_count = sum(bool(v) for v in diagnostic["validation"].values())
    missing_required = sorted(
        section
        for section in REQUIRED_SECTIONS
        if not diagnostic["validation"].get(section, False)
    )
    summary["status"] = "ok" if not missing_required else "partial" if valid_count else "error"
    summary["validation"] = diagnostic["validation"]
    summary["required_sections"] = sorted(REQUIRED_SECTIONS)
    summary["missing_required_sections"] = missing_required
    diagnostic["status"] = summary["status"]
    diagnostic["valid_sections"] = valid_count
    diagnostic["required_sections"] = sorted(REQUIRED_SECTIONS)
    diagnostic["missing_required_sections"] = missing_required

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    if summary["status"] != "error":
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        args.last_good.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.output, args.last_good)
    elif args.last_good.exists():
        stale = json.loads(args.last_good.read_text(encoding="utf-8"))
        stale["status"] = "stale"
        stale["generated_at"] = now_iso()
        stale["stale_reason"] = "NOAA current collection failed"
        args.output.write_text(json.dumps(stale, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        diagnostic["last_good_preserved"] = True

    args.diagnostic.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
