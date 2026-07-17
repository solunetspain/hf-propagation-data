#!/usr/bin/env python3
"""
Consulta el feed XML público de HamQSL/N0NBH y publica un JSON normalizado.

Si HamQSL devuelve una página que exige JavaScript o cualquier contenido no XML,
el script NO inventa datos: publica un diagnóstico preciso y conserva opcionalmente
el último resumen válido, marcado como obsoleto.

Solo usa la biblioteca estándar de Python.
"""

from __future__ import annotations

import argparse
import json
import shutil
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_URL = "https://www.hamqsl.com/solarxml.php"
DEFAULT_OUTPUT = Path("data/hamqsl-summary.json")
DEFAULT_DIAGNOSTIC = Path("diagnostics/hamqsl-diagnostic.json")
DEFAULT_LAST_GOOD = Path("data/hamqsl-last-good.json")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean_tag(tag: str) -> str:
    return tag.split("}", 1)[-1].strip().lower()


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def xml_to_flat_map(root: ET.Element) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for elem in root.iter():
        text = clean_text(elem.text)
        if text:
            result.setdefault(clean_tag(elem.tag), []).append(text)
    return result


def pick(flat: dict[str, list[str]], *names: str) -> str | None:
    for name in names:
        values = flat.get(name.lower())
        if values:
            return values[0]
    return None


def parse_number(value: str | None) -> int | float | None:
    if value is None:
        return None
    cleaned = value.strip().replace(",", ".")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def parse_band_conditions(root: ET.Element) -> dict[str, Any]:
    result: dict[str, Any] = {"day": {}, "night": {}}
    for elem in root.iter():
        tag = clean_tag(elem.tag)
        if tag not in {"band", "condition"}:
            continue
        attrs = {clean_tag(k): v for k, v in elem.attrib.items()}
        name = attrs.get("name") or attrs.get("band")
        time_name = (attrs.get("time") or attrs.get("period") or "").lower()
        value = clean_text(elem.text) or attrs.get("condition") or attrs.get("value")
        if not name or not value:
            continue
        target = "night" if "night" in time_name else "day" if "day" in time_name else None
        if target:
            result[target][name] = value
    return result


def classify_non_xml(body: bytes, content_type: str) -> str:
    text = body[:5000].decode("utf-8", errors="replace").lower()
    if "javascript is required" in text:
        return "javascript_challenge"
    if "cloudflare" in text or "attention required" in text:
        return "anti_bot_challenge"
    if "text/html" in content_type.lower() or "<html" in text:
        return "html_instead_of_xml"
    return "non_xml_response"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--diagnostic", type=Path, default=DEFAULT_DIAGNOSTIC)
    parser.add_argument("--last-good", type=Path, default=DEFAULT_LAST_GOOD)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    diagnostic: dict[str, Any] = {
        "generated_at": utc_now_iso(),
        "source_url": args.url,
        "status": "error",
        "http_status": None,
        "content_type": None,
        "response_bytes": 0,
        "validation": {
            "endpoint_located": True,
            "response_received": False,
            "xml_received": False,
            "format_parsed": False,
            "current_data_checked": False,
        },
        "errors": [],
        "limitation": None,
    }

    request = urllib.request.Request(
        args.url,
        headers={
            "User-Agent": "SOLUNET-HF-Propagation-Collector/1.0 (+public GitHub Actions)",
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1",
            "Cache-Control": "no-cache",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            body = response.read()
            diagnostic["http_status"] = getattr(response, "status", 200)
            diagnostic["content_type"] = response.headers.get("Content-Type", "")
            diagnostic["response_bytes"] = len(body)
            diagnostic["validation"]["response_received"] = True

        stripped = body.lstrip()
        content_type = diagnostic["content_type"] or ""
        looks_xml = stripped.startswith(b"<?xml") or stripped.startswith(b"<solar") or (
            stripped.startswith(b"<") and "xml" in content_type.lower()
        )

        if not looks_xml:
            reason = classify_non_xml(body, content_type)
            diagnostic["limitation"] = reason
            diagnostic["errors"].append(
                f"HamQSL respondió, pero no entregó XML utilizable: {reason}"
            )
            raise ValueError(reason)

        diagnostic["validation"]["xml_received"] = True
        root = ET.fromstring(body)
        diagnostic["validation"]["format_parsed"] = True
        flat = xml_to_flat_map(root)

        current = {
            "solar_flux": parse_number(pick(flat, "solarflux", "solar_flux", "sfi")),
            "sunspots": parse_number(pick(flat, "sunspots", "sunspotnumber", "ssn")),
            "a_index": parse_number(pick(flat, "aindex", "a_index")),
            "k_index": parse_number(pick(flat, "kindex", "k_index")),
            "xray": pick(flat, "xray", "x_ray"),
            "proton_flux": pick(flat, "protonflux", "proton_flux"),
            "electron_flux": pick(flat, "electronflux", "electron_flux"),
            "geomagnetic_field": pick(flat, "geomagfield", "geomagneticfield", "geomag"),
            "signal_noise_level": pick(flat, "signalnoise", "signal_noise", "signalnoiselevel"),
            "solar_wind": parse_number(pick(flat, "solarwind", "solar_wind")),
            "magnetic_field": parse_number(pick(flat, "magneticfield", "magnetic_field")),
        }

        useful_values = sum(value is not None for value in current.values())
        diagnostic["validation"]["current_data_checked"] = useful_values >= 3

        summary = {
            "source": "HamQSL / N0NBH solar XML",
            "source_url": args.url,
            "generated_at": utc_now_iso(),
            "source_updated_at": pick(flat, "updated", "updated_at", "timestamp", "date"),
            "status": "ok" if useful_values >= 3 else "partial",
            "validation": diagnostic["validation"],
            "current": current,
            "band_conditions": parse_band_conditions(root),
            "raw_fields_present": sorted(flat.keys()),
            "notes": [
                "Referencia global; no sustituye KC2G para España o IN91PO.",
                "Frecuencia de consulta recomendada: una vez por hora.",
            ],
        }

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        args.last_good.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.output, args.last_good)
        diagnostic["status"] = summary["status"]
        diagnostic["useful_values"] = useful_values

    except urllib.error.HTTPError as exc:
        diagnostic["http_status"] = exc.code
        diagnostic["errors"].append(f"HTTPError: {exc.code} {exc.reason}")
        diagnostic["limitation"] = "http_error"
    except urllib.error.URLError as exc:
        diagnostic["errors"].append(f"URLError: {exc.reason}")
        diagnostic["limitation"] = "network_or_timeout"
    except Exception as exc:  # noqa: BLE001
        if not diagnostic["errors"]:
            diagnostic["errors"].append(f"{type(exc).__name__}: {exc}")

    if diagnostic["status"] == "error" and args.last_good.exists():
        try:
            previous = json.loads(args.last_good.read_text(encoding="utf-8"))
            previous["status"] = "stale"
            previous["generated_at"] = utc_now_iso()
            previous["stale_reason"] = diagnostic["limitation"] or "current_fetch_failed"
            previous["validation"] = diagnostic["validation"]
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            diagnostic["last_good_preserved"] = True
        except Exception as exc:  # noqa: BLE001
            diagnostic["errors"].append(f"No se pudo conservar last-good: {exc}")

    args.diagnostic.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostic.write_text(
        json.dumps(diagnostic, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # No rompemos todo el workflow por un bloqueo externo de HamQSL.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
