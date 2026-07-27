#!/usr/bin/env python3
"""Collect a lightweight NASA OMNI2 context snapshot.

This source is deliberately diagnostic-only: it is not mixed into the
prediction score until out-of-sample validation proves independent benefit.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA = Path("public/data")
URL_TEMPLATE = "https://omniweb.gsfc.nasa.gov/pub/omni2/omni2_{year}.dat"


def collect() -> dict:
    now = datetime.now(timezone.utc)
    errors = []
    selected_url = ""
    payload = ""
    for year in (now.year, now.year - 1):
        url = URL_TEMPLATE.format(year=year)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hf-propagation-data/1.0"})
            with urllib.request.urlopen(req, timeout=20) as response:
                candidate = response.read().decode("ascii", "replace")
            if candidate.strip():
                payload = candidate
                selected_url = url
                break
        except Exception as exc:
            errors.append(f"{year}: {exc}")
    lines = []
    for raw in payload.splitlines():
        fields = raw.split()
        if len(fields) >= 3:
            try:
                int(fields[0]); int(fields[1]); int(fields[2])
            except ValueError:
                continue
            lines.append(raw.strip())
    latest = lines[-1] if lines else ""
    fields = latest.split()
    result = {
        "status": "ok" if len(fields) >= 3 else "error",
        "source": "NASA OMNI2",
        "url": selected_url or URL_TEMPLATE.format(year=now.year),
        "retrieved_at_utc": now.isoformat(),
        "generated_at": now.isoformat(),
        "latest_record": latest,
        "latest_record_fields": fields,
        "prediction_weight": 0,
        "use_in_prediction": False,
        "note": "Independent solar-wind context only; no prediction weight until calibrated.",
    }
    if errors:
        result["attempt_errors"] = errors
    if not latest:
        result["error"] = "; ".join(errors) or "No se encontró un registro OMNI2 válido."
    return result


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        result = collect()
    except Exception as exc:
        result = {
            "status": "error",
            "source": "NASA OMNI2",
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "prediction_weight": 0,
            "use_in_prediction": False,
            "error": str(exc),
        }
    (DATA / "omni-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] in {"ok", "partial"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
