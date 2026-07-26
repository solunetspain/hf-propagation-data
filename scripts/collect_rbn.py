#!/usr/bin/env python3
"""Collect optional RBN spots and regionalize receivers only with verified location data."""
from __future__ import annotations

import json
import os
import re
import select
import socket
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BANDS = {
    "160m": (1.8, 2.0), "80m": (3.5, 4.0), "40m": (7.0, 7.3),
    "20m": (14.0, 14.35), "17m": (18.068, 18.168),
    "15m": (21.0, 21.45), "12m": (24.89, 24.99), "10m": (28.0, 29.7),
}
FREQUENCY_TOKEN_RE = re.compile(r"^(\d+(?:\.\d+)?)$")
CALLSIGN_RE = re.compile(r"^[A-Z0-9]{2,}(?:/[A-Z0-9]+)*$")
REGIONS = ("Península", "Baleares", "Canarias")


def band_for(freq_mhz: float) -> str | None:
    for band, (lo, hi) in BANDS.items():
        if lo <= freq_mhz <= hi:
            return band
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_frequency_token(token: str) -> float | None:
    token = token.strip()
    if not FREQUENCY_TOKEN_RE.fullmatch(token):
        return None
    try:
        value = float(token)
    except ValueError:
        return None
    frequency_mhz = value / 1000.0 if value >= 1000 else value
    return frequency_mhz if band_for(frequency_mhz) else None


def maidenhead_center(locator: str) -> tuple[float, float] | None:
    loc = locator.strip().upper()
    if len(loc) not in (4, 6, 8) or not re.fullmatch(r"[A-R]{2}[0-9]{2}[A-X]{2}([0-9]{2})?", loc):
        return None
    lon = -180.0 + (ord(loc[0]) - 65) * 20 + int(loc[2]) * 2
    lat = -90.0 + (ord(loc[1]) - 65) * 10 + int(loc[3])
    lon += (ord(loc[4]) - 65) * (5 / 60) + (5 / 120)
    lat += (ord(loc[5]) - 65) * (2.5 / 60) + (2.5 / 120)
    if len(loc) == 8:
        lon += int(loc[6]) * (5 / 600) + (5 / 1200)
        lat += int(loc[7]) * (2.5 / 600) + (2.5 / 1200)
    return lat, lon


def region_from_coordinates(lat: float | None, lon: float | None) -> str | None:
    if lat is None or lon is None:
        return None
    if 27.0 <= lat <= 29.5 and -18.5 <= lon <= -13.0:
        return "Canarias"
    if 38.5 <= lat <= 40.2 and 1.0 <= lon <= 4.5:
        return "Baleares"
    if 35.0 <= lat <= 44.5 and -10.5 <= lon <= 4.8:
        return "Península"
    return None


def looks_like_callsign(token: str) -> bool:
    token = token.strip(" ,;:()[]")
    return bool(CALLSIGN_RE.fullmatch(token)) and any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


def parse_spot(line: str) -> dict[str, object] | None:
    if not line.lstrip().upper().startswith("DX DE "):
        return None
    fields = line.split()
    receiver = fields[2].rstrip(":") if len(fields) > 2 else ""
    frequency_mhz = None
    freq_index = None
    for index, token in enumerate(fields[3:], start=3):
        frequency_mhz = parse_frequency_token(token)
        if frequency_mhz is not None:
            freq_index = index
            break
    if frequency_mhz is None:
        return None
    heard = next((t.strip(" ,;:()[]") for t in fields[freq_index + 1:] if looks_like_callsign(t)), None)
    return {
        "raw": line[:500],
        "receiver_callsign": receiver or None,
        "heard_callsign": heard,
        "receiver_region": None,
        "receiver_locator": None,
        "receiver_lookup_source": None,
        "receiver_region_confidence": "unassigned",
        "frequency_mhz": frequency_mhz,
        "band": band_for(frequency_mhz),
    }


def read_stream(conn: socket.socket, seconds: float) -> str:
    conn.setblocking(False)
    chunks: list[bytes] = []
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        remaining = max(0.1, deadline - time.monotonic())
        readable, _, _ = select.select([conn], [], [], min(1.0, remaining))
        if not readable:
            continue
        try:
            chunk = conn.recv(65536)
        except BlockingIOError:
            continue
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks).decode("utf-8", "replace")


class CallbookResolver:
    """QRZ first, HamQTH fallback, persistent cache, no prefix-only geography."""

    def __init__(self, cache_path: Path, timeout: float = 5.0):
        self.cache_path = cache_path
        self.timeout = timeout
        self.cache = self._load()
        self.qrz_session: str | None = None
        self.qrz_attempted = 0
        self.hamqth_attempted = 0
        self.hamqth_used = 0
        self.unresolved = 0

    def _load(self) -> dict[str, dict[str, object]]:
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self.cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def _text(root: ET.Element, name: str) -> str | None:
        node = root.find(".//" + name)
        value = node.text.strip() if node is not None and node.text else ""
        return value or None

    def _request_xml(self, url: str) -> ET.Element:
        request = urllib.request.Request(url, headers={"User-Agent": "EA2EWL-HF-RBN/1.0"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return ET.fromstring(response.read())

    def _qrz(self, callsign: str) -> dict[str, object] | None:
        username = os.getenv("QRZ_USERNAME", "").strip()
        password = os.getenv("QRZ_PASSWORD", "").strip() or os.getenv("QRZ_API_KEY", "").strip()
        if not username or not password:
            return None
        self.qrz_attempted += 1
        if not self.qrz_session:
            params = urllib.parse.urlencode({"username": username, "password": password, "agent": "EA2EWL-HF-RBN/1.0"})
            root = self._request_xml("https://xmldata.qrz.com/xml/current/?op=logbook&" + params)
            error = self._text(root, "Error")
            self.qrz_session = self._text(root, "Key")
            if error or not self.qrz_session:
                return None
        query = urllib.parse.urlencode({"s": self.qrz_session, "callsign": callsign})
        root = self._request_xml("https://xmldata.qrz.com/xml/current/?op= callsign" .replace("op= ", "op=") + "&" + query)
        locator = self._text(root, "grid") or self._text(root, "grid_square")
        return self._location_record(locator, "QRZ")

    def _hamqth(self, callsign: str) -> dict[str, object] | None:
        username = os.getenv("HAMQTH_USERNAME", "").strip()
        password = os.getenv("HAMQTH_PASSWORD", "").strip()
        if not username or not password:
            return None
        self.hamqth_attempted += 1
        params = urllib.parse.urlencode({"user": username, "pwd": password, "callsign": callsign, "prg": "EA2EWL-HF-RBN"})
        root = self._request_xml("https://www.hamqth.com/xml.php?" + params)
        locator = self._text(root, "grid") or self._text(root, "grid_square")
        return self._location_record(locator, "HamQTH")

    @staticmethod
    def _location_record(locator: str | None, source: str) -> dict[str, object] | None:
        point = maidenhead_center(locator) if locator else None
        if not point:
            return None
        lat, lon = point
        region = region_from_coordinates(lat, lon)
        if not region:
            return None
        return {"locator": locator.upper(), "latitude": round(lat, 5), "longitude": round(lon, 5), "region": region, "source": source}

    def resolve(self, callsign: str | None) -> dict[str, object] | None:
        key = (callsign or "").upper().strip()
        if not key:
            self.unresolved += 1
            return None
        cached = self.cache.get(key)
        if isinstance(cached, dict) and cached.get("region") and cached.get("locator"):
            return cached
        try:
            record = self._qrz(key)
        except Exception:
            record = None
        if record is None:
            try:
                record = self._hamqth(key)
            except Exception:
                record = None
            if record:
                self.hamqth_used += 1
        if record:
            self.cache[key] = record
            return record
        self.unresolved += 1
        self.cache[key] = {"region": None, "locator": None, "source": "unresolved", "updated_at": now_iso()}
        return None


def main() -> int:
    out = Path(os.getenv("RBN_OUTPUT", "public/data/rbn-spots.json"))
    diag = Path(os.getenv("RBN_DIAGNOSTIC", "public/diagnostics/rbn-diagnostic.json"))
    cache_path = Path(os.getenv("RBN_CALLBOOK_CACHE", "public/data/rbn-callsign-cache.json"))
    generated = now_iso()
    host = os.getenv("RBN_TELNET_HOST", "").strip()
    port = int(os.getenv("RBN_TELNET_PORT", "0") or 0)
    callsign = os.getenv("RBN_TELNET_CALLSIGN", "").strip()
    timeout = float(os.getenv("RBN_TELNET_TIMEOUT", "8") or 8)
    result = {
        "schema_version": "1.3",
        "source": "Reverse Beacon Network",
        "generated_at": generated,
        "status": "disabled",
        "transport": "telnet",
        "scope": "live spots only",
        "bands": {},
        "spots": [],
        "regions": {},
        "regional_attribution": "Only QRZ or HamQTH locator coordinates are used; unresolved spots remain global.",
        "global_unassigned_spots": 0,
        "quality_gate": {"minimum_spots": 5, "minimum_distinct_receivers": 3, "eligible_for_auxiliary_weight": False},
        "limitation": "RBN live endpoint is not configured; no RBN evidence is counted.",
    }
    diagnostic = {
        "generated_at": generated,
        "status": "disabled",
        "errors": [],
        "validation": {
            "endpoint_configured": bool(host and port),
            "connection_attempted": False,
            "stream_read": False,
            "spots_parsed": 0,
            "regional_spots_attributed": 0,
            "distinct_receivers": 0,
            "qrz_lookups": 0,
            "hamqth_fallback_lookups": 0,
            "unresolved_callbooks": 0,
        },
        "interpretation": "RBN reports skimmer receptions, not completed QSOs. Regional use requires a verified locator.",
    }
    if host and port:
        diagnostic["validation"]["connection_attempted"] = True
        resolver = CallbookResolver(cache_path, timeout=min(timeout, 6))
        try:
            with socket.create_connection((host, port), timeout=timeout) as conn:
                conn.settimeout(timeout)
                try:
                    conn.sendall((callsign + "\n").encode("ascii", "ignore") if callsign else b"\n")
                except OSError:
                    pass
                data = read_stream(conn, timeout)
            diagnostic["validation"]["stream_read"] = True
            spots: list[dict[str, object]] = []
            seen: set[tuple[object, object, object]] = set()
            for line in data.splitlines():
                spot = parse_spot(line)
                if not spot:
                    continue
                key = (spot["receiver_callsign"], spot["frequency_mhz"], spot["heard_callsign"])
                if key in seen:
                    continue
                seen.add(key)
                location = resolver.resolve(str(spot["receiver_callsign"]) if spot.get("receiver_callsign") else None)
                if location:
                    spot.update({
                        "receiver_region": location["region"],
                        "receiver_locator": location["locator"],
                        "receiver_lookup_source": location["source"],
                        "receiver_region_confidence": "callbook_locator",
                    })
                spots.append(spot)
            regional = {}
            for region in REGIONS:
                selected = [s for s in spots if s.get("receiver_region") == region]
                regional[region] = {
                    "report_count": len(selected),
                    "distinct_receivers": len({s.get("receiver_callsign") for s in selected if s.get("receiver_callsign")}),
                    "bands": dict(Counter(str(s["band"]) for s in selected)),
                }
            unassigned = sum(1 for s in spots if not s.get("receiver_region"))
            eligible = any(v["report_count"] >= 5 and v["distinct_receivers"] >= 3 for v in regional.values())
            result.update({
                "status": "ok" if spots else "partial",
                "spots": spots[:500],
                "bands": dict(Counter(str(s["band"]) for s in spots)),
                "regions": regional,
                "global_unassigned_spots": unassigned,
                "quality_gate": {"minimum_spots": 5, "minimum_distinct_receivers": 3, "eligible_for_auxiliary_weight": eligible},
                "limitation": None if spots else "Endpoint responded but no parseable HF spots were found in the bounded Telnet window.",
            })
            diagnostic["status"] = result["status"]
            diagnostic["validation"].update({
                "spots_parsed": len(spots),
                "regional_spots_attributed": sum(v["report_count"] for v in regional.values()),
                "distinct_receivers": len({s.get("receiver_callsign") for s in spots if s.get("receiver_callsign")}),
                "qrz_lookups": resolver.qrz_attempted,
                "hamqth_fallback_lookups": resolver.hamqth_attempted,
                "unresolved_callbooks": resolver.unresolved,
            })
            if resolver.hamqth_used:
                diagnostic["fallback_notice"] = "HamQTH se usó como respaldo para algunas búsquedas porque QRZ no devolvió una ubicación utilizable."
                result["fallback_notice"] = diagnostic["fallback_notice"]
        except Exception as exc:
            diagnostic["status"] = "error"
            diagnostic["errors"].append(f"{type(exc).__name__}: {exc}")
            result["status"] = "error"
            result["limitation"] = "RBN endpoint unavailable or response not parseable; RBN was excluded."
        finally:
            resolver.save()
    out.parent.mkdir(parents=True, exist_ok=True)
    diag.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    diag.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
