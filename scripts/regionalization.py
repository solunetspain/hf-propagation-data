from __future__ import annotations

import statistics
from typing import Any


SPAIN_POINTS = {
    "A_Coruna": (43.3623, -8.4115),
    "Santander": (43.4623, -3.8099),
    "Bilbao": (43.2630, -2.9350),
    "Valladolid": (41.6523, -4.7245),
    "IN91PO": (41.6041667, -0.7083333),
    "Madrid": (40.4168, -3.7038),
    "Barcelona": (41.3874, 2.1686),
    "Valencia": (39.4699, -0.3763),
    "Murcia": (37.9922, -1.1307),
    "Badajoz": (38.8794, -6.9707),
    "Sevilla": (37.3891, -5.9845),
    "Cadiz": (36.5271, -6.2886),
    "Malaga": (36.7213, -4.4214),
    "Almeria": (36.8340, -2.4637),
    "Ibiza": (38.9067, 1.4206),
    "Palma": (39.5696, 2.6502),
    "Mahon": (39.8885, 4.2658),
    "Arrecife": (28.9630, -13.5477),
    "Puerto_del_Rosario": (28.5004, -13.8627),
    "Las_Palmas": (28.1235, -15.4363),
    "Santa_Cruz_Tenerife": (28.4636, -16.2518),
    "Santa_Cruz_La_Palma": (28.6830, -17.7640),
}

PENINSULA_SUBREGIONS = {
    "northwest_cantabrian": {
        "label": "Noroeste y Cantábrico",
        "points": ("A_Coruna", "Santander", "Bilbao"),
    },
    "interior_ebro": {
        "label": "Interior y valle del Ebro",
        "points": ("Valladolid", "Madrid", "IN91PO"),
    },
    "mediterranean": {
        "label": "Mediterráneo",
        "points": ("Barcelona", "Valencia", "Murcia"),
    },
    "south_southwest": {
        "label": "Sur y suroeste",
        "points": ("Badajoz", "Sevilla", "Cadiz", "Malaga", "Almeria"),
    },
}

REGION_POINT_NAMES = {
    "mainland": tuple(
        name
        for subregion in PENINSULA_SUBREGIONS.values()
        for name in subregion["points"]
    ),
    "balearics": ("Ibiza", "Palma", "Mahon"),
    "canaries": (
        "Arrecife",
        "Puerto_del_Rosario",
        "Las_Palmas",
        "Santa_Cruz_Tenerife",
        "Santa_Cruz_La_Palma",
    ),
}


def _select_points(
    points: list[dict[str, Any]], names: tuple[str, ...]
) -> list[dict[str, Any]]:
    indexed = {str(point["name"]): point for point in points}
    missing = [name for name in names if name not in indexed]
    if missing:
        raise ValueError(f"Faltan puntos regionales: {', '.join(missing)}")
    return [indexed[name] for name in names]


def _metric_summary(
    rows: list[dict[str, Any]], key: str
) -> dict[str, float]:
    values = [float(row[key]) for row in rows]
    minimum = min(values)
    maximum = max(values)
    return {
        "median": round(float(statistics.median(values)), 3),
        "min": round(minimum, 3),
        "max": round(maximum, 3),
        "spread": round(maximum - minimum, 3),
    }


def summarize_points(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("No se puede resumir una región sin puntos")
    return {
        "points": len(rows),
        "fof2_mhz": _metric_summary(rows, "fof2_mhz"),
        "mufd_mhz": _metric_summary(rows, "mufd_mhz"),
    }


def build_region_summaries(
    points: list[dict[str, Any]],
) -> dict[str, Any]:
    mainland = _select_points(points, REGION_POINT_NAMES["mainland"])
    balearics = _select_points(points, REGION_POINT_NAMES["balearics"])
    canaries = _select_points(points, REGION_POINT_NAMES["canaries"])

    subregions = {}
    for key, definition in PENINSULA_SUBREGIONS.items():
        rows = _select_points(points, definition["points"])
        subregions[key] = {
            "label": definition["label"],
            "summary": summarize_points(rows),
            "points": rows,
        }

    return {
        "mainland": {
            "summary": summarize_points(mainland),
            "points": mainland,
            "subregions": subregions,
        },
        "balearics": {
            "summary": summarize_points(balearics),
            "points": balearics,
        },
        "canaries": {
            "summary": summarize_points(canaries),
            "points": canaries,
        },
    }
