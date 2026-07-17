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
    # Centro exacto del locator Maidenhead IN91PO.
    "IN91PO_Nuez_de_Ebro": (41.6041667, -0.7083333),
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

def risk_level(score: int) -> str:
    return "alto" if score >= 4 else "medio" if score >= 2 else "bajo"

def risk_for(data: dict[str, Any]) -> dict[str, Any]:
    """Separate present conditions from the following six-hour forecast."""
    current = data.get("current", {})
    hourly = data.get("hourly", {})
    forecast_codes = list(hourly.get("weather_code", []))
    cape_values = [x for x in hourly.get("cape", []) if isinstance(x, (int, float))]
    precip_prob = [x for x in hourly.get("precipitation_probability", []) if isinstance(x, (int, float))]
    current_thunder = current.get("weather_code") in (95, 96, 99)
    forecast_thunder = any(code in (95, 96, 99) for code in forecast_codes)
    max_cape = max(cape_values) if cape_values else None
    max_prob = max(precip_prob) if precip_prob else None

    current_score = 4 if current_thunder else 0
    current_reasons = (
        ["weather_code actual de tormenta 95/96/99"]
        if current_thunder
        else ["sin código actual de tormenta"]
    )

    forecast_score = 0
    forecast_reasons = []
    if forecast_thunder:
        forecast_score += 3
        forecast_reasons.append("tormenta prevista por weather_code 95/96/99")
    if max_cape is not None and max_cape >= 1000:
        forecast_score += 2
        forecast_reasons.append(f"CAPE máximo previsto {max_cape:.0f} J/kg")
    elif max_cape is not None and max_cape >= 300:
        forecast_score += 1
        forecast_reasons.append(f"CAPE moderado previsto {max_cape:.0f} J/kg")
    if max_prob is not None and max_prob >= 70:
        forecast_score += 1
        forecast_reasons.append(f"probabilidad de precipitación prevista {max_prob:.0f}%")

    return {
        # Legacy aliases now explicitly represent the present, not the worst
        # condition predicted during the next six hours.
        "risk": risk_level(current_score),
        "score": current_score,
        "current_risk": {
            "risk": risk_level(current_score),
            "score": current_score,
            "thunderstorm_code_present": current_thunder,
            "reasons": current_reasons,
            "observation": current,
        },
        "forecast_6h": {
            "risk": risk_level(forecast_score),
            "score": forecast_score,
            "thunderstorm_code_present": forecast_thunder,
            "max_cape_j_kg": max_cape,
            "max_precipitation_probability": max_prob,
            "reasons": forecast_reasons,
            "times": hourly.get("time", []),
        },
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
        "classification": (
            "Modelled thunderstorm/QRN risk, not direct lightning detection. "
            "Current conditions and six-hour forecast are reported separately."
        ),
        "direct_lightning_detection_validated": False,
        "limitations": [
            "No direct lightning observations are used.",
            "Forecast CAPE never changes the current-risk classification.",
        ],
        "points": {},
    }
    diagnostic = {
        "generated_at": now_iso(),
        "status": "ok",
        "errors": [],
        "validation": {
            "model_response_received": False,
            "current_and_forecast_separated": True,
            "direct_lightning_observations_obtained": False,
        },
        "points": {},
    }

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
    diagnostic["validation"]["model_response_received"] = bool(result["points"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.diagnostic.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
