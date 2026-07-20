#!/usr/bin/env python3
"""Build the canonical Spanish HF report consumed by hf-propagation-web.

The generator is deliberately conservative: missing, stale or spatially
incompatible sources are described, never replaced with guessed values.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DATA = Path("public/data")
DEFAULT_OUTPUT = DATA / "web-report-es.json"

BLOCK_TITLES = (
    "Fuentes consultadas",
    "Resumen ejecutivo",
    "Cabecera",
    "Estado solar y geomagnético",
    "Radioapagones y absorción",
    "Validación por fuente",
    "Estado ionosférico KC2G",
    "Tendencias",
    "Actividad observada",
    "NVIS EA 80/40/20 m",
    "Europa y DX",
    "Terminador",
    "Ruido",
    "Aperturas repentinas",
    "Fiabilidad global de las predicciones",
    "Incertidumbres",
    "Conclusión operativa",
    "Resumen final: si no te quieres complicar mucho...",
)

REGIONS = {
    "peninsula": {"label": "Península", "kc2g": "mainland"},
    "baleares": {"label": "Baleares", "kc2g": "balearics"},
    "canarias": {"label": "Canarias", "kc2g": "canaries"},
}

BANDS = (
    ("10 m", 28.3),
    ("12 m", 24.9),
    ("15 m", 21.2),
    ("17 m", 18.1),
    ("20 m", 14.1),
    ("40 m", 7.1),
    ("80 m", 3.6),
)

SOURCE_FILES = {
    "kc2g_spain": "kc2g-spain.json",
    "kc2g_local": "kc2g-in91po.json",
    "noaa": "noaa-summary.json",
    "dxview": "dxview-in91po-summary.json",
    "hamqsl": "hamqsl-summary.json",
    "qrn": "qrn-spain-summary.json",
    "giro": "giro-spain-summary.json",
    "psk": "pskreporter-hf-summary.json",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def load_optional(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def nested(mapping: dict[str, Any] | None, *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def fmt(value: Any, digits: int = 1, suffix: str = "") -> str:
    number = finite(value)
    return "no validado" if number is None else f"{number:.{digits}f}{suffix}"


def status_ok(source: dict[str, Any] | None) -> bool:
    return isinstance(source, dict) and source.get("status") in {"ok", "partial"}


def kc2g_usable(source: dict[str, Any] | None, now: datetime) -> bool:
    if not isinstance(source, dict):
        return False
    declared = source.get("freshness")
    if declared in {"stale", "unusable"}:
        return False
    if declared in {"fresh", "degraded"}:
        return True
    observed = parse_time(source.get("timestamp_utc") or source.get("generated_at"))
    return observed is not None and 0 <= (now - observed).total_seconds() <= 90 * 60


def source_age(source: dict[str, Any] | None, now: datetime) -> str:
    if not source:
        return "no disponible"
    stamp = (
        source.get("timestamp_utc")
        or source.get("source_generated_at")
        or source.get("generated_at")
    )
    parsed = parse_time(stamp)
    if parsed is None:
        return "edad no verificable"
    minutes = max(0, int((now - parsed).total_seconds() // 60))
    return f"{minutes} min"


def region_summary(kc2g: dict[str, Any] | None, key: str) -> dict[str, Any] | None:
    value = nested(kc2g, "regions", key, "summary")
    return value if isinstance(value, dict) else None


def supported_band(summary: dict[str, Any] | None) -> str | None:
    """Choose a conservative operational band from median FOT/foF2."""
    muf = finite(nested(summary, "mufd_mhz", "median"))
    fof2 = finite(nested(summary, "fof2_mhz", "median"))
    if muf is not None:
        fot = 0.85 * muf
        for label, frequency in BANDS[:-2]:
            if frequency <= fot:
                return label
        if 7.1 <= fot:
            return "40 m"
    if fof2 is not None and fof2 >= 3.5:
        return "80 m"
    return None


def qrn_points(region: str) -> tuple[str, ...]:
    if region == "peninsula":
        return ("Galicia", "Cantabrico", "Centro", "Mediterraneo", "Andalucia")
    return ("Baleares",) if region == "baleares" else ("Canarias",)


def qrn_assessment(qrn: dict[str, Any] | None, region: str) -> tuple[str, bool]:
    order = {"bajo": 0, "medio": 1, "alto": 2}
    values = []
    for name in qrn_points(region):
        risk = nested(qrn, "points", name, "current_risk", "risk")
        if risk in order:
            values.append(str(risk))
    if not values:
        return "no validado", False
    worst = max(values, key=lambda item: order[item])
    return worst, True


def drap_assessment(noaa: dict[str, Any] | None, region: str) -> tuple[float | None, bool]:
    names = qrn_points(region)
    values = [
        finite(nested(noaa, "drap", "points", name, "highest_frequency_affected_1db_mhz"))
        for name in names
    ]
    usable = [value for value in values if value is not None]
    return (max(usable), True) if usable else (None, False)


def observed_bands(source: dict[str, Any] | None, kind: str) -> list[str]:
    if not isinstance(source, dict):
        return []
    bands = source.get("bands")
    if not isinstance(bands, dict):
        return []
    result = []
    for key, value in bands.items():
        if not isinstance(value, dict):
            continue
        if kind == "dxview":
            count = finite(value.get("activity_zone_count")) or 0
            mhz = finite(value.get("band_mhz")) or finite(key)
            if count > 0 and mhz is not None:
                result.append(f"{mhz:g} MHz")
        elif kind == "psk" and (finite(value.get("report_count")) or 0) > 0:
            result.append(str(key))
    return result


def subregional_section(kc2g: dict[str, Any] | None) -> tuple[str, list[str]]:
    subregions = nested(kc2g, "regions", "mainland", "subregions")
    if not isinstance(subregions, dict):
        return "No hay desglose subregional validado.", []
    rows = []
    bands: list[str] = []
    for value in subregions.values():
        if not isinstance(value, dict):
            continue
        summary = value.get("summary") if isinstance(value.get("summary"), dict) else None
        band = supported_band(summary) or "sin banda respaldada"
        bands.append(band)
        rows.append(
            "| {label} | {fof2} | {muf} | {band} |".format(
                label=value.get("label", "Macrozona"),
                fof2=fmt(nested(summary, "fof2_mhz", "median"), suffix=" MHz"),
                muf=fmt(nested(summary, "mufd_mhz", "median"), suffix=" MHz"),
                band=band,
            )
        )
    if not rows:
        return "No hay desglose subregional validado.", []
    table = "\n".join(
        [
            "| Macrozona | foF2 mediana | MUF(3000) mediana | Referencia conservadora |",
            "|---|---:|---:|---|",
            *rows,
        ]
    )
    distinct = sorted(set(bands))
    if len(distinct) > 1:
        table += (
            "\n\n**Aviso subregional:** las macrozonas no conducen a la misma banda "
            "de referencia; conviene aplicar la fila correspondiente a la estación."
        )
    return table, distinct


def reliability(
    region: str,
    summary: dict[str, Any] | None,
    noaa: dict[str, Any] | None,
    qrn_valid: bool,
    observed: bool,
) -> int:
    score = 0
    if summary is not None:
        score += 45
    if status_ok(noaa):
        score += 25
    if qrn_valid:
        score += 10
    if region == "peninsula" and observed:
        score += 20
    return score


def sources_table(
    region: str, sources: dict[str, dict[str, Any] | None], now: datetime
) -> str:
    regional_observation = True
    rows = [
        (
            "KC2G",
            "regional",
            source_age(sources["kc2g_spain"], now),
            "normal" if kc2g_usable(sources["kc2g_spain"], now) else "0",
            "Muestreo de puntos; no es un circuito completo.",
        ),
        (
            "NOAA/SWPC",
            "global y D-RAP regional",
            source_age(sources["noaa"], now),
            "normal" if status_ok(sources["noaa"]) else "0",
            "Entorno espacial; D-RAP usa rejilla.",
        ),
        (
            "Open-Meteo QRN",
            "regional modelado",
            source_age(sources["qrn"], now),
            "limitado" if status_ok(sources["qrn"]) else "0",
            "No es detección directa de rayos.",
        ),
        (
            "DXView",
            "IN91/IN91PO" if regional_observation else "IN91/IN91PO, no regional",
            source_age(sources["dxview"], now),
            "normal" if regional_observation and observed_bands(sources["dxview"], "dxview") else "0",
            "Actividad regional, no valor puntual.",
        ),
        (
            "PSKReporter",
            "IN91/IN91PO" if regional_observation else "IN91/IN91PO, no regional",
            source_age(sources["psk"], now),
            "normal" if regional_observation and observed_bands(sources["psk"], "psk") else "0",
            "Sesgo hacia modos digitales; cero informes no prueba cierre.",
        ),
        (
            "HamQSL",
            "global",
            source_age(sources["hamqsl"], now),
            "contraste" if status_ok(sources["hamqsl"]) else "0",
            "No sustituye la evaluación regional.",
        ),
        (
            "GIRO",
            "ionosondas españolas",
            source_age(sources["giro"], now),
            "contraste" if status_ok(sources["giro"]) else "0",
            "No existe ionosonda en cada punto de muestreo.",
        ),
    ]
    body = ["| Fuente | Alcance | Edad | Peso | Limitación |", "|---|---|---:|---|---|"]
    body.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(body)


def build_region(
    region: str,
    sources: dict[str, dict[str, Any] | None],
    now: datetime,
) -> dict[str, Any]:
    definition = REGIONS[region]
    summary = (
        region_summary(sources["kc2g_spain"], definition["kc2g"])
        if kc2g_usable(sources["kc2g_spain"], now)
        else None
    )
    fof2 = nested(summary, "fof2_mhz", "median")
    muf = nested(summary, "mufd_mhz", "median")
    fof2_text = fmt(fof2, suffix=" MHz")
    muf_text = fmt(muf, suffix=" MHz")
    fot = finite(muf)
    fot_text = fmt(0.85 * fot if fot is not None else None, suffix=" MHz")
    primary = supported_band(summary)
    qrn, qrn_valid = qrn_assessment(sources["qrn"], region)
    drap, drap_valid = drap_assessment(sources["noaa"], region)
    dx_bands = observed_bands(sources["dxview"], "dxview")
    psk_bands = observed_bands(sources["psk"], "psk")
    observed = bool(dx_bands or psk_bands)
    confidence = reliability(region, summary, sources["noaa"], qrn_valid, observed)
    confidence_label = "alta" if confidence >= 75 else "media" if confidence >= 50 else "baja"
    current_scales = nested(sources["noaa"], "current", "scales") or {}
    geomagnetic = nested(sources["noaa"], "current", "geomagnetic") or {}
    xray = nested(sources["noaa"], "current", "xray") or {}
    solar_flux = nested(sources["noaa"], "current", "solar_flux") or {}
    sunspots = nested(sources["noaa"], "current", "sunspots") or {}
    local_trend = nested(sources["kc2g_local"], "trend") or {}
    subregions_text, subregion_bands = subregional_section(
        sources["kc2g_spain"] if summary is not None else None
    )

    main_text = primary or "ninguna banda respaldada con los datos disponibles"
    observation_text = (
        "DXView: " + (", ".join(dx_bands) if dx_bands else "sin actividad útil")
        + "; PSKReporter: " + (", ".join(psk_bands) if psk_bands else "sin informes útiles")
        + ". La atribución regional se conserva; las cubetas gruesas y el sesgo digital quedan declarados como limitaciones."
    )
    trend_text = (
        "La serie regional no contiene todavía histórico propio suficiente. "
        + (
            f"Como referencia limitada de IN91PO: foF2 {local_trend.get('fof2', 'sin tendencia')} y "
            f"MUF {local_trend.get('mufd', 'sin tendencia')}."
            if region == "peninsula" and local_trend
            else "No se publica una flecha regional."
        )
    )
    absorption_text = (
        f"D-RAP: la frecuencia más alta afectada al menos 1 dB alcanza {drap:.1f} MHz en los puntos consultados."
        if drap_valid and drap is not None
        else "D-RAP regional no está validado en esta ejecución."
    )
    nv80 = "favorable" if finite(fof2) is not None and finite(fof2) >= 3.5 else "no respaldado"
    nv40 = "favorable" if finite(fof2) is not None and finite(fof2) >= 7.0 else "no respaldado para cobertura muy corta"
    nv20 = "banda de salto medio/largo; no NVIS ordinario"
    uncertainties = [
        "MUF(3000) no representa por sí sola el peor punto de una ruta completa.",
        "No se mide el ruido local, la antena, la potencia ni la ocupación de banda.",
        "La geometría exacta del terminador no forma parte del conjunto validado actual.",
    ]
    if not observed:
        uncertainties.append("No hay confirmación observacional regional DXView/PSKReporter en esta ejecución.")
    if region == "peninsula" and len(subregion_bands) > 1:
        uncertainties.append("Las macrozonas peninsulares cambian la banda conservadora de referencia.")

    blocks = [
        sources_table(region, sources, now),
        f"foF2 regional {fof2_text}; MUF(3000) {muf_text}. Referencia conservadora actual: **{main_text}**. QRN modelado: {qrn}.",
        f"Informe generado {iso_utc(now)}. KC2G regional: {source_age(sources['kc2g_spain'], now)}; NOAA: {source_age(sources['noaa'], now)}; QRN: {source_age(sources['qrn'], now)}.",
        (
            f"SFI {fmt(solar_flux.get('observed_flux_sfu'), suffix=' sfu')}; SSN {fmt(sunspots.get('sunspot_number'), digits=0)}; "
            f"Kp {fmt(geomagnetic.get('kp'))}; A {fmt(geomagnetic.get('a_index'))}; "
            f"rayos X {xray.get('class') or 'no validados'}; escalas R/S/G "
            f"{current_scales.get('R', 'N/D')}/{current_scales.get('S', 'N/D')}/{current_scales.get('G', 'N/D')}."
        ),
        absorption_text + " La absorción se interpreta por banda; no se deduce un apagón si R y D-RAP no lo respaldan.",
        "Las fuentes con respuesta inválida, datos obsoletos, escala espacial incompatible o sin dato útil reciben peso cero. Consulte la tabla del bloque 0.",
        (
            f"Mediana regional: foF2 {fof2_text}, MUF(3000) {muf_text}, FOT orientativa 0,85×MUF {fot_text}.\n\n"
            + (subregions_text if region == "peninsula" else "El resumen usa todos los puntos representativos definidos para la región.")
        ),
        trend_text,
        observation_text,
        f"80 m: {nv80}. 40 m: {nv40}. 20 m: {nv20}. Añada absorción D y QRN antes de decidir.",
        (
            f"Primera referencia: **{main_text}**. Pruebe una banda inferior como alternativa de robustez. "
            "FT8/CW puede confirmar señales marginales antes de pasar a SSB. "
            + ("La actividad observada compatible figura en el bloque 8." if observed else "No hay confirmación observacional regional suficiente.")
        ),
        "No hay geometría solar regional validada en esta versión del conjunto de datos; no se anuncia una ventana greyline concreta.",
        f"Riesgo QRN actual modelado: **{qrn}**. Open-Meteo no aporta detección directa de rayos ni mide el ruido de la estación.",
        "No se afirma Es, long path, TEP ni recuperación sin evidencia compatible. Una apertura por encima de la MUF local exige confirmación antes de clasificar su mecanismo.",
        f"Índice operativo de confianza: **{confidence}% ({confidence_label})**. Es una rúbrica documental y predictiva, no una probabilidad calibrada de QSO.",
        "\n".join(f"- {item}" for item in uncertainties),
        (
            f"1. Empiece por {main_text}.\n2. Compruebe waterfall/balizas durante 5-10 minutos.\n"
            "3. Si no hay señales, baje una banda y no prolongue la prueba marginal más de 20-30 minutos."
        ),
        f"Pruebe **{main_text}** ahora; si no hay señales en 5-10 minutos, baje una banda. QRN modelado: {qrn}.",
    ]
    markdown = "\n\n".join(
        f"## {index}. {BLOCK_TITLES[index]}\n\n{body}"
        for index, body in enumerate(blocks)
    )
    return {
        "label": definition["label"],
        "status": "ok" if summary is not None else "degraded",
        "report_markdown": markdown,
    }


def build_report(data_dir: Path = DATA, now: datetime | None = None) -> dict[str, Any]:
    now = (now or utc_now()).astimezone(timezone.utc)
    sources = {
        key: load_optional(data_dir / filename)
        for key, filename in SOURCE_FILES.items()
    }
    regions = {
        key: build_region(key, sources, now)
        for key in REGIONS
    }
    return {
        "schema_version": "1.0",
        "status": "ok" if all(value["status"] == "ok" for value in regions.values()) else "degraded",
        "generated_at_utc": iso_utc(now),
        "valid_until_utc": iso_utc(now + timedelta(minutes=90)),
        "regions": regions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = build_report(args.data_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
