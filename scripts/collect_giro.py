#!/usr/bin/env python3
"""
Optional GIRO/DIDBase/FastChar cross-check.

The public DIDBase interface may change or throttle automated requests.
This collector never breaks the workflow: it records exact failures and
publishes only parsed measurements. It does not claim a station in IN91PO.
"""
from __future__ import annotations
import argparse, json, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATIONS = {
    "Roquetes": "EB040",
    "El_Arenosillo": "EA036",
}
PARAMETERS = ("foF2", "MUF(3000)F2", "hmF2", "foEs", "fmin")

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def fetch_station(code: str, hours: int = 6):
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    # FastChar/DIDBase expects one comma-separated characteristic list and
    # the legacy UTC date syntax used by its public servlet.
    from_date = start.strftime("%Y.%m.%d (...) %H:%M:%S")
    to_date = now.strftime("%Y.%m.%d (...) %H:%M:%S")
    char_names = ",".join(PARAMETERS)
    attempts = []
    for host in ("lgdc.uml.edu", "giro.uml.edu"):
        params = [
            ("ursiCode", code),
            ("charName", char_names),
            ("fromDate", from_date),
            ("toDate", to_date),
            ("DMUF", "3000"),
        ]
        url = "https://" + host + "/common/DIDBGetValues?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SOLUNET-HF-GIRO/1.3 (+https://previsionespropagacion.ea2ewl.es/)",
                "Accept": "text/plain,text/csv,*/*;q=0.5",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = getattr(response, "status", 200)
                rows = parse_text(body)
                attempt = {"url": url, "http_status": status, "bytes": len(body),
                           "parsed_rows": len(rows)}
                attempts.append(attempt)
                if rows:
                    return url, status, body, attempts
        except Exception as error:
            attempts.append({"url": url, "error": f"{type(error).__name__}: {error}"})
    raise RuntimeError(json.dumps({
        "message": "GIRO returned no parseable measurements",
        "attempts": attempts,
    }, ensure_ascii=False))

def parse_text(text: str):
    import re
    rows = []
    missing = {"-999", "-999.0", "9999", "9999.0", "---", "__", "//", "/_"}
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or raw.startswith("<"):
            continue
        tokens = raw.replace(",", " ").replace(";", " ").split()
        if not tokens:
            continue
        timestamp = tokens[0]
        value_tokens = []
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", timestamp):
            value_tokens = tokens[1:]
        elif len(tokens) > 1 and ":" in tokens[1] and re.match(r"^\d{4}[-.]\d{2}[-.]\d{2}$", timestamp):
            timestamp = f"{timestamp.replace('.', '-')}T{tokens[1]}Z"
            value_tokens = tokens[2:]
        else:
            continue
        numeric = []
        for token in value_tokens:
            clean = token.strip()
            if clean in missing:
                numeric.append(None)
                continue
            try:
                numeric.append(float(clean))
            except ValueError:
                continue
        if len(numeric) < 2:
            continue
        confidence = numeric[0]
        values = numeric[1:]
        measurements = {
            name: (values[index] if index < len(values) else None)
            for index, name in enumerate(PARAMETERS)
        }
        rows.append({
            "raw": raw,
            "timestamp_utc": timestamp if timestamp.endswith("Z") else timestamp + "Z",
            "confidence_score": confidence,
            "measurements": measurements,
        })
    return rows[-100:]
def summarize_rows(rows):
    valid = [
        row for row in rows
        if any(value is not None for value in row.get("measurements", {}).values())
    ]
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
    for name in PARAMETERS:
        current = latest_values.get(name)
        previous_value = previous_values.get(name)
        trends[name] = {
            "latest": current,
            "previous": previous_value,
            "delta": round(current - previous_value, 3)
            if isinstance(current, (int, float)) and isinstance(previous_value, (int, float))
            else None,
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
    out = {
        "source": "GIRO/DIDBase cross-check",
        "generated_at": now_iso(),
        "status": "partial",
        "stations": {},
        "parameters": list(PARAMETERS),
        "data_classification": "measured ionosonde values when parsed; unavailable values are not inferred",
    }
    diag = {
        "generated_at": now_iso(),
        "status": "partial",
        "errors": [],
        "parameters_requested": list(PARAMETERS),
        "interpretation": "GIRO values are measured by ionosonde when parsed; trend deltas compare two observations in the six-hour query window.",
    }
    for name, code in STATIONS.items():
        print(f"GIRO {name}/{code}: iniciando consulta", flush=True)
        try:
            url, http_status, text, attempts = fetch_station(code)
            rows = parse_text(text)
            print(f"GIRO {name}/{code}: HTTP {http_status}, bytes={len(text)}, filas_parseadas={len(rows)}", flush=True)
            summary = summarize_rows(rows)
            out["stations"][name] = {
                "ursi_code": code,
                "url": url,
                "http_status": http_status,
                "parsed_rows": rows,
                "summary": summary,
                "raw_excerpt": text[:4000],
                "attempts": attempts,
            }
            diag.setdefault("stations", {})[name] = summary
            if rows:
                out["status"] = "ok"
        except Exception as error:
            message = f"{name}/{code}: {type(error).__name__}: {error}"
            print(f"GIRO ERROR: {message}", flush=True)
            diag["errors"].append(message)
            out["stations"][name] = {"ursi_code": code, "status": "error", "error": message}
    diag["status"] = out["status"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.diagnostic.write_text(json.dumps(diag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
