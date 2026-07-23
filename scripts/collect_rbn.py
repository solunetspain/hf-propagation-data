#!/usr/bin/env python3
"""Collect optional live RBN spots without fabricating data.

RBN is a spot network, not a QSO database. A live endpoint must be supplied
through RBN_TELNET_HOST/RBN_TELNET_PORT; when it is absent the artifact is
explicitly marked disabled and the report must not count it as evidence.
"""
from __future__ import annotations

import json
import os
import socket
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BANDS = {"160m": (1.8, 2.0), "80m": (3.5, 4.0), "40m": (7.0, 7.3),
         "20m": (14.0, 14.35), "17m": (18.068, 18.168),
         "15m": (21.0, 21.45), "12m": (24.89, 24.99), "10m": (28.0, 29.7)}

def band_for(freq_mhz: float) -> str | None:
    for band, (lo, hi) in BANDS.items():
        if lo <= freq_mhz <= hi:
            return band
    return None

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def main() -> int:
    out = Path(os.getenv("RBN_OUTPUT", "public/data/rbn-spots.json"))
    diag = Path(os.getenv("RBN_DIAGNOSTIC", "public/diagnostics/rbn-diagnostic.json"))
    generated = now_iso()
    host = os.getenv("RBN_TELNET_HOST", "").strip()
    port = int(os.getenv("RBN_TELNET_PORT", "0") or 0)
    timeout = float(os.getenv("RBN_TELNET_TIMEOUT", "8") or 8)
    result = {
        "schema_version": "1.0", "source": "Reverse Beacon Network",
        "generated_at": generated, "status": "disabled",
        "transport": "telnet", "scope": "live spots only",
        "bands": {}, "spots": [], "limitation":
        "RBN live endpoint is not configured; no RBN evidence is counted."
    }
    diagnostic = {
        "generated_at": generated, "status": "disabled", "errors": [],
        "validation": {"endpoint_configured": bool(host and port),
                       "connection_attempted": False, "spots_parsed": 0},
        "interpretation": "RBN indicates stations heard by skimmers. It does not prove a completed QSO or a direct destination route."
    }
    if host and port:
        diagnostic["validation"]["connection_attempted"] = True
        try:
            with socket.create_connection((host, port), timeout=timeout) as conn:
                conn.settimeout(timeout)
                data = conn.recv(1024 * 1024).decode("utf-8", "replace")
            spots = []
            for line in data.splitlines():
                # Common cluster/RBN lines contain frequency and callsigns.
                fields = line.split()
                freq = next((float(x) for x in fields if x.replace(".", "", 1).isdigit() and 1.0 < float(x) < 30.0), None)
                if freq is None or not band_for(freq):
                    continue
                spots.append({"raw": line[:500], "frequency_mhz": freq, "band": band_for(freq)})
            result.update({"status": "ok" if spots else "partial", "spots": spots[:500],
                           "bands": dict(Counter(s["band"] for s in spots)),
                           "limitation": None if spots else "Endpoint responded but no parseable HF spots were found."})
            diagnostic["status"] = result["status"]
            diagnostic["validation"]["spots_parsed"] = len(spots)
        except Exception as exc:
            diagnostic["status"] = "error"
            diagnostic["errors"].append(f"{type(exc).__name__}: {exc}")
            result["status"] = "error"
            result["limitation"] = "RBN endpoint unavailable or response not parseable; RBN was excluded."
    out.parent.mkdir(parents=True, exist_ok=True)
    diag.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    diag.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
