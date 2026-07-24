    color = "#2e8b57" if delta > 0.5 else "#c94c4c" if delta < -0.5 else "#607d9b"
    return f'<strong style="color:{color};font-size:1.15em">{arrow}</strong> {abs(delta):.1f} zonas'

def nvis_reach_estimate(region: str, metrics: dict[str, Any]) -> str:
    """Classify observed reach, distinguishing local and external EA areas."""
    reports = int(get(metrics, "report_count", default=0) or 0)
    distance = get(metrics, "distance_km", "median", default=None)
    areas = [str(area) for area in get(metrics, "ea_areas", default=[]) if str(area).startswith("EA")]
    local_areas = {
        "peninsula": {"EA1", "EA2", "EA3", "EA4", "EA5", "EA7"},
        "baleares": {"EA6"},
        "canarias": {"EA8"},
    }.get(region, set())
    external_areas = [area for area in areas if area not in local_areas]
    has_local = any(area in local_areas for area in areas)
    try:
        distance = float(distance)
    except (TypeError, ValueError):
        distance = None
    if reports < 3 or distance is None:
        return "Sin evidencia suficiente"
    if distance < 500:
        area_text = ", ".join(areas) if areas else "zonas EA no identificadas"
        return f"Probablemente corta ({area_text})"
    if distance < 1500:
        if has_local and external_areas:
            return f"Selectiva por zonas ({', '.join(external_areas)})"
        return "Mixta"
    if distance >= 2500:
        if not external_areas:
            return "Probablemente larga (zonas EA lejanas no identificadas)"
        return f"Probablemente larga ({', '.join(external_areas)})"
    if not external_areas:
        return "Selectiva por zonas (zonas EA no identificadas)"
    return f"Selectiva por zonas ({', '.join(external_areas)})"


def reliability_index(region: str, source: dict[str, Any], dx_source: dict[str, Any], kc_source: dict[str, Any]) -> int: