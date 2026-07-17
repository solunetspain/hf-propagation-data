#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

URLS = {
    "scales": "https://services.swpc.noaa.gov/products/noaa-scales.json",
    "kp": "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    "plasma": [
        "https://services.swpc.noaa.gov/products/solar-wind/plasma-2-hour.json",
        "https://services.swpc.noaa.gov/products/solar-wind/plasma-6-hour.json",
        "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json",
    ],
    "mag": [
        "https://services.swpc.noaa.gov/products/solar-wind/mag-2-hour.json",
        "https://services.swpc.noaa.gov/products/solar-wind/mag-6-hour.json",
        "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json",
    ],
    "xray": "https://services.swpc.noaa.gov/json/goes/primary/xrays-6-hour.json",
    "protons": "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-6-hour.json",
    "electrons": "https://services.swpc.noaa.gov/json/goes/primary/integral-electrons-6-hour.json",
    "solar_flux": "https://services.swpc.noaa.gov/json/solar-radio-flux.json",
    "sunspots": "https://services.swpc.noaa.gov/json/sunspot_report.json",
    "drap": "https://services.swpc.noaa.gov/text/drap_global_frequencies.txt",
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

def fetch_first_tabular(urls: list[str], diagnostic: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    for url in urls:
        try:
            body, meta = fetch(url)
            data = json_body(body)
            row = tabular_latest(data)
            diagnostic["requests"].append({**meta, "usable": row is not None})
            if row:
                return row, url
        except Exception as exc:  # noqa: BLE001
            diagnostic["requests"].append({"url": url, "usable": False, "error": f"{type(exc).__name__}: {exc}"})
    return None, None

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
        body, meta = fetch(URLS["kp"])
        data = json_body(body)
        row = latest_dict(data)
        diagnostic["requests"].append({**meta, "usable": row is not None})
        if row:
            summary["current"]["geomagnetic"] = {
                "timestamp_utc": parse_dt(row.get("time_tag")),
                "kp": finite(row.get("Kp")),
                "a_index": finite(row.get("a_running")),
                "station_count": row.get("station_count"),
            }
        diagnostic["validation"]["kp"] = row is not None
    except Exception as exc:
        diagnostic["errors"].append(f"kp: {exc}")
        diagnostic["validation"]["kp"] = False

    # Solar wind
    plasma, plasma_url = fetch_first_tabular(URLS["plasma"], diagnostic)
    mag, mag_url = fetch_first_tabular(URLS["mag"], diagnostic)
    if plasma or mag:
        summary["current"]["solar_wind"] = {
            "timestamp_utc": parse_dt((plasma or mag or {}).get("time_tag")),
            "density_p_cm3": finite((plasma or {}).get("density")),
            "speed_km_s": finite((plasma or {}).get("speed")),
            "temperature_k": finite((plasma or {}).get("temperature")),
            "bx_gsm_nt": finite((mag or {}).get("bx_gsm")),
            "by_gsm_nt": finite((mag or {}).get("by_gsm")),
            "bz_gsm_nt": finite((mag or {}).get("bz_gsm")),
            "bt_nt": finite((mag or {}).get("bt")),
            "plasma_url_used": plasma_url,
            "mag_url_used": mag_url,
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

    # Solar flux: keep latest row; schemas may change, retain useful fields.
    try:
        body, meta = fetch(URLS["solar_flux"])
        data = json_body(body)
        row = latest_dict(data, ("time_tag", "timestamp", "date"))
        diagnostic["requests"].append({**meta, "usable": row is not None})
        if row:
            summary["current"]["solar_flux"] = {
                "timestamp_utc": parse_dt(row.get("time_tag") or row.get("timestamp") or row.get("date")),
                "observed_flux": finite(row.get("flux") or row.get("observed_flux") or row.get("f107")),
                "adjusted_flux": finite(row.get("adjusted_flux")),
                "raw": row,
            }
        diagnostic["validation"]["solar_flux"] = row is not None
    except Exception as exc:
        diagnostic["errors"].append(f"solar_flux: {exc}")
        diagnostic["validation"]["solar_flux"] = False

    # Sunspots: expose last report, but do not pretend group count is official SSN.
    try:
        body, meta = fetch(URLS["sunspots"])
        data = json_body(body)
        row = latest_dict(data, ("observed_date", "time_tag", "date"))
        diagnostic["requests"].append({**meta, "usable": row is not None})
        if row:
            summary["current"]["sunspots"] = {
                "timestamp_utc": parse_dt(row.get("observed_date") or row.get("time_tag") or row.get("date")),
                "sunspot_number": finite(row.get("sunspot_number") or row.get("ssn") or row.get("sunspot_count")),
                "raw": row,
                "note": "Only use sunspot_number when the field is explicitly present; do not derive SSN from groups.",
            }
        diagnostic["validation"]["sunspots"] = row is not None
    except Exception as exc:
        diagnostic["errors"].append(f"sunspots: {exc}")
        diagnostic["validation"]["sunspots"] = False

    # D-RAP text. Preserve a compact raw excerpt and numeric tokens.
    try:
        body, meta = fetch(URLS["drap"])
        text = body.decode("utf-8", errors="replace").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        diagnostic["requests"].append({**meta, "usable": bool(lines)})
        summary["drap"] = {
            "status": "raw_available" if lines else "unavailable",
            "lines": lines[:30],
            "note": "NOAA D-RAP ASCII source preserved. Interpretation must follow the headings present in these lines.",
        }
        diagnostic["validation"]["drap"] = bool(lines)
    except Exception as exc:
        diagnostic["errors"].append(f"drap: {exc}")
        diagnostic["validation"]["drap"] = False

    valid_count = sum(bool(v) for v in diagnostic["validation"].values())
    summary["status"] = "ok" if valid_count >= 6 else "partial" if valid_count else "error"
    summary["validation"] = diagnostic["validation"]
    diagnostic["status"] = summary["status"]
    diagnostic["valid_sections"] = valid_count

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
