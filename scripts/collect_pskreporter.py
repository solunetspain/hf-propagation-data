#!/usr/bin/env python3
"""
Optional PSKReporter observation collector for recent reports involving IN91.

PSKReporter applies rate limits and may change response formats. The collector
is deliberately conservative, performs one compact query and never breaks the
workflow. It publishes observations only when parseable.
"""
from __future__ import annotations
import argparse, json, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", type=Path, default=Path("public/data/pskreporter-hf-summary.json"))
    ap.add_argument("--diagnostic", type=Path, default=Path("public/diagnostics/pskreporter-diagnostic.json"))
    args = ap.parse_args()

    params = {
        "receiverLocator": "IN91",
        "flowStartSeconds": -3600,
        "rronly": 1,
    }
    url = "https://retrieve.pskreporter.info/query?" + urllib.parse.urlencode(params)
    out = {"source":"PSKReporter","generated_at":now_iso(),"status":"partial","query_url":url,"reports":[]}
    diag = {"generated_at":now_iso(),"status":"partial","errors":[]}
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"SOLUNET-HF-PSKReporter/1.0"})
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read()
            ctype = r.headers.get("Content-Type","")
        root = ET.fromstring(body)
        reports = []
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag in ("receptionReport","activeReceiver"):
                reports.append({"type":tag, **elem.attrib})
        out["reports"] = reports[:500]
        out["report_count"] = len(reports)
        out["content_type"] = ctype
        out["status"] = "ok" if reports else "partial"
    except Exception as e:
        diag["errors"].append(f"{type(e).__name__}: {e}")
    diag["status"] = out["status"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    args.diagnostic.write_text(json.dumps(diag, ensure_ascii=False, indent=2)+"\n",encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
