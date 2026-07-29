#!/usr/bin/env python3
"""Optional OCR bridge for ionogram screenshots.

OCR is deliberately diagnostic-only. It never supplies prediction values by
itself: every extracted value is marked as OCR and pending validation.
Configure station image URLs with IONOGRAM_OCR_URL_<STATION>, and install
Tesseract on the runner if OCR is desired. Missing configuration or OCR
engine is a recorded limitation, not a fabricated measurement.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

STATIONS = {"EB040": "Roquetes", "EA036": "El Arenosillo"}
FIELDS = ("foF2", "hmF2", "foEs", "fmin")
PATTERNS = {
    "foF2": r"\bfo\s*F?2\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
    "hmF2": r"\bhm\s*F?2\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
    "foEs": r"\bfo\s*Es\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
    "fmin": r"\bfmin\s*[:=]?\s*(\d+(?:[.,]\d+)?)",
}

def now():
    return datetime.now(timezone.utc).isoformat()

def ocr_station(code: str):
    url = os.getenv("IONOGRAM_OCR_URL_" + code)
    if not url:
        return {"status": "not_configured", "station": STATIONS[code],
                "reason": "No se ha configurado una URL de imagen de ionograma."}
    if not shutil.which("tesseract"):
        return {"status": "unavailable", "station": STATIONS[code],
                "url": url, "reason": "El ejecutable Tesseract no está instalado en el runner."}
    with tempfile.TemporaryDirectory() as directory:
        image = Path(directory) / (code + ".png")
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "SOLUNET-HF-ionogram-ocr/1.0"})
            with urllib.request.urlopen(request, timeout=30) as response:
                image.write_bytes(response.read())
            proc = subprocess.run(
                ["tesseract", str(image), "stdout", "--psm", "6"],
                capture_output=True, text=True, timeout=45, check=False,
            )
            text = proc.stdout or ""
            measurements = {}
            for field, pattern in PATTERNS.items():
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if match:
                    measurements[field] = float(match.group(1).replace(",", "."))
            if not measurements:
                return {"status": "no_parseable_values", "station": STATIONS[code],
                        "url": url, "ocr_text_length": len(text),
                        "reason": "La imagen se descargó, pero no contiene etiquetas legibles de forma fiable."}
            return {"status": "ocr_pending_validation", "station": STATIONS[code],
                    "url": url, "measurements": measurements,
                    "classification": "OCR; no medido validado; no entra en la puntuación"}
        except Exception as error:
            return {"status": "error", "station": STATIONS[code], "url": url,
                    "reason": f"{type(error).__name__}: {error}"}

def main():
    output = Path("public/data/ionogram-ocr-summary.json")
    diagnostic = Path("public/diagnostics/ionogram-ocr-diagnostic.json")
    stations = {code: ocr_station(code) for code in STATIONS}
    configured = sum(item.get("status") == "ocr_pending_validation" for item in stations.values())
    result = {"generated_at": now(), "status": "diagnostic_only",
              "parameters": list(FIELDS), "stations": stations,
              "validated_measurements": False,
              "prediction_weight": 0,
              "note": "El OCR requiere validación de fuente, fecha, estación y coherencia antes de influir en la predicción."}
    diag = {"generated_at": result["generated_at"], "status": result["status"],
            "configured_stations": configured, "stations": stations,
            "interpretation": "Los valores OCR se conservan como candidatos pendientes; no se presentan como medición ionosférica validada."}
    output.parent.mkdir(parents=True, exist_ok=True)
    diagnostic.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    diagnostic.write_text(json.dumps(diag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
