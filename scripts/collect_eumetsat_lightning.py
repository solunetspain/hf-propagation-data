#!/usr/bin/env python3
"""Download and decode recent EUMETSAT MTG-LI lightning events by region."""
from __future__ import annotations
import json, os, shutil, signal, tempfile, time, zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

COLLECTION = "EO:EUM:DAT:0690"
BOXES = {
    "peninsula": (35.0, 44.5, -10.0, 4.5),
    "baleares": (38.5, 40.3, 1.0, 4.5),
    "canarias": (27.0, 29.5, -18.5, -13.0),
}

class DownloadTimeout(Exception):
    pass

def timeout_handler(signum, frame):
    raise DownloadTimeout("bounded MTG-LI download exceeded 240 seconds")

def find_variables(group, found=None):
    found = found or {}
    for name, var in getattr(group, "variables", {}).items():
        key = name.lower()
        standard = str(getattr(var, "standard_name", "")).lower()
        long_name = str(getattr(var, "long_name", "")).lower()
        if key in {"latitude", "lat"} or "latitude" in key or standard == "latitude" or "latitude" in long_name:
            found.setdefault("latitude", var)
        if key in {"longitude", "lon", "long"} or "longitude" in key or standard == "longitude" or "longitude" in long_name:
            found.setdefault("longitude", var)
    for child in getattr(group, "groups", {}).values():
        find_variables(child, found)
    return found

def decode_netcdf(path: Path):
    import numpy as np
    from netCDF4 import Dataset
    with Dataset(str(path), "r") as ds:
        vars_ = find_variables(ds)
        lat = next((vars_[k][:] for k in ("latitude", "lat") if k in vars_), None)
        lon = next((vars_[k][:] for k in ("longitude", "lon", "long") if k in vars_), None)
        if lat is None or lon is None:
            raise ValueError("NetCDF latitude/longitude variables not found; available=" + ",".join(sorted(vars_)))
        lat = np.asarray(lat).reshape(-1)
        lon = np.asarray(lon).reshape(-1)
        n = min(lat.size, lon.size)
        lat, lon = lat[:n], lon[:n]
        valid = np.isfinite(lat) & np.isfinite(lon)
        counts = {}
        for name, (south, north, west, east) in BOXES.items():
            counts[name] = int(np.count_nonzero(valid & (lat >= south) & (lat <= north) & (lon >= west) & (lon <= east)))
        return counts, n

def decode_product(path: Path):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as zf:
                zf.extractall(root)
            candidates = list(root.rglob("*.nc")) + list(root.rglob("*.nc4"))
        else:
            candidates = [path]
        if not candidates:
            raise ValueError("downloaded product contains no NetCDF file")
        return decode_netcdf(candidates[0])

def main() -> int:
    out = Path("public/data/eumetsat-lightning.json")
    result = {
        "source": "EUMETSAT MTG-LI",
        "collection": COLLECTION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "unavailable",
        "regional_validation": "pending",
        "regions": {"peninsula": None, "baleares": None, "canarias": None},
    }
    key, secret = os.environ.get("EUMETSAT_CONSUMER_KEY"), os.environ.get("EUMETSAT_CONSUMER_SECRET")
    if not key or not secret:
        result["reason"] = "EUMETSAT credentials not configured"
    else:
        try:
            import eumdac
            for attempt in range(3):
                try:
                    datastore = eumdac.DataStore(eumdac.AccessToken((key, secret)))
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
            collection = datastore.get_collection(COLLECTION)
            end = datetime.now(timezone.utc)
            product = next(iter(collection.search(dtstart=end - timedelta(minutes=30), dtend=end)))
            result["product"] = str(product)
            entries = list(product.entries)
            netcdf_entries = [name for name in entries if str(name).lower().endswith((".nc", ".nc4")) and "chk-" not in str(name).lower() and "manifest" not in str(name).lower()]
            if not netcdf_entries:
                raise ValueError("product manifest contains no NetCDF event entry; entries=" + "|".join(map(str, entries)))
            entry = netcdf_entries[0]
            result["entry"] = entry
            with tempfile.TemporaryDirectory() as td:
                target = Path(td) / Path(entry).name
                old_handler = signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(240)
                try:
                    with product.open(entry=entry) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, old_handler)
                result["downloaded_bytes"] = target.stat().st_size
                counts, event_count = decode_product(target)
            result["regions"] = counts
            result["event_count"] = event_count
            result["status"] = "decoded"
            result["regional_validation"] = "validated"
            result["reason"] = "MTG-LI NetCDF coordinates decoded and counted by region"
        except Exception as exc:
            result["reason"] = f"{type(exc).__name__}: {exc}"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
