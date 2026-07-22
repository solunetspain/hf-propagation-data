#!/usr/bin/env python3
"""Persist and score mature HF predictions against later PSKReporter/DXView observations."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

REGIONS = ("peninsula", "baleares", "canarias")
BANDS = ("160m", "80m", "40m", "20m", "17m", "15m", "12m", "10m")
BAND_KEYS = {"160m":"0","80m":"3","40m":"7","20m":"14","17m":"18","15m":"21","12m":"24","10m":"28"}
WINDOW_MINUTES = 90
MAX_CYCLES = 10000
MIN_CONFIRMING_OBSERVATIONS = 3
METHOD_VERSION = "2.0-minimum-three-observations"

def load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default

def nested(value, *keys, default=None):
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value

def count_observations(psk, dx, region, band):
    psk_count = nested(psk, "regions", region, "bands", band, "report_count", default=0) or 0
    dx_median = nested(dx, "regions", region, "bands", BAND_KEYS[band], "activity_zone_count", "median", default=0) or 0
    try:
        return int(psk_count) + (1 if float(dx_median) > 0 else 0)
    except (TypeError, ValueError):
        return int(psk_count) if isinstance(psk_count, (int, float)) else 0

def iso_datetime(value, fallback):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return fallback

def normalize_band(value):
    if value is None:
        return None
    return str(value).replace(" ", "").lower()

def classify(predicted, alternative, observed):
    """Classify only when the regional evidence reaches the minimum threshold."""
    try:
        confirmed = int(observed) >= MIN_CONFIRMING_OBSERVATIONS
    except (TypeError, ValueError):
        confirmed = False
    if predicted and confirmed:
        return "hit"
    if alternative and confirmed:
        return "partial"
    if predicted:
        return "failure"
    return "not_evaluated"

def empty_item():
    return {"observations_processed": 0, "observations_total": 0, "hits": 0, "partial": 0, "failures": 0, "reliability_pct": None}

def main():
    data = Path("public/data")
    history_path = data / "prediction-history.json"
    report = load(data / "web-report-es.json", {})
    psk = load(data / "pskreporter-hf-regions.json", {})
    dx = load(data / "dxview-regions-summary.json", {})
    history = load(history_path, {"schema_version": "1.0", "window_minutes": WINDOW_MINUTES, "max_cycles": MAX_CYCLES, "entries": []})
    now = datetime.now(timezone.utc)
    entries = history.get("entries", [])
    if not isinstance(entries, list):
        entries = []
    # Re-evaluate old entries when the scoring method changes.
    if history.get("method_version") != METHOD_VERSION:
        for entry in entries:
            if isinstance(entry, dict):
                entry["evaluation"] = None

    recommendations = nested(report, "prediction_model", "recommendations", default={})
    current = {
        "generated_at_utc": report.get("generated_at_utc", now.isoformat()),
        "recommendations": recommendations,
        "observations_at_prediction": {
            region: {band: count_observations(psk, dx, region, band) for band in BANDS}
            for region in REGIONS
        },
        "evaluation": None,
    }

    for entry in entries:
        if entry.get("evaluation") is not None:
            continue
        created = iso_datetime(entry.get("generated_at_utc"), now)
        if (now - created).total_seconds() < WINDOW_MINUTES * 60:
            continue
        evaluation = {}
        old_recommendations = entry.get("recommendations", {})
        for region in REGIONS:
            first = nested(old_recommendations, region, "first", default=None)
            alternative = nested(old_recommendations, region, "alternative", default=None)
            # Accept the existing list form for compatibility with earlier entries.
            if isinstance(nested(old_recommendations, region, default=None), list):
                bands = nested(old_recommendations, region, default=[])
                first = bands[0] if bands else None
                alternative = bands[1] if len(bands) > 1 else None
            evaluation[region] = {}
            for band in BANDS:
                observed = count_observations(psk, dx, region, band)
                result = classify(normalize_band(band) == normalize_band(first), normalize_band(band) == normalize_band(alternative), observed)
                evaluation[region][band] = {"result": result, "observations": observed, "evaluated_at_utc": now.isoformat()}
        entry["evaluation"] = evaluation

    entries.append(current)
    entries = entries[-MAX_CYCLES:]
    summary = {}
    for region in REGIONS:
        summary[region] = {}
        for band in BANDS:
            item = empty_item()
            for entry in entries:
                result = nested(entry, "evaluation", region, band, "result", default="not_evaluated")
                if result == "not_evaluated":
                    continue
                observed = nested(entry, "evaluation", region, band, "observations", default=0) or 0
                item["observations_processed"] += 1
                item["observations_total"] += int(observed)
                item["hits"] += result == "hit"
                item["partial"] += result == "partial"
                item["failures"] += result == "failure"
            if item["observations_processed"]:
                item["reliability_pct"] = round((item["hits"] + 0.5 * item["partial"]) / item["observations_processed"] * 100)
            summary[region][band] = item

    totals = {}
    for region in REGIONS:
        totals[region] = empty_item()
        for band in BANDS:
            item = summary[region][band]
            for key in ("observations_processed", "observations_total", "hits", "partial", "failures"):
                totals[region][key] += item[key]
        if totals[region]["observations_processed"]:
            totals[region]["reliability_pct"] = round((totals[region]["hits"] + 0.5 * totals[region]["partial"]) / totals[region]["observations_processed"] * 100)
    total = empty_item()
    for region in REGIONS:
        for key in ("observations_processed", "observations_total", "hits", "partial", "failures"):
            total[key] += totals[region][key]
    if total["observations_processed"]:
        total["reliability_pct"] = round((total["hits"] + 0.5 * total["partial"]) / total["observations_processed"] * 100)

    history.update({
        "schema_version": "1.0",
        "window_minutes": WINDOW_MINUTES,
        "max_cycles": MAX_CYCLES,
        "method_version": METHOD_VERSION,
        "minimum_confirming_observations": MIN_CONFIRMING_OBSERVATIONS,
        "generated_at_utc": now.isoformat(),
        "entries": entries,
        "summary": summary,
        "regional_totals": totals,
        "total": total,
        "method": "first recommendation=hit and alternative=partial only with at least three regional observations after 90 minutes; otherwise the first recommendation is a failure; other bands are not evaluated",
        "sources": ["PSKReporter", "DXView"],
    })
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
