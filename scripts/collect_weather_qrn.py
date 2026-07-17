#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

POINTS = {
    "IN91PO_Nuez_de_Ebro": (41.59, -0.22),
    "Galicia": (42.75, -8.40),
    "Cantabrico": (43.30, -3.00),
    "Centro": (40.42, -3.70),
    "Mediterraneo": (39.47, -0.38),
    "Andalucia": (37.39, -5.99),
    "Baleares": (39.57, 2.65),
    "Canarias": (28.10, -15.42),
}

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def fetch_point(lat: float, lon: float) -> dict[str, Any]:
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "weather_code,precipitation,rain,showers",
        "hourly": "weather_code,precipitation_probability,precipitation,rain,showers,cape",
        "forecast_hours": 6,
        "timezone": "UTC",
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "SOLUNET-HF-QRN/1.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))

def risk_for(data: dict[str, Any]) -> dict[str, Any]:
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    codes = [current.get("weather_code")] + list(hourly.get("weather_code", []))
    cape_values = [x for x in hourly.get("cape", []) if isinstance(x, (int, float))]
    precip_prob = [x for x in hourly.get("precipitation_probability", []) if isinstance(x, (int, float))]
    thunder = any(code in (95, 96, 99) for code in codes)
    max_cape = max(cape_values) if cape_values else None
    max_prob = max(precip_prob) if precip_prob else None

    score = 0
    reasons = []
    if thunder:
        score += 3
        reasons.append("weather_code de tormenta 95/96/99")
    if max_cape is not None and max_cape >= 1000:
        score += 2
        reasons.append(f"CAPE máximo {max_cape:.0f} J/kg")
    elif max_cape is not None and max_cape >= 300:
        score += 1
        reasons.append(f"CAPE moderado {max_cape:.0f} J/kg")
    if max_prob is not None and max_prob >= 70:
        score += 1
        reasons.append(f"probabilidad precipitación {max_prob:.0f}%")

    level = "alto" if score >= 4 else "medio" if score >= 2 else "bajo"
    return {
        "risk": level,
        "score": score,
        "thunderstorm_code_present": thunder,
        "max_cape_j_kg_6h": max_cape,
        "max_precipitation_probability_6h": max_prob,
        "reasons": reasons,
        "current": current,
    }

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("public/data/qrn-spain-summary.json"))
    parser.add_argument("--diagnostic", type=Path, default=Path("public/diagnostics/qrn-diagnostic.json"))
    args = parser.parse_args()

    result = {
        "source": "Open-Meteo model-based QRN risk",
        "generated_at": now_iso(),
        "status": "ok",
        "classification": "Modelled thunderstorm/QRN risk, not direct lightning detection.",
        "points": {},
    }
    diagnostic = {"generated_at": now_iso(), "status": "ok", "errors": [], "points": {}}

    for name, (lat, lon) in POINTS.items():
        try:
            data = fetch_point(lat, lon)
            result["points"][name] = {
                "latitude": lat,
                "longitude": lon,
                **risk_for(data),
            }
            diagnostic["points"][name] = {"status": "ok"}
        except Exception as exc:  # noqa: BLE001
            diagnostic["points"][name] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
            diagnostic["errors"].append(f"{name}: {exc}")

    if len(result["points"]) < 4:
        result["status"] = "partial"
        diagnostic["status"] = "partial"
    if not result["points"]:
        result["status"] = "error"
        diagnostic["status"] = "error"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.diagnostic.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
