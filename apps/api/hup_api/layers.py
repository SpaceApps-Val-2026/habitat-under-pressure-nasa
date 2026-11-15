"""Map layers of an analysis (A11): GET /api/analyses/{id}/layers.

For the zone and period of the analysis plan, each gridded variable becomes a series of COGs, one per year, with
one shared range. The clips live in the derived tier (hup_data.layers) and are copied into the analysis folder,
under layers/, so that GET /api/analyses/{id}/files/layers/... serves them with range requests, like any other
file of the analysis. A variable that cannot be drawn comes back as an absence with its reason. Landsat
variables (A11b) come as per-pixel composites, with before and after frames for an impact analysis.
"""
import re
import shutil

from fastapi import APIRouter, Query

from hup_api.errors import ApiError, not_found
from hup_api.library import folder
from hup_data import layers, store

router = APIRouter(prefix="/api")

_NAME = r"^[a-z0-9][a-z0-9_.-]{0,63}$"


def _plan(analysis_id: str) -> dict:
    if not folder.exists(analysis_id):
        raise not_found()
    plan = folder.read_json(analysis_id, "plan.json")
    if plan is None:
        plan = (folder.read_json(analysis_id, "analysis.json") or {}).get("plan")
    if not isinstance(plan, dict) or "zone" not in plan or "period" not in plan:
        raise not_found("plan")
    return plan


def _windows(plan: dict) -> tuple[dict | None, float | None]:
    """The before and after windows of the plan, and the distance of its reference, if a step names them."""
    windows = margin = None
    for step in plan.get("steps") or []:
        params = step.get("params") or {}
        before, after = params.get("before"), params.get("after")
        if windows is None and all(isinstance(w, list) and len(w) == 2 for w in (before, after)):
            windows = {"before": before, "after": after}
        if margin is None and isinstance(params.get("reference_km"), int | float) and params["reference_km"] > 0:
            margin = float(params["reference_km"])
    return windows, margin


def _item(analysis_id: str, variable: str, plan: dict, scenario: str | None) -> dict:
    if variable in layers.PIXEL_VARIABLES:  # Landsat: per window of the plan, with its reference around the zone
        windows, margin = _windows(plan)
        meta = layers.zone_layers(variable, plan["zone"], plan["period"], windows=windows, margin_km=margin)
    else:
        meta = layers.zone_layers(variable, plan["zone"], plan["period"], scenario)
    base = f"/api/analyses/{analysis_id}/files/"
    frames = []
    for frame in meta["frames"]:
        path = None
        if frame.get("file"):
            path = f"layers/{variable}-{meta['fingerprint'][:16]}{frame['file']}"
            target = folder.directory(analysis_id) / path
            if not target.is_file():  # the name carries the fingerprint: an existing file is the same file
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(f".{target.name}.tmp")
                shutil.copyfile(store.derived_path(meta["fingerprint"], frame["file"]), temporary)
                temporary.replace(target)
        extra = {key: frame[key] for key in ("scenario", "window", "years") if frame.get(key)}
        result = layers.layer_result(meta, frame, path)
        frames.append({"t": frame["t"], **extra, "url": base + path if path else None,
                       **({"absence": result["absence"]} if result.get("absence") else {}),
                       "result": result})
    keys = ("variable", "name", "unit", "data_class", "scale", "range", "bounds", "crs", "nodata", "format",
            "sources", "support", "limits", "origin", "absence")
    extra = {key: meta[key] for key in ("windows", "margin_km", "range_percentiles") if meta.get(key) is not None}
    return {**{key: meta.get(key) for key in keys}, **extra, "frames": frames}


@router.get("/analyses/{analysis_id}/layers")
def get_layers(
    analysis_id: str,
    variable: str | None = Query(None, pattern=_NAME),
    scenario: str | None = Query(None, pattern=_NAME),
) -> dict:
    """Map layers for the zone and period of an analysis: one item per variable, one frame per year.

    Without `variable`, every variable of the plan that this computer can draw. `scenario` picks the climate
    scenario; by default the first climate scenario of the plan. Landsat variables (ndvi, lst) are drawn per
    pixel around the zone, with one frame per window when a step of the plan has `before` and `after` (impact),
    and the reference distance of the plan as the surroundings drawn.
    """
    plan = _plan(analysis_id)
    wanted = plan.get("variables") or []
    if variable is not None:
        if variable not in wanted:
            raise ApiError(404, "not_found", "No such variable in this analysis.")
        names = [variable]
    else:
        drawable = set(layers.variables())
        names = [name for name in wanted if name in drawable]
    if scenario is None:
        climate = (plan.get("scenarios") or {}).get("climate") or []
        scenario = next((s for s in climate if isinstance(s, str) and re.match(_NAME, s)), None)
    return {"items": [_item(analysis_id, name, plan, scenario) for name in names]}
