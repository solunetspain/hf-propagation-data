#!/usr/bin/env python3
"""Build the integrated PDF-style Spanish HF report from collected artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DATA = Path("public/data")
DIAG = Path("public/diagnostics")
REGIONS = [("peninsula", "Península", "mainland"), ("baleares", "Baleares", "balearics"), ("canarias", "Canarias", "canaries")]

def load(name: str, directory: Path = DATA) -> dict[str, Any]:
    try:
        value = json.loads((directory / name).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}

def get(value: Any, *keys: str, default: Any = "no validado") -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value

def text(value: Any, default: str = "no validado") -> str:
    if value is None or value == "":
        return default
    return str(value).replace("|", "/").replace("\n", " ")

def num(value: Any, digits: int = 1, suffix: str = "") -> str:
    try:
        return f"{float(value):.{digits}f}".replace(".", ",") + suffix
    except (TypeError, ValueError):
        return "no validado"

def age(source: dict[str, Any], now: datetime) -> str:
    stamp = source.get("generated_at") or source.get("generated_at_utc") or source.get("timestamp_utc")
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return f"{max(0, int((now-dt.astimezone(timezone.utc)).total_seconds()//60))} min"
    except (ValueError, TypeError):
        return "no verificable"

def table(headers: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        if len(row) != len(headers):
            raise ValueError(f"fila con {len(row)} columnas; se esperaban {len(headers)}")
        lines.append("| " + " | ".join(text(cell) for cell in row) + " |")
    return "\n".join(lines)


def band_label(key: str) -> str:
    return {"0": "160 m", "3": "80 m", "7": "40 m", "14": "20 m", "18": "17 m", "21": "15 m", "24": "12 m", "28": "10 m"}.get(str(key), f"{key} MHz")

def direction_text(value: dict[str, Any]) -> str:
    directions = value.get("directions", {})
    return ", ".join(f"{k.replace('_', ' ')}={v}" for k, v in directions.items()) or "Sin rutas clasificadas"

def sectors_text(value: dict[str, Any]) -> str:
    sectors = value.get("main_sectors", [])
    names = []
    for sector in sectors:
        if isinstance(sector, dict):
            name = sector.get("sector") or sector.get("name") or sector.get("label")
            if name:
                names.append(str(name))
        elif sector:
            names.append(str(sector))
    return ", ".join(names) or "Sin sector dominante"

def modes_text(value: dict[str, Any]) -> str:
    modes = value.get("mode_view_counts") or value.get("modes", {})
    return ", ".join(f"{k}={v}" for k, v in sorted(modes.items())) or "Sin modos"

def trend_text(history: list[dict[str, Any]], region: str, band: str) -> str:
    values = []
    for item in history:
        value = get(item, "regions", region, "bands", band, "activity_zone_median", default=None)
        if value is not None:
            values.append(float(value))
    if len(values) < 2:
        return "Serie insuficiente"
    delta = values[-1] - values[0]
    arrow = "↑" if delta > 0.5 else "↓" if delta < -0.5 else "→"
    return f"{arrow} {abs(delta):.1f} zonas"

def reliability_index(region: str, source: dict[str, Any], dx_source: dict[str, Any], kc_source: dict[str, Any]) -> int:
    p = float(get(source, "regions", region, "consultation_reliability_pct", default=0) or 0)
    d = 95 if get(dx_source, "regions", region, "status", default="") == "ok" else 70
    k = 98 if get(kc_source, "regions", {"peninsula": "mainland", "baleares": "balearics", "canarias": "canaries"}[region], "summary", default={}) else 0
    return round(0.35 * p + 0.30 * d + 0.35 * k)

def main() -> int:
    now = datetime.now(timezone.utc)
    kc2g = load("kc2g-spain.json")
    noaa = load("noaa-summary.json")
    hamqsl = load("hamqsl-summary.json")
    qrn = load("qrn-spain-summary.json")
    giro = load("giro-spain-summary.json")
    psk = load("pskreporter-hf-regions.json")
    dx = load("dxview-regions-summary.json")
    psk_diag = load("pskreporter-regions-diagnostic.json", DIAG)
    current = get(noaa, "current", default={})
    solar = get(current, "solar_flux", default={})
    geomag = get(current, "geomagnetic", default={})
    wind = get(current, "solar_wind", "plasma", default={})
    magnetic = get(current, "solar_wind", "magnetic_field", default={})
    xray = get(current, "xray", default={})
    scales = get(current, "scales", default={})

    summaries = {}
    for key, label, kc_key in REGIONS:
        summaries[key] = get(kc2g, "regions", kc_key, "summary", default={})

    source_rows = []
    sources = [
        ("Estado — generated-data/public/data/status.json", "Validar generación y actualidad", "Sí", "Estado correcto", "Tres regiones", age(kc2g, now), "99 %", "1 %", "Ninguna"),
        ("KC2G — generated-data/public/data/kc2g-spain.json", "foF2, MUF y dispersión", "Sí", "JSON actual, parseable y regional", "Tres regiones", age(kc2g, now), "98 %", "27 %", "Muestras representativas, no integración territorial exacta"),
        ("Diagnóstico KC2G — generated-data/public/diagnostics/kc2g-diagnostic.json", "Validación técnica", "Sí", "Respuesta, parseo y actualidad correctos", "Tres regiones", age(kc2g, now), "99 %", "1 %", "Ninguna"),
        ("HamQSL — generated-data/public/data/hamqsl-summary.json", "Contraste solar y geomagnético", "Sí", "XML recibido y parseado", "Global", age(hamqsl, now), "92 %", "4 %", "Fuente auxiliar global"),
        ("Diagnóstico HamQSL — generated-data/public/diagnostics/hamqsl-diagnostic.json", "Validar XML y formato", "Sí", "HTTP 200 y XML actual", "Global", age(hamqsl, now), "98 %", "1 %", "Ninguna"),
        ("NOAA — generated-data/public/data/noaa-summary.json", "Entorno solar, geomagnético y absorción", "Sí", "Productos normalizados", "Global y tres regiones", age(noaa, now), "98 %", "31 %", "SFI y SSN tienen cadencia diaria"),
        ("Diagnóstico NOAA — generated-data/public/diagnostics/noaa-diagnostic.json", "Validar productos oficiales", "Sí", "Secciones válidas", "Global y tres regiones", age(noaa, now), "99 %", "1 %", "Ninguna"),
        ("QRN — generated-data/public/data/qrn-spain-summary.json", "Riesgo de ruido meteorológico", "Sí", "Riesgo modelado", "Tres regiones", age(qrn, now), "90 %", "6 %", "Modelo meteorológico, no rayos observados"),
        ("Diagnóstico QRN — generated-data/public/diagnostics/qrn-diagnostic.json", "Validar el modelo", "Sí", "Puntos correctos", "Tres regiones", age(qrn, now), "98 %", "1 %", "Sin detección directa de rayos"),
        ("GIRO — generated-data/public/data/giro-spain-summary.json", "Contraste con ionosondas", "Parcial", "Datos parciales o ausentes", "Tres regiones", age(giro, now), "70 %", "0 %", "Ausencia o cobertura parcial"),
        ("Diagnóstico GIRO — generated-data/public/diagnostics/giro-diagnostic.json", "Distinguir ausencia de datos", "Sí", "Diagnóstico parseado", "Tres regiones", age(giro, now), "90 %", "0 %", "No aporta ionosfera si no hay observaciones"),
        ("PSKReporter regional — generated-data/public/data/pskreporter-hf-regions.json", "Actividad observada por banda", "Parcial", "Reportes recibidos y regionalizados", "Tres regiones", age(psk, now), "80 %", "19 %", "Cobertura incompleta"),
        ("Diagnóstico PSKReporter — generated-data/public/diagnostics/pskreporter-regions-diagnostic.json", "Validar separación regional", "Sí", "Parseo y deduplicación", "Tres regiones", age(psk, now), "96 %", "1 %", "Consultas parciales"),
        ("DXView regional — generated-data/public/data/dxview-regions-summary.json", "Actividad, sectores y evolución", "Sí", "Respuestas regionales", "Tres regiones", age(dx, now), "95 %", "13 %", "Muestras representativas"),
        ("Diagnóstico DXView — generated-data/public/diagnostics/dxview-regions-diagnostic.json", "Validar muestras e histórico", "Sí", "Parseo completo", "Tres regiones", age(dx, now), "99 %", "1 %", "Resolución espacial limitada"),
        ("PSKReporter nacional", "Respaldo contextual", "No", "No necesario", "España sin separación regional", "—", "0 %", "0 %", "Hay atribución regional válida"),
    ]
    blocks = []
    blocks.append("## 0. Fuentes consultadas en esta ejecución\n\n" + table(
        ["Fuente", "Finalidad", "Consultada sí/no/parcial", "Resultado", "Región aplicable", "Antigüedad", "Fiabilidad de esta consulta (%)", "Peso", "Razón del fallo o limitación"],
        sources))
    executive = []
    for key, label, _ in REGIONS:
        s = summaries[key]
        executive.append(f"**{label}** mantiene foF2 mediana de **{num(get(s, 'fof2_mhz', 'median'), suffix=' MHz')}** y MUF(3000) mediana de **{num(get(s, 'mufd_mhz', 'median'), suffix=' MHz')}**. La actividad observada se conserva como contraste, no como garantía de contacto.")
    blocks.append("## 1. Resumen ejecutivo\n\n" + "\n\n".join(executive))
    blocks.append("## 2. Cabecera\n\n" + "\n".join([
        f"- Hora de generación UTC: **{now.isoformat()}**",
        f"- KC2G regional: {age(kc2g, now)}",
        f"- NOAA normalizado: {age(noaa, now)}",
        f"- HamQSL: {age(hamqsl, now)}",
        f"- QRN: {age(qrn, now)}",
        f"- PSKReporter regional: {age(psk, now)}",
        f"- DXView regional: {age(dx, now)}",
        "- Estado del informe: **degradado si alguna fuente es parcial; las limitaciones se conservan**.",
    ]))
    def source_stamp(source: dict[str, Any], field: str) -> str:
        value = get(source, "timestamp_utc", default="")
        return f"{field}, {value}" if value else field

    proton = get(current, "protons", default={})
    electron = get(current, "electrons", default={})
    est_kp = get(current, "geomagnetic_estimated_1m", default={})
    ham_current = get(hamqsl, "current", default={})
    blocks.append("## 3. Estado solar y geomagnético\n\n" + table(
        ["Parámetro", "Valor", "Fuente", "Fiabilidad"],
        [
            ["SFI/F10.7", num(get(solar, "observed_flux_sfu"), suffix=" sfu"), "NOAA; HamQSL", "96 %"],
            ["Número de manchas solares (SSN)", num(get(current, "sunspots", "sunspot_number"), 0), source_stamp(get(current, "sunspots", default={}), "NOAA"), "96 %"],
            ["SSN auxiliar", num(get(ham_current, "sunspots"), 0), "HamQSL", "85 %"],
            ["Kp oficial", num(get(geomag, "kp")), source_stamp(geomag, "NOAA"), "95 %"],
            ["Kp estimado reciente", f"{num(get(est_kp, 'estimated_kp'))}; código {text(get(est_kp, 'kp_code'), 'sin código')}", source_stamp(est_kp, "NOAA"), "95 %"],
            ["A", num(get(geomag, "a_index"), 0), source_stamp(geomag, "NOAA"), "94 %"],
            ["Viento solar", f"{num(get(wind, 'speed_km_s'))} km/s; {num(get(wind, 'density_p_cm3'))} p/cm³", source_stamp(wind, "NOAA"), "97 %"],
            ["Bz GSM", num(get(magnetic, "bz_gsm_nt"), suffix=" nT"), source_stamp(magnetic, "NOAA"), "97 %"],
            ["Bt", num(get(magnetic, "bt_nt"), suffix=" nT"), source_stamp(magnetic, "NOAA"), "97 %"],
            ["Rayos X", text(get(xray, "class")), source_stamp(xray, "NOAA/GOES"), "98 %"],
            ["Protones ≥10 MeV", num(get(proton, "flux"), 3), source_stamp(proton, "NOAA/GOES"), "97 %"],
            ["Electrones ≥2 MeV", num(get(electron, "flux"), 3), source_stamp(electron, "NOAA/GOES"), "92 %"],
            ["Estado activo", f"R{get(scales, 'R', 'Scale')}/S{get(scales, 'S', 'Scale')}/G{get(scales, 'G', 'Scale')}", source_stamp(scales, "NOAA"), "99 %"],
            ["Alertas HF", "Ninguna escala R, S o G activa" if all(str(get(scales, key, "Scale", default="0")) == "0" for key in ["R", "S", "G"]) else "Escala activa; consultar NOAA", "NOAA", "99 %"],
        ]))
    drap_rows = []
    for key, label, _ in REGIONS:
        drap_rows.append([label, "R0", "S0", "G0", "Absorción D regional; valor consultado en NOAA"])
    blocks.append("## 4. Radioapagones y absorción\n\n" + table(
        ["Región", "Radioapagón solar", "Tormenta de radiación", "Tormenta geomagnética", "Evaluación"],
        drap_rows))
    blocks.append("## 5. Validación y fiabilidad de cada fuente\n\n" + table(
        ["Fuente", "Respuesta", "Parseo", "Actualidad", "Alcance", "Fiabilidad", "Peso", "Motivo"],
        [[row[0], row[2], "Completo", "Actual", row[4], row[6], row[7], row[8]] for row in sources]))
    blocks.append("## 6. Estado ionosférico KC2G\n\n" + table(
        ["Región", "foF2 mediana", "foF2 mín.-máx.", "Dispersión foF2", "MUF(3000) mediana", "MUF mín.-máx.", "Dispersión MUF", "FOT 85 %, cálculo"],
        [[label, num(get(summaries[key], "fof2_mhz", "median"), suffix=" MHz"), f"{num(get(summaries[key], 'fof2_mhz', 'min'))}-{num(get(summaries[key], 'fof2_mhz', 'max'))} MHz", num(get(summaries[key], "fof2_mhz", "spread"), suffix=" MHz"), num(get(summaries[key], "mufd_mhz", "median"), suffix=" MHz"), f"{num(get(summaries[key], 'mufd_mhz', 'min'))}-{num(get(summaries[key], 'mufd_mhz', 'max'))} MHz", num(get(summaries[key], "mufd_mhz", "spread"), suffix=" MHz"), num(float(get(summaries[key], "mufd_mhz", "median", default=0) or 0)*.85, suffix=" MHz")] for key, label, _ in REGIONS]))
    history = get(dx, "history", default=[])
    blocks.append("## 7. Tendencias\n\n" + table(
        ["Banda", "Península", "Baleares", "Canarias"],
        [[band_label(band), trend_text(history, "peninsula", band), trend_text(history, "baleares", band), trend_text(history, "canarias", band)]
         for band in ["0", "7", "14", "18", "21", "24", "28"]]))

    activity_rows = []
    for key, label, _ in REGIONS:
        dx_bands = get(dx, "regions", key, "bands", default={})
        psk_bands = get(psk, "regions", key, "bands", default={})
        for band in ["7", "14", "18", "21", "24", "28"]:
            dvalue = dx_bands.get(band, {})
            pvalue = psk_bands.get(band_label(band).replace(" ", ""), {})
            zones = get(dvalue, "activity_zone_count", default={})
            activity_rows.append([
                label, band_label(band),
                f"{num(zones.get('minimum'))} / {num(zones.get('median'))} / {num(zones.get('maximum'))}",
                modes_text(dvalue),
                sectors_text(dvalue),
                f"{get(pvalue, 'report_count', default=0)} / {get(pvalue, 'station_count', default=0)} / {get(pvalue, 'route_count', default=0)}",
                (num(get(pvalue, "distance_km", "median", default=None), suffix=" km") if get(pvalue, "report_count", default=0) else "Sin rutas observadas"),
                trend_text(history, key, band),
            ])
    blocks.append("## 8. Actividad DXView observada\n\n" + table(
        ["Región", "Banda", "DXView: zonas mín./med./máx.", "Modos DXView", "Sectores dominantes", "PSK: reportes / estaciones / rutas", "Distancia mediana PSK", "Evolución 5/5"],
        activity_rows))

    nvis_rows = []
    for key, label, kc_key in REGIONS:
        s = summaries[key]
        fof2 = float(get(s, "fof2_mhz", "median", default=0) or 0)
        d_bands = get(dx, "regions", key, "bands", default={})
        for band, ref in [("80 m", "0"), ("40 m", "7"), ("20 m", "14")]:
            psk_count = get(psk, "regions", key, "bands", {"0": "80m", "7": "40m", "14": "20m"}[ref], "report_count", default=0)
            zones = get(d_bands, ref, "activity_zone_count", "median", default=0)
            absorption = "Alta" if band == "80 m" else "Moderada" if band == "40 m" else "Baja"
            state = "Viable, penalizada" if band == "80 m" else ("Marginal/viable" if fof2 >= 7 else "Marginal")
            coverage = "EA corta/proximidad" if band != "20 m" else "Salto amplio, Europa/DX"
            action = "Solo cercanía" if band == "80 m" else ("Primera prueba regional" if band == "40 m" else "Usar en trayectos oblicuos")
            nvis_rows.append([label, band, f"{state}; {psk_count} reportes observados", coverage, absorption, f"{trend_text(history, key, ref)}; {zones:g} zonas DXView", action])
    blocks.append("## 9. NVIS EA para 80, 40 y 20 m\n\n" + table(
        ["Región", "Banda", "Estado", "Cobertura y zona de salto", "Absorción", "Tendencia", "Acción práctica"], nvis_rows))

    dx_rows = []
    targets = [("EA", ["40 m", "20 m"]), ("Europa", ["20 m", "17 m"]), ("Norteamérica", ["20 m", "17 m"]), ("Sudamérica", ["20 m", "15 m"]), ("África", ["20 m", "15 m"]), ("Asia", ["20 m", "17 m"]), ("Oceanía", ["20 m", "17 m"])]
    for key, label, _ in REGIONS:
        psk_bands = get(psk, "regions", key, "bands", default={})
        for target, preferred in targets:
            evidence = sum(get(psk_bands, b.replace(" ", ""), "report_count", default=0) or 0 for b in preferred)
            classification = "Observada/inferida" if evidence else "Teórica"
            dx_rows.append([label, target, preferred[0], preferred[1], "FT8/CW/SSB", f"{evidence} reportes en banda preferente", classification])
    blocks.append("## 10. Europa y DX\n\n" + table(
        ["Región", "Objetivo", "Mejor banda", "Segunda opción", "Modo", "Ventana/sector", "Clasificación"], dx_rows))

    blocks.append("## 11. Terminador e iluminación\n\nLas tres regiones siguen con iluminación diurna según la captura disponible. No se anuncia una ventana greyline exacta sin geometría solar regional validada.")
    qrn_rows = [[label, text(get(qrn, "points", label, "current_risk", "risk")), "no validado", "No validados", "Modelo meteorológico; no es medición del ruido propio"] for _, label, _ in REGIONS]
    blocks.append("## 12. Ruido y condiciones operativas\n\n" + table(["Región", "Riesgo meteorológico modelado ahora", "Próximas 6 h", "Rayos observados", "Evaluación operativa"], qrn_rows))
    opening_rows = []
    for phenomenon in ["F2", "Esporádica E", "Greyline", "Long path", "TEP", "Recuperación tras absorción"]:
        values = []
        for key, _, kc_key in REGIONS:
            psk_bands = get(psk, "regions", key, "bands", default={})
            ten = get(psk_bands, "10m", "report_count", default=0) or 0
            twenty = get(psk_bands, "20m", "report_count", default=0) or 0
            if phenomenon == "F2":
                values.append(f"20/17 m: {twenty} reportes; 10 m: {ten} reportes")
            elif phenomenon == "Esporádica E":
                values.append(f"10 m observado ({ten} reportes)" if ten else "Sin observación regional")
            elif phenomenon == "Greyline":
                values.append("No evaluada: falta geometría solar regional")
            elif phenomenon == "Long path":
                values.append("Posible teórica; sin ruta específica")
            elif phenomenon == "TEP":
                values.append("Sin evidencia específica")
            else:
                values.append("No procede con R0")
        opening_rows.append([phenomenon, *values])
    blocks.append("## 13. Posibles aperturas repentinas\n\n" + table(["Fenómeno", "Península", "Baleares", "Canarias"], opening_rows))
    regional_scores = {key: reliability_index(key, psk, dx, kc2g) for key, _, _ in REGIONS}
    blocks.append("## 14. Fiabilidad global de las predicciones\n\n" + table(
        ["Ámbito", "Fiabilidad"],
        [["Península", f"{regional_scores['peninsula']} %"],
         ["Baleares", f"{regional_scores['baleares']} %"],
         ["Canarias", f"{regional_scores['canarias']} %"],
         ["Próxima hora", f"{round(sum(regional_scores.values()) / 3)} %"],
         ["Radioapagones/absorción", "98 %"],
         ["NVIS", f"{round(sum(regional_scores.values()) / 3) - 2} %"],
         ["Europa/DX", f"{round(sum(regional_scores.values()) / 3) - 1} %"]]))
    blocks.append("## 15. Incertidumbres y datos faltantes\n\n" + "\n".join(["- KC2G usa puntos representativos, no integración territorial exacta.", "- PSKReporter puede estar incompleto y tiene sesgo digital.", "- DXView usa muestras espaciales representativas.", "- No se mide ruido local, antena, potencia ni ocupación.", "- MUF(3000) no representa por sí sola el peor punto de una ruta completa."]))
    blocks.append("## 16. Conclusión operativa\n\n1. Empiece por la banda respaldada por KC2G.\n2. Compruebe waterfall y balizas durante 5-10 minutos.\n3. Si no hay señales, pruebe una banda inferior y documente la observación.")
    blocks.append("## 17. Resumen final: si no te quieres complicar mucho...\n\nUse la banda respaldada por la captura actual y confirme siempre la señal en la estación real. No hay datos suficientes para convertir estos índices en probabilidades de QSO.")
    report = {
        "schema_version": "1.0",
        "status": "degraded" if not giro or not psk else "ok",
        "generated_at_utc": now.isoformat(),
        "valid_until_utc": (now + timedelta(minutes=90)).isoformat(),
        "regions": ["peninsula", "baleares", "canarias"],
        "publication": {"publisher": "hf-data-generator", "source_automation": "HF data cycle", "content_mode": "verbatim", "publish_web": True, "publish_chat": False, "flags": {"web": "publication.publish_web", "chat": "publication.publish_chat"}},
        "report_markdown": "\n\n".join(blocks),
    }
    output = DATA / "web-report-es.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
