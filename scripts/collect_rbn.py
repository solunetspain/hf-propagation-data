#!/usr/bin/env python3
"""Collect optional live RBN spots without fabricating data."""
from __future__ import annotations

import json
import os
import re
import select
import socket
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BANDS = {
    "160m": (1.8, 2.0), "80m": (3.5, 4.0), "40m": (7.0, 7.3),
    "20m": (14.0, 14.35), "17m": (18.068, 18.168),
    "15m": (21.0, 21.45), "12m": (24.89, 24.99), "10m": (28.0, 29.7),
}
FLOAT_RE = re.compile(r"(?<![0-9.])([0-9]{1,2}(?:\.[0-9]{1,6})?)(?![0-9.])")


def band_for(freq_mhz: float) -> str | None:
    for band, (lo, hi) in BANDS.items():
        if lo <= freq_mhz <= hi:
            return band
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_spot(line: str) -> dict[str, object] | None:
    """Parse a conservative HF frequency from a cluster/RBN line."""
    for match in FLOAT_RE.finditer(line):
        try:
            frequency = float(match.group(1))
        except ValueError:
            continue
        band = band_for(frequency)
        if band:
            return {
                "raw": line[:500],
                "frequency_mhz": frequency,
                "band": band,
            }
    return None


def read_stream(conn: socket.socket, seconds: float) -> str:
    """Read a bounded Telnet stream; RBN is continuous, not one-shot."""
    conn.setblocking(False)
    chunks: list[bytes] = []
    deadline = __import__("time").monotonic() + seconds
    while __import__("time").monotonic() < deadline:
        remaining = max(0.1, deadline - __import__("time").monotonic())
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


def main() -> int:
    out = Path(os.getenv("RBN_OUTPUT", "public/data/rbn-spots.json"))
    diag = Path(os.getenv("RBN_DIAGNOSTIC", "public/diagnostics/rbn-diagnostic.json"))
    generated = now_iso()
    host = os.getenv("RBN_TELNET_HOST", "").strip()
    port = int(os.getenv("RBN_TELNET_PORT", "0") or 0)
    timeout = float(os.getenv("RBN_TELNET_TIMEOUT", "8") or 8)
    result = {
        "schema_version": "1.1",
        "source": "Reverse Beacon Network",
        "generated_at": generated,
        "status": "disabled",
        "transport": "telnet",
        "scope": "live spots only",
        "bands": {},
        "spots": [],
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
        },
        "interpretation": (
            "RBN indicates stations heard by skimmers. It does not prove a completed "
            "QSO or a direct destination route."
        ),
    }
    if host and port:
        diagnostic["validation"]["connection_attempted"] = True
        try:
            with socket.create_connection((host, port), timeout=timeout) as conn:
                conn.settimeout(timeout)
                # RBN Telnet servers may emit a login/prompt. A blank line is
                # harmless and helps servers begin streaming to an anonymous client.
                try:
                    conn.sendall(b"\n")
                except OSError:
                    pass
                data = read_stream(conn, timeout)
            diagnostic["validation"]["stream_read"] = True
            spots: list[dict[str, object]] = []
            seen: set[tuple[object, object]] = set()
            for line in data.splitlines():
                spot = parse_spot(line)
                if not spot:
                    continue
                key = (spot["frequency_mhz"], spot["raw"])
                if key in seen:
                    continue
                seen.add(key)
                spots.append(spot)
            result.update({
                "status": "ok" if spots else "partial",
                "spots": spots[:500],
                "bands": dict(Counter(str(s["band"]) for s in spots)),
                "limitation": None if spots else (
                    "Endpoint responded but no parseable HF spots were found in the "
                    "bounded Telnet window."
                ),
            })
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
