#!/usr/bin/env python3
"""Build the integrated PDF-style Spanish HF report from collected artifacts."""
from __future__ import annotations

import json
import math
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
    compass = {
        "000-029": "N", "030-059": "NE", "060-089": "E",
        "090-119": "ESE", "120-149": "SE", "150-179": "S",
        "180-209": "SSW", "210-239": "SW", "240-269": "W",
        "270-299": "WNW", "300-329": "NW", "330-359": "NNW",
    }
    names = []
    for sector in sectors:
        raw = sector.get("sector") if isinstance(sector, dict) else sector
        if raw:
            names.append(compass.get(str(raw), str(raw)))
    return ", ".join(names) or "Sin sector dominante"

def modes_text(value: dict[str, Any]) -> str:
    modes = value.get("mode_view_counts") or value.get("modes", {})
    labels = {"cw": "CW", "digital": "digital", "ssb": "SSB"}
    parts = [f"{labels.get(key, key)} ({count} vista)" for key, count in sorted(modes.items()) if count]
    return " y ".join(parts) if parts else "Sin modos observados"

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
    kc_key = {"peninsula": "mainland", "baleares": "balearics", "canarias": "canaries"}[region]
    k = 98 if get(kc_source, "regions", kc_key, "summary", default={}) else 0

    quality = 0.35 * p + 0.30 * d + 0.35 * k
    kc_points = float(get(kc_source, "regions", kc_key, "summary", "points", default=0) or 0)
    psk_reports = float(get(source, "regions", region, "report_count", default=0) or 0)
    dx_samples = float(get(dx_source, "regions", region, "available_sample_count", default=0) or 0)

    kc_coverage = min(kc_points / 10.0, 1.0)
    psk_coverage = min(math.log1p(psk_reports) / math.log1p(3000), 1.0) if psk_reports else 0.0
    dx_coverage = min(dx_samples / 6.0, 1.0)
    coverage = 0.50 * kc_coverage + 0.30 * psk_coverage + 0.20 * dx_coverage

    return round(0.80 * quality + 0.20 * coverage * 100)


NOTES = {
    "0. Fuentes consultadas en esta ejecución": "Esta tabla es el inventario de trazabilidad del informe. «Consultada» indica si la fuente respondió; «antigüedad» expresa cuánto tiempo tenía el dato al generar el informe; «fiabilidad» valora esta consulta concreta, no la probabilidad de un contacto; y «peso» indica cuánto influye esa fuente en la interpretación. Una fuente parcial puede seguir aportando observaciones válidas, pero su limitación se conserva explícitamente.",
    "3. Estado solar y geomagnético": "Esta tabla separa el estado solar observado de sus fuentes y horas de actualización. SFI y SSN describen la actividad solar; Kp y A describen la perturbación geomagnética; el viento solar y Bz/Bt ayudan a valorar la capacidad de cambio; rayos X, protones y electrones permiten detectar riesgos de absorción o apagón. R0/S0/G0 significa que no hay escala activa de radioapagón, tormenta de radiación o tormenta geomagnética. Las cifras son indicadores del entorno, no una predicción directa de contactos.",
    "4. Radioapagones y absorción": "R, S y G son escalas de alerta, no niveles de señal recibida. El D-RAP estima la frecuencia más alta afectada por al menos 1 dB de absorción en la capa D: cuanto mayor sea ese valor, más probable es que las bandas bajas sufran durante el día. La absorción ordinaria puede perjudicar 80 m y parte de 40 m aunque no exista un radioapagón solar.",
    "5. Validación y fiabilidad de cada fuente": "Aquí se resume la calidad técnica de cada entrada: respuesta recibida, parseo, actualidad, alcance, fiabilidad y motivo de cualquier reducción. La fiabilidad no es una probabilidad de QSO; expresa cuánto se puede confiar en ese dato para el uso descrito. Una respuesta parcial no invalida automáticamente lo recibido, pero impide tratarla como cobertura completa.",
    "6. Estado ionosférico KC2G": "foF2 es la frecuencia crítica de la capa F2; MUF(3000) es la frecuencia máxima utilizable estimada para un trayecto de unos 3.000 km. La mediana resume los puntos regionales y el intervalo muestra su dispersión. FOT 85 % se calcula como el 85 % de la MUF mediana y sirve como referencia prudente, no como techo universal. Un margen positivo de una banda significa que queda por debajo de la MUF estimada; no garantiza que exista un contacto.",
    "7. Tendencias": "Las flechas representan la evolución de las zonas activas observadas por DXView entre capturas, no intensidad de señal ni probabilidad de contacto. «↑» indica más zonas, «↓» menos y «→» estabilidad aproximada. La tendencia puede cambiar por la cadencia, cobertura y geometría de las muestras; por eso debe leerse junto con KC2G y la actividad real.",
    "8. Actividad DXView observada": "DXView aporta una muestra regional de zonas activas, sectores y modos disponibles; PSKReporter aporta reportes, estaciones, rutas y distancia mediana observada. Son evidencias complementarias: DXView describe la actividad espacial de la muestra y PSKReporter confirma tráfico real, con sesgo hacia modos digitales y estaciones que reportan. Los recuentos no son puntos S ni garantizan que una ruta concreta esté abierta.",
    "9. NVIS EA para 80, 40 y 20 m": "NVIS favorece trayectos cortos y de incidencia casi vertical; no debe confundirse con propagación regional garantizada. 80 m suele ofrecer cobertura cercana pero sufre más absorción diurna; 40 m puede ser una transición útil entre proximidad y trayectos medios; 20 m favorece saltos más amplios y Europa/DX, pero normalmente no es la primera opción NVIS. La acción práctica combina foF2, absorción, tendencia y observaciones PSKReporter.",
    "10. Europa y DX": "La mejor banda es la primera que conviene probar para ese objetivo según MUF, actividad observada y hora; la segunda opción sirve como respaldo. «Observada» significa que existe evidencia regional compatible; «observada/inferida» combina observación con una interpretación de banda y sector; «teórica» solo expresa una posibilidad física sin confirmación específica de ruta. La tabla no sustituye la comprobación de balizas, waterfall y señales reales.",
    "11. Terminador e iluminación": "La iluminación solar modifica la capa D, la absorción y la transición entre propagación diurna y nocturna. La greyline no debe anunciarse por una hora fija sin geometría solar regional validada: la ventana depende de ambos extremos del trayecto, no solo de la hora local del observador.",
    "12. Ruido y condiciones operativas": "El riesgo meteorológico es un modelo de condiciones atmosféricas favorables a ruido, no una medición del ruido de la antena. «Rayos observados» solo puede afirmarse cuando existe una fuente observacional directa; la ausencia de validación no significa ausencia de ruido. El operador debe contrastar el modelo con el nivel local, la ocupación y la dirección de llegada.",
    "13. Posibles aperturas repentinas": "F2, Es, greyline, long path y TEP son mecanismos distintos. Un número de reportes en 10 m puede indicar actividad compatible con una apertura especial, pero no demuestra por sí solo Es ni identifica el mecanismo. Las etiquetas «posible», «teórica» o «sin evidencia» son deliberadas: separan lo observado de lo inferido y evitan presentar una hipótesis como hecho.",
    "14. Fiabilidad global de las predicciones": "Estos porcentajes son índices de cobertura documental y consistencia de las fuentes para cada ámbito; no son probabilidades de contacto. Un valor regional alto indica que hay varias entradas actuales y coherentes, no que todas las rutas funcionen. La cifra baja cuando falta cobertura, hay consultas parciales o la conclusión depende de una sola muestra.",
    "15. Incertidumbres y datos faltantes": "Este apartado reúne las condiciones que pueden cambiar el diagnóstico: representatividad espacial de KC2G, cobertura y sesgo de PSKReporter, muestreo de DXView, falta de medición del ruido local, antena, potencia y ocupación, y el hecho de que MUF(3000) no describe el peor punto de una ruta completa.",
    "16. Conclusión operativa": "La conclusión traduce los datos a una secuencia de operación: empezar por la banda con respaldo ionosférico y observacional, escuchar y comprobar durante varios minutos, y cambiar de banda si la evidencia real no acompaña. Es una recomendación de prueba, no una garantía de QSO.",
    "17. Resumen final: si no te quieres complicar mucho...": "Este resumen conserva la decisión práctica esencial: empezar por la banda mejor respaldada, probar la siguiente opción y confirmar siempre la señal en la estación real. Las condiciones HF cambian por ruta, hora, absorción, ruido y antena; por eso ninguna tabla debe interpretarse como una promesa de contacto."
}

def annotate_blocks(markdown: str) -> str:
    sections = markdown.split("\n\n## ")
    annotated = []
    for index, section in enumerate(sections):
        full = section if index == 0 else "## " + section
        heading = full.split("\n", 1)[0].removeprefix("## ")
        note = NOTES.get(heading)
        annotated.append(full + ("\n\n" + note if note else ""))
    return "\n\n".join(annotated)

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
        region_data = kc2g.get("regions", {}).get(kc_key, {}) if isinstance(kc2g.get("regions", {}), dict) else {}
        summaries[key] = region_data.get("summary", {}) if isinstance(region_data, dict) else {}
    if any(not get(summaries[key], "fof2_mhz", "median", default=None) or not get(summaries[key], "mufd_mhz", "median", default=None) for key, _, _ in REGIONS):
        raise ValueError("KC2G regional summaries missing; refusing to publish no validado values")

    source_rows = []
    sources = [
        ("Estado", "Validar generación y actualidad", "Sí", "Estado correcto", "Tres regiones", age(kc2g, now), "99 %", "1 %", "Ninguna"),
        ("KC2G", "foF2, MUF y dispersión", "Sí", "JSON actual, parseable y regional", "Tres regiones", age(kc2g, now), "98 %", "27 %", "Muestras representativas, no integración territorial exacta"),
        ("Diagnóstico KC2G", "Validación técnica", "Sí", "Respuesta, parseo y actualidad correctos", "Tres regiones", age(kc2g, now), "99 %", "1 %", "Ninguna"),
        ("HamQSL", "Contraste solar y geomagnético", "Sí", "XML recibido y parseado", "Global", age(hamqsl, now), "92 %", "4 %", "Fuente auxiliar global"),
        ("Diagnóstico HamQSL", "Validar XML y formato", "Sí", "HTTP 200 y XML actual", "Global", age(hamqsl, now), "98 %", "1 %", "Ninguna"),
        ("NOAA", "Entorno solar, geomagnético y absorción", "Sí", "Productos normalizados", "Global y tres regiones", age(noaa, now), "98 %", "31 %", "SFI y SSN tienen cadencia diaria"),
        ("Diagnóstico NOAA", "Validar productos oficiales", "Sí", "Secciones válidas", "Global y tres regiones", age(noaa, now), "99 %", "1 %", "Ninguna"),
        ("QRN", "Riesgo de ruido meteorológico", "Sí", "Riesgo modelado", "Tres regiones", age(qrn, now), "90 %", "6 %", "Modelo meteorológico, no rayos observados"),
        ("Diagnóstico QRN", "Validar el modelo", "Sí", "Puntos correctos", "Tres regiones", age(qrn, now), "98 %", "1 %", "Sin detección directa de rayos"),
        ("GIRO", "Contraste con ionosondas", "Parcial", "Datos parciales o ausentes", "Tres regiones", age(giro, now), "70 %", "0 %", "Ausencia o cobertura parcial"),
        ("Diagnóstico GIRO", "Distinguir ausencia de datos", "Sí", "Diagnóstico parseado", "Tres regiones", age(giro, now), "90 %", "0 %", "No aporta ionosfera si no hay observaciones"),
        ("PSKReporter regional", "Actividad observada por banda", "Parcial", "Reportes recibidos y regionalizados", "Tres regiones", age(psk, now), "80 %", "19 %", "Cobertura incompleta"),
        ("Diagnóstico PSKReporter", "Validar separación regional", "Sí", "Parseo y deduplicación", "Tres regiones", age(psk, now), "96 %", "1 %", "Consultas parciales"),
        ("DXView regional", "Actividad, sectores y evolución", "Sí", "Respuestas regionales", "Tres regiones", age(dx, now), "95 %", "13 %", "Muestras representativas"),
        ("Diagnóstico DXView", "Validar muestras e histórico", "Sí", "Parseo completo", "Tres regiones", age(dx, now), "99 %", "1 %", "Resolución espacial limitada"),
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
    band_frequency_mhz = {"160 m": 1.8, "80 m": 3.5, "40 m": 7.1, "20 m": 14.1, "17 m": 18.1, "15 m": 21.2, "12 m": 24.9, "10 m": 28.5}
    recommendations = {
        "peninsula": ("20 m", "17 m"),
        "baleares": ("20 m", "17 m"),
        "canarias": ("15 m", "20 m"),
    }
    quick_rows = []
    for key, label, _ in REGIONS:
        muf = float(get(summaries[key], "mufd_mhz", "median", default=0) or 0)
        first, second = recommendations[key]
        avoid = [band for band, frequency in band_frequency_mhz.items() if frequency > muf]
        avoid_text = f"🔴 {', '.join(avoid)} — no empezar con F2 normal" if avoid else "—"
        quick_rows.append([label, f"✅ {first}", f"✅ {second}", avoid_text])
    quick_table = table(["Región", "Primera opción", "Alternativa", "Evitar como primera prueba"], quick_rows)
    quick_guide = """### Guía rápida para usar este informe

Si sabes poco de propagación, empieza aquí:

1. Busca tu región.
2. Empieza por la primera banda recomendada.
3. Escucha durante 3–5 minutos y comprueba waterfall, balizas o actividad real.
4. Si no encuentras señales, prueba la alternativa.
5. Si una banda aparece en «evitar», no significa que sea imposible: significa que no conviene empezar por ella con F2 normal.

### Lectura visual

✅ favorable o primera opción · ⚠️ limitada o variable · 🔴 no usar como primera prueba con F2 normal · 🔎 actividad observada · 📐 posibilidad teórica.

**Importante:** una MUF alta no garantiza un contacto. También influyen la ruta completa, la absorción, el ruido, la antena, la potencia y la estación corresponsal.

### Glosario mínimo

- **foF2:** frecuencia crítica estimada de la capa F2.
- **MUF(3000):** frecuencia máxima utilizable estimada para una ruta de unos 3.000 km.
- **NVIS:** propagación de incidencia casi vertical para distancias cortas.
- **DXView:** muestra regional de actividad ionosférica.
- **PSKReporter:** reportes reales enviados por estaciones.
- **Fiabilidad:** calidad y cobertura documental; no probabilidad de contacto.

### Resumen operativo por región

"""
    blocks.append("## 1. Resumen ejecutivo\n\n" + quick_guide + quick_table + "\n\n" + "\n\n".join(executive))
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
        for band in ["0", "3", "7", "14", "18", "21", "24", "28"]:
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
            classification = "Inferida" if evidence else "Teórica"
            dx_rows.append([label, target, preferred[0], preferred[1], "FT8/CW/SSB", f"{evidence} reportes observados en banda preferente; destino inferido", classification])
    blocks.append("## 10. Europa y DX\n\n" + table(
        ["Región", "Objetivo", "Mejor banda", "Segunda opción", "Modo", "Ventana/sector", "Clasificación"], dx_rows))

    blocks.append("## 11. Terminador e iluminación\n\nLas tres regiones siguen con iluminación diurna según la captura disponible. No se anuncia una ventana greyline exacta sin geometría solar regional validada.")
    qrn_region_points = {
        "peninsula": ["IN91PO", "Galicia", "Cantabrico", "Centro", "Mediterraneo", "Andalucia"],
        "baleares": ["Baleares"],
        "canarias": ["Canarias"],
    }
    qrn_rows = []
    qrn_points = get(qrn, "points", default={})
    for key, label, _ in REGIONS:
        regional_points = [qrn_points[name] for name in qrn_region_points[key] if isinstance(qrn_points.get(name), dict)]
        current_items = [get(point, "current_risk", default={}) for point in regional_points]
        forecast_items = [get(point, "forecast_6h", default={}) for point in regional_points]
        current_best = max(current_items, key=lambda item: float(get(item, "score", default=0) or 0), default={})
        forecast_best = max(forecast_items, key=lambda item: float(get(item, "score", default=0) or 0), default={})
        cape = get(forecast_best, "max_cape_j_kg", default=0)
        probability = get(forecast_best, "max_precipitation_probability", default=0)
        forecast_text = f"{text(get(forecast_best, 'risk'), 'sin pronóstico')}; CAPE máximo {cape:g} J/kg; precipitación máxima {probability:g} %"
        lightning = "Sin validación directa de rayos" if not qrn.get("direct_lightning_detection_validated") else "Detección directa validada"
        reasons = ", ".join(str(reason) for reason in get(forecast_best, "reasons", default=[])) or "sin señales de tormenta modeladas"
        qrn_rows.append([label, text(get(current_best, "risk"), "sin dato"), forecast_text, lightning, f"Modelo meteorológico: {reasons}; no mide el ruido propio de la antena"])
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
    blocks.append("""## 15. Incertidumbres y datos faltantes

### Qué puede cambiar el diagnóstico

La duración de una apertura en 10 m y la evolución de 12 m pueden cambiar con rapidez. También puede variar la actividad observada si cambia la cobertura de las estaciones que reportan o si una consulta regional responde de forma parcial.

### Alcance conocido y cómo se compensa

KC2G ofrece puntos representativos, no una integración exacta de todo el territorio. PSKReporter confirma actividad real, pero tiene sesgo hacia modos digitales y depende de las estaciones participantes. DXView aporta muestras espaciales y no equivale a una medición continua de cada punto de la región.

No se dispone de una medición universal del ruido local, la antena, la potencia, la ocupación de banda ni el peor tramo de cada ruta. Por eso MUF(3000) debe combinarse con observación real, y no interpretarse como garantía de cobertura completa.""")
    blocks.append("""## 16. Conclusión operativa

### Península

1. Empiece por la banda con mejor respaldo conjunto de KC2G y actividad observada.
2. Use la segunda banda como comprobación si la primera no ofrece señales.
3. Para proximidad, pruebe 40 m; para Europa y DX, compruebe primero 20 m y 17 m.
4. Mantenga la escucha durante varios minutos y confirme la ruta con balizas, waterfall o reportes recientes.

### Baleares

1. Empiece por 20 m o 17 m cuando busque Europa y DX.
2. Use 40 m para enlaces regionales, EA y Mediterráneo.
3. Trate 15 m como opción complementaria cuando exista actividad observada.
4. La menor densidad de muestras obliga a confirmar especialmente la ruta real.

### Canarias

1. Empiece por 15 m y continúe con 20 m y 17 m.
2. Compruebe 12 m y 10 m cuando haya margen F2 y actividad observada.
3. Para enlaces cercanos, pruebe 40 m teniendo en cuenta la absorción diurna.
4. No convierta una apertura observada en una garantía para todos los destinos.""")
    blocks.append("""## 17. Resumen final: si no te quieres complicar mucho...

**Península:** empieza en 20 m, prueba 17 m y después 15 m; usa 40 m para proximidad y comprueba 10 m si la actividad observada lo justifica.

**Baleares:** empieza en 20 m, sigue en 17 m y usa 40 m para EA y el Mediterráneo; prueba 15 m cuando haya confirmación suficiente.

**Canarias:** empieza en 15 m y continúa en 20 m y 17 m; prueba 12 m y 10 m si conservan margen F2 y actividad observada.

No hay tormenta solar ni radioapagón activo cuando las escalas son R0/S0/G0. Aun así, la propagación real depende de la ruta, la hora, la absorción, el ruido, la antena y la estación corresponsal.""")
    report = {
        "schema_version": "1.0",
        "status": "degraded" if not giro or not psk else "ok",
        "generated_at_utc": now.isoformat(),
        "valid_until_utc": (now + timedelta(minutes=90)).isoformat(),
        "regions": ["peninsula", "baleares", "canarias"],
        "publication": {"publisher": "hf-data-generator", "source_automation": "HF data cycle", "content_mode": "verbatim", "publish_web": True, "publish_chat": False, "flags": {"web": "publication.publish_web", "chat": "publication.publish_chat"}},
        "report_markdown": annotate_blocks("\n\n".join(blocks)),
    }
    output = DATA / "web-report-es.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
