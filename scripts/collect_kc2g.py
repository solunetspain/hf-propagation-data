from __future__ import annotations

import io
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
import hdf5plugin  # noqa: F401 - registra filtros HDF5/SZ
import numpy as np
import requests

BASE = "https://prop.kc2g.com/api"
PUBLIC = Path("public")
DATA = PUBLIC / "data"
DIAG = PUBLIC / "diagnostics"

IN91PO = {
    "locator": "IN91PO",
    "name": "Nuez de Ebro / Zaragoza",
    "latitude": 41.6041667,
    "longitude": -0.7083333,
}

SPAIN_POINTS = {
    "A_Coruna": (43.3623, -8.4115),
    "Bilbao": (43.2630, -2.9350),
    "Nuez_de_Ebro": (IN91PO["latitude"], IN91PO["longitude"]),
    "Barcelona": (41.3874, 2.1686),
    "Madrid": (40.4168, -3.7038),
    "Valencia": (39.4699, -0.3763),
    "Murcia": (37.9922, -1.1307),
    "Sevilla": (37.3891, -5.9845),
    "Malaga": (36.7213, -4.4214),
    "Palma": (39.5696, 2.6502),
    "Las_Palmas": (28.1235, -15.4363),
    "Santa_Cruz_Tenerife": (28.4636, -16.2518),
}

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "solunetspain-hf-propagation-data/1.0 (+GitHub Actions)",
        "Accept": "*/*",
    }
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_from_epoch(value: float | int) -> str:
    return datetime.fromtimestamp(float(value), timezone.utc).isoformat()


def age_minutes(epoch: float | int) -> float:
    return max(0.0, (now_utc().timestamp() - float(epoch)) / 60.0)


def freshness(age: float) -> str:
    if age <= 45:
        return "fresh"
    if age <= 90:
        return "degraded"
    if age <= 180:
        return "stale"
    return "unusable"


def get_json(path: str, **params: Any) -> Any:
    response = SESSION.get(f"{BASE}/{path}", params=params, timeout=45)
    response.raise_for_status()
    return response.json()


def get_bytes(path: str, **params: Any) -> bytes:
    response = SESSION.get(f"{BASE}/{path}", params=params, timeout=90)
    response.raise_for_status()
    if len(response.content) < 1024:
        raise RuntimeError(
            f"{path} devolvió solo {len(response.content)} bytes"
        )
    return response.content


def normalize_lon(lon: float) -> float:
    return ((float(lon) + 180.0) % 360.0) - 180.0


def interpolate(grid: np.ndarray, lat: float, lon: float) -> float:
    array = np.asarray(grid, dtype=float)
    if array.shape != (181, 361):
        raise ValueError(
            f"Forma inesperada {array.shape}; se esperaba (181, 361)"
        )

    lat = min(90.0, max(-90.0, float(lat)))
    lon = normalize_lon(lon)

    y = lat + 90.0
    x = lon + 180.0
    y0, x0 = int(math.floor(y)), int(math.floor(x))
    y1, x1 = min(y0 + 1, 180), min(x0 + 1, 360)
    wy, wx = y - y0, x - x0

    values = np.array(
        [
            array[y0, x0],
            array[y0, x1],
            array[y1, x0],
            array[y1, x1],
        ],
        dtype=float,
    )
    weights = np.array(
        [
            (1 - wy) * (1 - wx),
            (1 - wy) * wx,
            wy * (1 - wx),
            wy * wx,
        ],
        dtype=float,
    )

    valid = np.isfinite(values)
    if not valid.any():
        raise ValueError("Los cuatro puntos vecinos son inválidos")
    return float(np.sum(values[valid] * weights[valid]) / np.sum(weights[valid]))


def inspect_hdf5(blob: bytes) -> dict[str, Any]:
    inventory: dict[str, Any] = {}
    with h5py.File(io.BytesIO(blob), "r") as handle:
        def visitor(name: str, obj: Any) -> None:
            if isinstance(obj, h5py.Dataset):
                inventory[f"/{name}"] = {
                    "shape": list(obj.shape),
                    "dtype": str(obj.dtype),
                }

        handle.visititems(visitor)
    return inventory


def extract_maps(blob: bytes) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    inventory = inspect_hdf5(blob)
    with h5py.File(io.BytesIO(blob), "r") as handle:
        for required in ("/maps/fof2", "/maps/mufd"):
            if required not in handle:
                raise KeyError(f"No existe {required} en el HDF5")
        fof2 = np.asarray(handle["/maps/fof2"], dtype=float)
        mufd = np.asarray(handle["/maps/mufd"], dtype=float)
    return fof2, mufd, inventory


def linear_slope_per_hour(history: list[dict[str, Any]], key: str) -> float | None:
    valid = [row for row in history if isinstance(row.get(key), (int, float))]
    if len(valid) < 2:
        return None
    t0 = min(float(row["timestamp_epoch"]) for row in valid)
    x = np.array(
        [(float(row["timestamp_epoch"]) - t0) / 3600.0 for row in valid],
        dtype=float,
    )
    y = np.array([float(row[key]) for row in valid], dtype=float)
    if np.ptp(x) <= 0:
        return None
    return float(np.polyfit(x, y, 1)[0])


def label_trend(slope: float | None, threshold: float) -> str:
    if slope is None:
        return "unknown"
    if slope > threshold:
        return "rising"
    if slope < -threshold:
        return "falling"
    return "stable"


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )


def main() -> int:
    DATA.mkdir(parents=True, exist_ok=True)
    DIAG.mkdir(parents=True, exist_ok=True)

    validation = {
        "endpoint_located": True,
        "response_received": False,
        "format_parsed": False,
        "current_data_checked": False,
        "local_value_obtained": False,
    }
    diagnostic: dict[str, Any] = {
        "generated_at": now_utc().isoformat(),
        "validation": validation,
        "errors": [],
    }

    try:
        latest = get_json("latest_run.json")
        maps = get_json(
            "available_maps.json",
            past_hours=3,
            future_hours=0,
        )
        stations = get_json("stations.json", maxage=10800)
        validation["response_received"] = True

        diagnostic["latest_run"] = latest
        diagnostic["available_maps_count"] = len(maps) if isinstance(maps, list) else None
        diagnostic["stations_count"] = len(stations) if isinstance(stations, list) else None

        if not isinstance(maps, list):
            raise TypeError("available_maps.json no devolvió una lista")

        real_snapshots = [
            row for row in maps
            if str(row.get("filesuffix", "")).lower() == "now"
            and row.get("run_id") is not None
            and row.get("ts") is not None
        ]
        real_snapshots.sort(key=lambda row: float(row["ts"]), reverse=True)
        snapshots = real_snapshots[:5]

        if not snapshots:
            raise RuntimeError(
                "No se encontraron instantáneas filesuffix=now"
            )

        history: list[dict[str, Any]] = []
        hdf5_inventory: dict[str, Any] | None = None
        current_grids: tuple[np.ndarray, np.ndarray] | None = None

        for index, row in enumerate(reversed(snapshots)):
            run_id = int(row["run_id"])
            ts = float(row["ts"])
            try:
                blob = get_bytes(
                    "assimilated.h5",
                    run_id=run_id,
                    ts=ts,
                )
                fof2_grid, mufd_grid, inventory = extract_maps(blob)
                if hdf5_inventory is None:
                    hdf5_inventory = inventory

                local_fof2 = interpolate(
                    fof2_grid,
                    IN91PO["latitude"],
                    IN91PO["longitude"],
                )
                local_mufd = interpolate(
                    mufd_grid,
                    IN91PO["latitude"],
                    IN91PO["longitude"],
                )
                age = age_minutes(ts)

                history.append(
                    {
                        "run_id": run_id,
                        "timestamp_epoch": ts,
                        "timestamp_utc": iso_from_epoch(ts),
                        "age_minutes": round(age, 1),
                        "freshness": freshness(age),
                        "fof2_mhz": round(local_fof2, 3),
                        "mufd_mhz": round(local_mufd, 3),
                    }
                )

                if row is snapshots[0] or ts == float(snapshots[0]["ts"]):
                    current_grids = (fof2_grid, mufd_grid)

            except Exception as exc:
                diagnostic["errors"].append(
                    {
                        "stage": "snapshot",
                        "run_id": run_id,
                        "ts": ts,
                        "error": str(exc),
                    }
                )

        if not history:
            raise RuntimeError("No se pudo interpretar ninguna instantánea HDF5")

        validation["format_parsed"] = True
        current = max(history, key=lambda row: float(row["timestamp_epoch"]))
        validation["current_data_checked"] = current["freshness"] != "unusable"
        validation["local_value_obtained"] = True

        fof2_slope = linear_slope_per_hour(history, "fof2_mhz")
        mufd_slope = linear_slope_per_hour(history, "mufd_mhz")

        local_payload = {
            "source": "KC2G assimilated HDF5",
            "generated_at": now_utc().isoformat(),
            "location": IN91PO,
            "validation": validation,
            "current": current,
            "history": history,
            "trend": {
                "sample_count": len(history),
                "fof2_mhz_per_hour": (
                    None if fof2_slope is None else round(fof2_slope, 3)
                ),
                "fof2": label_trend(fof2_slope, 0.25),
                "mufd_mhz_per_hour": (
                    None if mufd_slope is None else round(mufd_slope, 3)
                ),
                "mufd": label_trend(mufd_slope, 0.75),
            },
        }
        write_json(DATA / "kc2g-in91po.json", local_payload)

        if current_grids is None:
            current_row = max(snapshots, key=lambda row: float(row["ts"]))
            blob = get_bytes(
                "assimilated.h5",
                run_id=int(current_row["run_id"]),
                ts=float(current_row["ts"]),
            )
            fof2_grid, mufd_grid, _ = extract_maps(blob)
        else:
            fof2_grid, mufd_grid = current_grids

        points = []
        for name, (lat, lon) in SPAIN_POINTS.items():
            points.append(
                {
                    "name": name,
                    "latitude": lat,
                    "longitude": lon,
                    "fof2_mhz": round(interpolate(fof2_grid, lat, lon), 3),
                    "mufd_mhz": round(interpolate(mufd_grid, lat, lon), 3),
                }
            )

        mainland_names = {
            "A_Coruna", "Bilbao", "Nuez_de_Ebro", "Barcelona", "Madrid",
            "Valencia", "Murcia", "Sevilla", "Malaga"
        }
        mainland = [p for p in points if p["name"] in mainland_names]
        balearics = [p for p in points if p["name"] == "Palma"]
        canaries = [
            p for p in points
            if p["name"] in {"Las_Palmas", "Santa_Cruz_Tenerife"}
        ]

        def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
            return {
                "points": len(rows),
                "fof2_mhz": {
                    "median": round(float(np.median([p["fof2_mhz"] for p in rows])), 3),
                    "min": round(float(np.min([p["fof2_mhz"] for p in rows])), 3),
                    "max": round(float(np.max([p["fof2_mhz"] for p in rows])), 3),
                },
                "mufd_mhz": {
                    "median": round(float(np.median([p["mufd_mhz"] for p in rows])), 3),
                    "min": round(float(np.min([p["mufd_mhz"] for p in rows])), 3),
                    "max": round(float(np.max([p["mufd_mhz"] for p in rows])), 3),
                },
            }

        spain_payload = {
            "source": "KC2G assimilated HDF5",
            "generated_at": now_utc().isoformat(),
            "timestamp_utc": current["timestamp_utc"],
            "age_minutes": current["age_minutes"],
            "freshness": current["freshness"],
            "warning": (
                "Resumen de puntos representativos; no es una integración "
                "areal exacta de todo el territorio."
            ),
            "regions": {
                "mainland": {"summary": summary(mainland), "points": mainland},
                "balearics": {"summary": summary(balearics), "points": balearics},
                "canaries": {"summary": summary(canaries), "points": canaries},
            },
        }
        write_json(DATA / "kc2g-spain.json", spain_payload)

        diagnostic["hdf5_inventory"] = hdf5_inventory
        diagnostic["selected_snapshots"] = snapshots
        diagnostic["validation"] = validation
        diagnostic["status"] = "ok"
        write_json(DIAG / "kc2g-diagnostic.json", diagnostic)

        status = {
            "generated_at": now_utc().isoformat(),
            "status": "ok",
            "validation": validation,
            "current": current,
            "history_samples": len(history),
        }
        write_json(DATA / "status.json", status)
        print(json.dumps(status, indent=2))
        return 0

    except Exception as exc:
        diagnostic["errors"].append(
            {"stage": "fatal", "error": str(exc)}
        )
        diagnostic["status"] = "error"
        write_json(DIAG / "kc2g-diagnostic.json", diagnostic)
        write_json(
            DATA / "status.json",
            {
                "generated_at": now_utc().isoformat(),
                "status": "error",
                "validation": validation,
                "error": str(exc),
            },
        )
        print(f"ERROR: {exc}", file=sys.stderr)
        # Se publica el diagnóstico aunque KC2G falle, pero el workflow falla
        # para que quede visible en Actions.
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
