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
        "charName": "foF2,MUF(3000)F2,hmF2,fmin",
        "fromDate": start.strftime("%Y.%m.%d %H:%M:%S"),
        "toDate": now.strftime("%Y.%m.%d %H:%M:%S"),
    }
    url = "https://lgdc.uml.edu/common/DIDBGetValues?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent":"SOLUNET-HF-GIRO/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return url, r.read().decode("utf-8", errors="replace")

def parse_text(text: str):
    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.replace(",", " ").split()
        if len(parts) < 3:
            continue
        # Preserve a generic parsed row without inventing column semantics.
        rows.append({"raw": s, "tokens": parts})
    return rows[-10:]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=Path("public/data/giro-spain-summary.json"))
    ap.add_argument("--diagnostic", type=Path, default=Path("public/diagnostics/giro-diagnostic.json"))
    args = ap.parse_args()
    out = {"source":"GIRO/DIDBase cross-check","generated_at":now_iso(),"status":"partial","stations":{}}
    diag = {"generated_at":now_iso(),"status":"partial","errors":[]}
    for name, code in STATIONS.items():
        try:
            url, text = fetch_station(code)
            rows = parse_text(text)
            out["stations"][name] = {"ursi_code":code,"url":url,"parsed_rows":rows,"raw_excerpt":text[:4000]}
            if rows:
                out["status"] = "ok"
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
