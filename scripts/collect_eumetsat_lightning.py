#!/usr/bin/env python3
"""Collect recent MTG-LI filtered lightning events for Spain regions.

Requires EUMETSAT_CONSUMER_KEY and EUMETSAT_CONSUMER_SECRET.
This first adapter records product discovery and fails closed until the
NetCDF event decoder is verified against the current LI-2-LEF schema.
"""
from __future__ import annotations
import json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

COLLECTION = "EO:EUM:DAT:0690"

def main() -> int:
    out = Path("public/data/eumetsat-lightning.json")
    key = os.environ.get("EUMETSAT_CONSUMER_KEY")
    secret = os.environ.get("EUMETSAT_CONSUMER_SECRET")
    result = {
        "source": "EUMETSAT MTG-LI",
        "collection": COLLECTION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "unavailable",
        "regional_validation": "pending",
        "regions": {"peninsula": None, "baleares": None, "canarias": None},
    }
    if not key or not secret:
        result["reason"] = "EUMETSAT credentials not configured"
    else:
        result["reason"] = "NetCDF LI-2-LEF decoder not yet verified"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
