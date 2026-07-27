#!/usr/bin/env python3
"""Collect NASA OMNI2 context, with NOAA SWPC fallback for runner outages."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("public/data")
URL_TEMPLATE = "https://omniweb.gsfc.nasa.gov/pub/omni2/omni2_{year}.dat"
NOAA_MAG_URL = "https://services.swpc.noaa.gov/products/solar-wind/mag-1-day.json"
NOAA_PLASMA_URL = "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json"


def fetch(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "hf-propagation-data/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def collect_noaa_fallback(now: datetime, errors: list[str]) -> dict:
    products = {}
    fallback_errors = []
    for name, url in (("mag", NOAA_MAG_URL), ("plasma", NOAA_PLASMA_URL)):
        try:
            payload = json.loads(fetch(url).decode("utf-8"))
            rows = payload[1:] if isinstance(payload, list) and payload else []
            valid = [row for row in rows if isinstance(row, list) and row and row[0]]
            if valid:
                products[name] = {"url": url, "latest": valid[-1]}
            else:
                fallback_errors.append(f"{name}: respuesta sin registros")
        except Exception as exc:
            fallback_errors.append(f"{name}: {exc}")
    if not products:
        return {
            "status": "error",
            "source": "NASA OMNI2",
            "url": URL_TEMPLATE.format(year=now.year),
            "retrieved_at_utc": now.isoformat(),
            "generated_at": now.isoformat(),
            "prediction_weight": 0,
            "use_in_prediction": False,
            "attempt_errors": errors,
            "fallback_errors": fallback_errors,
            "error": "NASA OMNI2 inaccesible y el respaldo NOAA SWPC tampoco devolvió datos válidos.",
            "note": "Contexto diagnóstico no disponible; peso 0 %.",
        }
    return {
        "status": "partial",
        "source": "NASA OMNI2 / NOAA SWPC",
        "url": "; ".join(item["url"] for item in products.values()),
        "retrieved_at_utc": now.isoformat(),
        "generated_at": now.isoformat(),
        "latest_record": products,
        "latest_record_fields": [item["latest"] for item in products.values()],
        "fallback_source": "NOAA SWPC solar-wind products",
        "attempt_errors": errors,
        "fallback_errors": fallback_errors,
        "prediction_weight": 0,
        "use_in_prediction": False,
        "note": "NASA OMNI2 no fue accesible; se conserva contexto NOAA SWPC de respaldo. Solo diagnóstico; peso 0 %.",
    }


def collect() -> dict:
    now = datetime.now(timezone.utc)
    errors = []
    for year in (now.year, now.year - 1):
        url = URL_TEMPLATE.format(year=year)
        try:
            payload = fetch(url).decode("ascii", "replace")
            lines = []
            for raw in payload.splitlines():
                fields = raw.split()
                if len(fields) >= 3:
                    try:
                        int(fields[0]); int(fields[1]); int(fields[2])
                    except ValueError:
                        continue
                    lines.append(raw.strip())
            if lines:
                latest = lines[-1]
                return {
                    "status": "ok",
                    "source": "NASA OMNI2",
                    "url": url,
                    "retrieved_at_utc": now.isoformat(),
                    "generated_at": now.isoformat(),
                    "latest_record": latest,
                    "latest_record_fields": latest.split(),
                    "prediction_weight": 0,
                    "use_in_prediction": False,
                    "note": "Contexto solar independiente; peso 0 % hasta calibración fuera de muestra.",
                }
        except Exception as exc:
            errors.append(f"{year}: {exc}")
    return collect_noaa_fallback(now, errors)


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        result = collect()
    except Exception as exc:
        result = {"status": "error", "source": "NASA OMNI2 / NOAA SWPC", "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "prediction_weight": 0, "use_in_prediction": False, "error": str(exc)}
    (DATA / "omni-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
