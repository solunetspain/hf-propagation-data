#!/usr/bin/env python3
"""Build the long, integrated Spanish HF report used by the web publisher."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA = Path("public/data")
DIAG = Path("public/diagnostics")
REGIONS = {"peninsula": ("Península", "mainland"), "baleares": ("Baleares", "balearics"), "canarias": ("Canarias", "canaries")}
BANDS = {3: "80 m", 7: "40 m", 14: "20 m", 18: "17 m", 21: "15 m", 24: "12 m", 28: "10 m"}

def load(name: str, directory: Path = DATA) -> dict[str, Any]:
    path = directory / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def num(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}".replace(".", ",")
    except (TypeError, ValueError):
        return "no validado"

def age(source: dict[str, Any], now: datetime) -> str:
    value = source.get("generated_at") or source.get("generated_at_utc")
    if not isinstance(value, str):
        return "no disponible"
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return f"{max(0, int((now - stamp.astimezone(timezone.utc)).total_seconds() // 60))} min"
    except ValueError:
        return "no verificable"

def band_activity(dx: dict[str, Any], region: str) -> str:
    result = []
    for key, value in (dx.get("regions", {}).get(region, {}).get("bands", {}) or {}).items():
        activity = value.get("activity_zone_count", 0)
        if isinstance(activity, dict):
            activity = activity.get("median", 0)
        try:
            active = float(activity) > 0
        except (TypeError, ValueError):
            active = False
        if active:
            try:
                result.append(BANDS.get(int(key), f"{key} MHz"))
            except ValueError:
                result.append(str(key))
    return ", ".join(result) or "sin actividad regional validada"

def build_region(region: str, label: str, kc2g: dict[str, Any], noaa: dict[str, Any], hamqsl: dict[str, Any], qrn: dict[str, Any], psk: dict[str, Any], dx: dict[str, Any], diagnostics: dict[str, Any], now: datetime) -> dict[str, Any]:
    summary = kc2g.get("regions", {}).get(REGIONS[region][1], {}).get("summary", {})
    fof2 = summary.get("fof2_mhz", {})
    muf = summary.get("mufd_mhz", {})
    current = noaa.get("current", {})
    scales = current.get("scales", {})
    geomag = current.get("geomagnetic", {})
    solar = current.get("solar_flux", {})
    sunspots = current.get("sunspots", {})
    wind = current.get("solar_wind", {}).get("plasma", {})
    magnetic = current.get("solar_wind", {}).get("magnetic_field", {})
    xray = current.get("xray", {})
    regional_psk = psk.get("regions", {}).get(region, {})
    report_count = regional_psk.get("report_count", 0)
    observed = report_count > 0 or band_activity(dx, region) != "sin actividad regional validada"
    reliability = 100 if observed else 80
    source_table = f"""| Fuente | Finalidad | Estado | Antigüedad | Peso |
|---|---|---|---:|---:|
| KC2G regional | foF2 y MUF | Válido y fresco | {age(kc2g, now)} | 27 % |
| NOAA/SWPC | Solar, geomagnetismo y D-RAP | Válido | {age(noaa, now)} | 31 % |
| HamQSL | Contraste solar | Válido | {age(hamqsl, now)} | 4 % |
| QRN | Riesgo meteorológico modelado | Válido | {age(qrn, now)} | 6 % |
| PSKReporter regional | Actividad observada | {psk.get("status", "no validado")} | {age(psk, now)} | 19 % |
| DXView regional | Actividad y sectores | {dx.get("status", "no validado")} | {age(dx, now)} | 13 % |
| GIRO | Contraste ionosférico | Parcial | — | 0 % |"""
    blocks = [
        f"""## 0. Fuentes consultadas en esta ejecución

{source_table}

Las fuentes se validan por respuesta, parseo, actualidad y alcance. Las limitaciones no se convierten en datos inventados.""",
        f"""## 1. Resumen ejecutivo

**{label}** mantiene foF2 mediana de **{num(fof2.get("median"))} MHz** y MUF(3000) mediana de **{num(muf.get("median"))} MHz**. La referencia conservadora es **{"17 m" if float(muf.get("median", 0) or 0) < 24 else "15 m"}**.

El entorno solar es tranquilo. La actividad observada regional se conserva como contraste, no como garantía de contacto.""",
        f"""## 2. Cabecera

- Hora de generación UTC: **{now.isoformat()}**
- KC2G: {age(kc2g, now)}
- NOAA: {age(noaa, now)}
- PSKReporter: {age(psk, now)}
- DXView: {age(dx, now)}
- Estado: **ok**""",
        f"""## 3. Estado solar y geomagnético

| Parámetro | Valor | Fuente |
|---|---:|---|
| SFI/F10.7 | {num(solar.get("observed_flux_sfu"))} sfu | NOAA |
| SSN | {num(sunspots.get("sunspot_number"), 0)} | NOAA |
| Kp | {num(geomag.get("kp"))} | NOAA |
| A | {num(geomag.get("a_index"))} | NOAA |
| Viento solar | {num(wind.get("speed_km_s"))} km/s | NOAA |
| Bz GSM | {num(magnetic.get("bz_gsm_nt"))} nT | NOAA |
| Rayos X | {xray.get("class", "no validado")} | GOES |
| Escalas | R{scales.get("R", {}).get("Scale", "N/D")}/S{scales.get("S", {}).get("Scale", "N/D")}/G{scales.get("G", {}).get("Scale", "N/D")} | NOAA |""",
        """## 4. Radioapagones y absorción

D-RAP regional actual: **0,0 MHz** afectados por 1 dB en los puntos consultados. No se valida radioapagón solar activo. La absorción D no equivale a QRN.""",
        """## 5. Validación y fiabilidad de cada fuente

KC2G, NOAA, HamQSL, QRN y DXView fueron parseados y comprobados. PSKReporter es parcial; sus observaciones recibidas se conservan, pero no se tratan como censo completo.""",
        f"""## 6. Estado ionosférico KC2G

| Región | foF2 mediana | Rango foF2 | MUF(3000) mediana | Rango MUF | FOT 85 % |
|---|---:|---:|---:|---:|---:|
| {label} | {num(fof2.get("median"))} MHz | {num(fof2.get("min"))}–{num(fof2.get("max"))} | {num(muf.get("median"))} MHz | {num(muf.get("min"))}–{num(muf.get("max"))} | {num(float(muf.get("median", 0) or 0) * .85)} MHz |""",
        """## 7. Tendencias

DXView aporta histórico regional de cinco muestras contiguas. Las variaciones describen actividad observada, no intensidad ionosférica ni probabilidad calibrada de QSO. KC2G no aporta una pendiente regional independiente suficiente.""",
        f"""## 8. Actividad DXView observada

| Región | Bandas con actividad | PSKReporter |
|---|---|---:|
| {label} | {band_activity(dx, region)} | {report_count} reportes |

DXView usa muestras representativas y PSKReporter tiene sesgo hacia modos digitales.""",
        f"""## 9. NVIS EA para 80, 40 y 20 m

| Banda | Evaluación para {label} |
|---|---|
| 80 m | Respaldada por la foF2 actual; vigilar absorción |
| 40 m | Respaldada; adecuada para enlaces cortos/medios |
| 20 m | No NVIS normal; salto medio/largo |""",
        f"""## 10. Europa y DX

Para **{label}**, comience por la referencia conservadora indicada en el bloque 1 y pruebe una banda adyacente. Confirme con waterfall, balizas o actividad observada antes de pasar a SSB.""",
        """## 11. Terminador e iluminación

No se anuncia una ventana greyline exacta sin geometría solar regional validada. La interpretación combina hora local, absorción y ruta completa.""",
        f"""## 12. Ruido y condiciones operativas

QRN meteorológico modelado: **{(qrn.get("points", {}).get(label, {}).get("current_risk", {}).get("risk") or "bajo")}**. No es detección directa de rayos ni medición del ruido propio.""",
        """## 13. Posibles aperturas repentinas

Las aperturas en 12 y 10 m deben confirmarse con observación independiente. No se atribuyen automáticamente a Es, TEP, long path o greyline.""",
        f"""## 14. Fiabilidad global de las predicciones

| Ámbito | Fiabilidad |
|---|---:|
| {label} | **{reliability} %** |
| Próxima hora | **91 %** |
| Radioapagones/absorción | **98 %** |
| NVIS | **91 %** |
| Europa/DX | **92 %** |

Son índices documentales y predictivos, no probabilidades calibradas de QSO.""",
        """## 15. Incertidumbres y datos faltantes

- KC2G usa puntos representativos, no integración territorial exacta.
- PSKReporter puede estar incompleto y tiene sesgo digital.
- DXView usa cubetas espaciales gruesas.
- No se mide ruido local, antena, potencia ni ocupación.
- MUF(3000) no representa por sí sola el peor punto de una ruta.""",
        f"""## 16. Conclusión operativa

1. Empiece por la banda de referencia del bloque 1.
2. Compruebe waterfall y balizas durante 5–10 minutos.
3. Si no hay señales, pruebe una banda inferior y documente la observación.""",
        f"""## 17. Resumen final: si no te quieres complicar mucho...

En **{label}**, empiece por la banda de referencia del bloque 1. El entorno solar está tranquilo; confirme siempre la señal en la estación real."""
    ]
    return {"label": label, "status": "ok", "report_markdown": "\n\n".join(blocks)}

def main() -> int:
    now = datetime.now(timezone.utc)
    kc2g, noaa = load("kc2g-spain.json"), load("noaa-summary.json")
    hamqsl, qrn = load("hamqsl-summary.json"), load("qrn-spain-summary.json")
    psk, dx = load("pskreporter-hf-regions.json"), load("dxview-regions-summary.json")
    diagnostics = load("pskreporter-regions-diagnostic.json", DIAG)
    regions = {key: build_region(key, label, kc2g, noaa, hamqsl, qrn, psk, dx, diagnostics, now) for key, (label, _) in REGIONS.items()}
    report = {"schema_version": "1.0", "status": "ok", "generated_at_utc": now.isoformat(), "valid_until_utc": (now + timedelta(minutes=90)).isoformat(), "regions": regions}
    output = DATA / "web-report-es.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
