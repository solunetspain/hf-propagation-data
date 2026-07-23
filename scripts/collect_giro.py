#!/usr/bin/env python3
"""
Optional GIRO/DIDBase cross-check.

The public DIDBase interface may change or throttle automated requests.
This collector never breaks the workflow: it records exact failures and
publishes only parsed measurements. It does not claim a station in IN91PO.
"""
from __future__ import annotations
import argparse, json, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Candidate nearby/relevant stations. Codes can be changed without touching report logic.
STATIONS = {
    "Roquetes": "EB040",
    "El_Arenosillo": "EA036",
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def fetch_station(code: str, hours: int = 6):
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    params = {
        "ursiCode": code,
        "charName": "foF2,MUF(3000)F2,hmF2,foEs,fmin",
        "fromDate": start.strftime("%Y.%m.%d %H:%M:%S"),
        "toDate": now.strftime("%Y.%m.%d %H:%M:%S"),
    }
    url = "https://lgdc.uml.edu/common/DIDBGetValues?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent":"SOLUNET-HF-GIRO/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return url, r.read().decode("utf-8", errors="replace")

def parse_text(text: str):
    """Parse DIDB rows conservatively while retaining the original line."""
    rows = []
    names = ("foF2", "MUF(3000)F2", "hmF2", "foEs", "fmin")
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        parts = raw.replace(",", " ").split()
        if len(parts) < 3:
            continue
        row = {"raw": raw, "tokens": parts}
        # DIDB commonly begins with date and time, followed by values in the
        # requested charName order. Missing/flagged values remain None.
        date_token = parts[0]
        time_token = parts[1] if len(parts) > 1 else ""
        if "." in date_token and ":" in time_token:
            row["timestamp_utc"] = f"{date_token} {time_token}Z"
            values = parts[2:]
            measurements = {}
            for index, name in enumerate(names):
                if index >= len(values):
                    measurements[name] = None
                    continue
                token = values[index].strip()
                try:
                    value = float(token)
                    measurements[name] = None if value in (-999, -999.0, 9999, 9999.0) else value
                except ValueError:
                    measurements[name] = None
            row["measurements"] = measurements
        rows.append(row)
    return rows[-100:]


def summarize_rows(rows):
    """Return latest values and a conservative 30-60 minute comparison."""
    valid = [row for row in rows if isinstance(row.get("measurements"), dict)]
    latest = valid[-1] if valid else {}
    comparison = {}
    if len(valid) >= 2:
        latest_time = latest.get("timestamp_utc")
        for previous in reversed(valid[:-1]):
            if previous.get("timestamp_utc") != latest_time:
                comparison = previous
                break
    latest_values = latest.get("measurements", {})
    previous_values = comparison.get("measurements", {}) if comparison else {}
    trends = {}
    for name in ("foF2", "MUF(3000)F2", "hmF2", "foEs", "fmin"):
        current = latest_values.get(name)
        previous_value = previous_values.get(name)
        trends[name] = {
            "latest": current,
            "previous": previous_value,
            "delta": round(current - previous_value, 3) if isinstance(current, (int, float)) and isinstance(previous_value, (int, float)) else None,
            "classification": "measured" if current is not None else "unavailable",
        }
    return {
        "latest_timestamp_utc": latest.get("timestamp_utc"),
        "latest_measurements": latest_values,
        "previous_timestamp_utc": comparison.get("timestamp_utc") if comparison else None,
        "trends": trends,
        "sample_count": len(valid),
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=Path("public/data/giro-spain-summary.json"))
    ap.add_argument("--diagnostic", type=Path, default=Path("public/diagnostics/giro-diagnostic.json"))
    args = ap.parse_args()
    out = {"source":"GIRO/DIDBase cross-check","generated_at":now_iso(),"status":"partial","stations":{},"parameters":["foF2","MUF(3000)F2","hmF2","foEs","fmin"],"data_classification":"measured ionosonde values when parsed; unavailable values are not inferred"}
    diag = {"generated_at":now_iso(),"status":"partial","errors":[],"parameters_requested":["foF2","MUF(3000)F2","hmF2","fmin"],"interpretation":"GIRO values are measured by ionosonde when parsed; trend deltas compare two observations in the six-hour query window."}
    for name, code in STATIONS.items():
        try:
            url, text = fetch_station(code)
            rows = parse_text(text)
            out["stations"][name] = {"ursi_code":code,"url":url,"parsed_rows":rows,"summary":summarize_rows(rows),"raw_excerpt":text[:4000]}
            if rows:
                out["status"] = "ok"
                diag.setdefault("stations", {})[name] = summarize_rows(rows)
        except Exception as e:
            diag["errors"].append(f"{name}/{code}: {type(e).__name__}: {e}")
    diag["status"] = out["status"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    args.diagnostic.write_text(json.dumps(diag, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
