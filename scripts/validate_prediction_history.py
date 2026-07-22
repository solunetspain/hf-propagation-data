#!/usr/bin/env python3
"""Persist and score the HF prediction history using PSKReporter and DXView."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

REGIONS = ("peninsula", "baleares", "canarias")
BANDS = ("160m", "80m", "40m", "20m", "17m", "15m", "12m", "10m")
BAND_KEYS = {"160m":"0","80m":"3","40m":"7","20m":"14","17m":"18","15m":"21","12m":"24","10m":"28"}
MAX_OBSERVATIONS = 10000
WINDOW_MINUTES = 90

def load(path: Path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return default

def nested(value, *keys, default=None):
    for key in keys:
        if not isinstance(value, dict): return default
        value = value.get(key)
    return default if value is None else value

def count_observations(psk, dx, region, band):
    p = nested(psk, "regions", region, "bands", band, "report_count", default=0) or 0
    d = nested(dx, "regions", region, "bands", BAND_KEYS[band], "activity_zone_count", "median", default=0) or 0
    try: return int(p) + (1 if float(d) > 0 else 0)
    except (TypeError, ValueError): return int(p) if isinstance(p, (int,float)) else 0

def main():
    root = Path("public")
    history_path = root / "data" / "prediction-history.json"
    report = load(root / "data" / "web-report-es.json", {})
    psk = load(root / "data" / "pskreporter-hf-regions.json", {})
    dx = load(root / "data" / "dxview-regions-summary.json", {})
    history = load(history_path, {"schema_version":"1.0","window_minutes":WINDOW_MINUTES,"max_observations":MAX_OBSERVATIONS,"entries":[]})
    now = datetime.now(timezone.utc)
    entries = history.get("entries", [])
    prediction = {"generated_at_utc": report.get("generated_at_utc", now.isoformat()), "recommendations": report.get("prediction_model", {}).get("recommendations", {}), "regions": {}}
    for region in REGIONS:
        prediction["regions"][region] = {}
        for band in BANDS:
            prediction["regions"][region][band] = {"observations": count_observations(psk, dx, region, band)}
    entries.append(prediction)
    entries = entries[-MAX_OBSERVATIONS:]
    summary = {}
    for region in REGIONS:
        summary[region] = {}
        for band in BANDS:
            values = [e["regions"][region][band]["observations"] for e in entries if region in e.get("regions", {}) and band in e["regions"][region]]
            mature = [e for e in entries if (now - datetime.fromisoformat(e.get("generated_at_utc", now.isoformat()).replace("Z","+00:00"))).total_seconds() >= WINDOW_MINUTES*60]\n            first = nested(mature[-1] if mature else {}, "recommendations", region, default=[])\n            expected = band in first if isinstance(first, list) else False\n            observed = sum(1 for e in mature if e["regions"][region][band]["observations"] > 0)\n            processed = min(len(mature), MAX_OBSERVATIONS)\n            summary[region][band] = {"observations_processed": processed, "observations_total": sum(values), "hits": observed if expected else 0, "partial": 0, "failures": max(0, processed-observed) if expected else 0}
    history.update({"generated_at_utc": now.isoformat(), "entries": entries, "summary": summary})
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
if __name__ == "__main__": main()
