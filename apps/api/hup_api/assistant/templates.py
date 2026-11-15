"""Plan templates (B4, the form path): one design, a zone, a period and variables become a plan, without any LLM.

Everything comes from the catalog: an operation enters only if the catalog has it, a variable only if its `designs`
include the design. The read origin of each ingest step comes from the derived store and the coverage index, or is
`remote` for an API source. The answer level is computed here, never assumed:

    complete      everything asked exists and nothing limits it
    with_limits   it runs, and `limits` says each thing that limits it (missing package, variable limits...)
    not_possible  the design has no operation or no variable; the plan is the alternative that can be answered

The same input always gives the same plan: variables in catalog order, steps numbered s1, s2... in a fixed order.
"""
import copy
import math
import re
from datetime import date

from hup_api.library import plan_cache
from hup_data import coverage, fingerprint, paths, readers, settings, store
from hup_ops.ingest import BALANCED
from hup_ops.seasons import SEASONS

LAST_COMPLETE_YEAR = date.today().year - 1

DESIGNS = ("state", "trend", "impact", "scan", "event", "scenario", "exposure")
# whatif (14 November, C26): place + catalog archetype(s) + an assumed quantity. Not a variable design: it has its
# own recipe (whatif below) and never enters the alternatives. Its context is the state of the variables listed.
ALL_DESIGNS = (*DESIGNS, "whatif")
WHATIF_CONTEXT = "state"
# PRD §7: the Landsat heat and green layers are summer layers, so their trends read the summer unless asked otherwise.
SUMMER_VARIABLES = {"lst", "ndvi"}
# A11: annual series of maxima, whose return levels return_level gives, each with the daily variable it is the maximum
# of: in an episode, the wettest day of the episode is read from it and set against the whole years before. The
# catalog has no field that says a variable is one value per year that is a maximum, so they are named here.
ANNUAL_MAXIMA = {"pr_max_1day_observed": "precipitation_daily"}
# What each design computes after reading: (operation, required). Only operations the catalog has enter the plan.
RECIPES = {
    "state": (("return_level", False),),
    "trend": (("trend", True),),
    "impact": (("baci_contrast", True),),
    "scan": (("scan_trends", True),),
    "event": (("rain_extreme", False), ("same_date_anomaly", True), ("return_level", False)),
    "scenario": (("climate_delta", True), ("uncertainty_split", False)),
    "exposure": (("exposure", True),),
}
CHAINED = {"scenario"}  # each operation of these designs reads the previous one; elsewhere, all read the data
# The order in which an alternative design is looked for when the one asked is not possible.
ALTERNATIVES = ("trend", "state", "scan", "impact", "event", "scenario", "exposure")
DESIGN_NAMES = {
    "state": ("the current state", "el estado actual"), "trend": ("the trend", "la tendencia"),
    "impact": ("a before and after contrast with a reference area", "un contraste antes y después con un entorno"),
    "scan": ("a scan of trends", "un barrido de tendencias"), "event": ("an episode", "un episodio"),
    "scenario": ("future scenarios", "escenarios futuros"), "exposure": ("exposure", "la exposición"),
    "whatif": ("what an archetype would ask for", "lo que pediría un arquetipo"),
}
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MissingInput(ValueError):
    """The template needs something the request does not say. `field` names it; `question` asks for it."""

    def __init__(self, field: str, en: str, es: str):
        super().__init__(en)
        self.field, self.question = field, {"en": en, "es": es}


def _t(en: str, es: str) -> dict:
    return {"en": en, "es": es}


def _name(variable: dict) -> tuple[str, str]:
    name = variable.get("name") or {}
    en = name.get("en") or variable["id"]
    return en, name.get("es") or en


# inputs ---------------------------------------------------------------------------------------------------------


def _period(period) -> dict:
    if not isinstance(period, dict):
        raise MissingInput("period", "Which period should the analysis cover?", "¿Qué periodo debe cubrir el análisis?")
    out = plan_cache._period({k: v for k, v in period.items() if k in ("start", "end", "months")})
    if not all(isinstance(out.get(k), str) and _DATE.match(out[k]) for k in ("start", "end")) \
            or out["start"] > out["end"]:
        raise MissingInput("period", "The period needs a start and an end date, in that order.",
                           "El periodo necesita una fecha de inicio y una de fin, en ese orden.")
    return out


def _window(value) -> list | None:
    ok = isinstance(value, list) and len(value) == 2 and all(
        (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, str) and _DATE.match(v)) for v in value)
    return list(value) if ok else None


def _episode(period: dict) -> tuple[int, list[int]]:
    """An episode is the period asked: its year and its months. It must fall within one calendar year."""
    start, end = period["start"], period["end"]
    if start[:4] != end[:4]:
        raise MissingInput("period", "For an episode, give its dates within one calendar year.",
                           "Para un episodio, indica sus fechas dentro de un mismo año natural.")
    months = list(range(int(start[5:7]), int(end[5:7]) + 1))
    if period.get("months"):
        months = [m for m in months if m in period["months"]] or months
    return int(end[:4]), months


# steps ----------------------------------------------------------------------------------------------------------


def _regular(variable: dict) -> bool:
    """A regular product (one value per month or per year), as opposed to a satellite with a revisit time."""
    return "revisit_days" not in (variable.get("support") or {})


def _total(variable: dict) -> bool:
    """Values that are totals per month, which add up into a year."""
    return str(variable.get("unit") or "").endswith("/month")


def _read_params(design: str, variable: dict, period: dict) -> dict:
    params = {"variable": variable["id"]}
    if design in ("trend", "scan"):
        if (variable.get("support") or {}).get("revisit_days", 0) >= 365 and not period.get("months"):
            return params  # annual source values already have one observation per year
        params["aggregate"] = "year"
        months = period.get("months")
        if _total(variable):
            params["statistic"] = "sum"
        if _regular(variable):  # a full year needs every month asked for
            params["min_count"] = len(months or range(12))
        else:
            # Satellite scenes arrive unevenly by month, so every month weighs the same, and a year needs as many
            # scenes as months on average. Decided by Guillem on 14 November 2026 (A5 preparation note, section 13).
            params["statistic"] = BALANCED
            if variable["id"] in SUMMER_VARIABLES and not months:
                params["season"] = "summer"
                months = SEASONS["summer"]["north"]  # both hemispheres have four summer months
            params["min_count"] = len(months or range(12))
    if variable["id"] in ANNUAL_MAXIMA and design in ("state", "event"):
        # The maximum of each whole year, every year of the period from the start of its own record: never the
        # maximum of the months of an episode.
        since = (variable.get("support") or {}).get("since")
        start = f"{since:04d}-01-01" if isinstance(since, int) else period["start"]
        params["read_period"] = {"start": max(start, period["start"]), "end": period["end"]}
    threshold = variable.get("threshold")
    if threshold:
        # A threshold of the catalog (a percentile over a base period) goes into the read as it is declared: the
        # person never types it (AGENTS.md, 7). The only thresholds declared are percentiles of summer surface
        # temperature (A10) and the catalog has no field for the season of a threshold, so it is read on summer.
        params["percentile"] = threshold["percentile"]
        params["base_period"] = copy.deepcopy(threshold["base_period"])
        if not period.get("months"):
            params.setdefault("season", "summer")
    return params


def _steps(design: str, variables: list[dict], operations: dict, period: dict, before, after,
           episode: tuple[int, list[int]] | None, catalog_variables: dict[str, dict] | None = None,
           episode_period: dict | None = None, chains: tuple | list = ()) -> list[dict]:
    catalog_variables = catalog_variables or {}
    reads: list[dict] = []
    computes: list[dict] = []
    read = operations["read_exposure" if design == "exposure" else "read_series"]

    def add(target, operation, params, depends_on, variable=None):
        step = {"type": operation["type"], "operation": operation["id"], "version": operation["version"],
                "params": params, "depends_on": depends_on}
        if variable is not None:
            step["sources"] = list(variable["sources"])
        target.append(step)
        return step

    by_variable: dict[str, list[dict]] = {}
    for variable in variables:
        params = _read_params(design, variable, period)
        if design == "scenario":
            # A projection is read as a change over an observed reference: when the catalog names the observed
            # variable, it is read too. When it does not, the operation says that the reference is missing.
            steps_of_variable = [add(reads, read, params, [], variable)]
            baseline = catalog_variables.get((variable.get("baseline") or {}).get("variable"))
            if baseline is not None:
                observed = {"variable": baseline["id"], "aggregate": "year",
                            "statistic": variable["baseline"].get("statistic", "mean")}
                if _regular(baseline):
                    observed["min_count"] = 12
                steps_of_variable.append(add(reads, read, observed, [], baseline))
            by_variable[variable["id"]] = steps_of_variable
        elif design == "impact":
            extra = {"zone": {}, "reference": {"reference_inner_km": 3, "reference_km": 20, "land_only": True}}
            by_variable[variable["id"]] = [add(reads, read, {**params, "area": area, **extra[area]}, [], variable)
                                           for area in ("zone", "reference")]
        elif design == "event" and catalog_variables.get(ANNUAL_MAXIMA.get(variable["id"])) and episode_period:
            # The whole years before the episode, and the days of the episode from the daily variable: a year read
            # up to the episode would give the maximum since 1 January, not the episode.
            params["read_period"]["end"] = f"{episode[0] - 1}-12-31"
            days = catalog_variables[ANNUAL_MAXIMA[variable["id"]]]
            period_of_days = {"start": episode_period["start"], "end": episode_period["end"]}
            by_variable[variable["id"]] = [add(reads, read, params, [], variable),
                                           add(reads, read, {"variable": days["id"], "read_period": period_of_days},
                                               [], days)]
        else:
            by_variable[variable["id"]] = [add(reads, read, params, [], variable)]

    for operation_id, _ in RECIPES[design]:
        operation = operations.get(operation_id)
        if operation is None:
            continue
        if design == "scan":  # one scan over every series
            add(computes, operation, {}, [s for v in variables for s in by_variable[v["id"]]])
            continue
        for variable in variables:
            if design == "impact":
                params = {"variable": variable["id"], "before": before, "after": after}
            elif design in ("state", "event") and (operation_id == "return_level") != (
                    variable["id"] in ANNUAL_MAXIMA):
                continue  # return levels only for annual maxima; an annual series has no months to compare
            elif operation_id == "return_level":
                params = {"event_year": episode[0]} if design == "event" else {}
            elif design == "event":
                if operation_id == "rain_extreme" and not _total(variable):
                    continue
                params = {"year": episode[0], "months": episode[1]}
            else:
                params = {}
                baseline = (variable.get("baseline") or {}).get("variable")
                if operation_id == "climate_delta":
                    if baseline in catalog_variables:
                        params["reference_variable"] = baseline  # which of its two readings is the observed one
                    else:
                        params["output"] = "change"
            step = add(computes, operation, params, by_variable[variable["id"]])
            if design in CHAINED:  # the next operation of the chain reads this one
                by_variable[variable["id"]] = [step]

    if design == "scan" and "evaluate_chain" in operations:
        chain_operation = operations["evaluate_chain"]
        default_minimum = chain_operation["params"]["properties"]["min_years"]["default"]
        selected = {variable["id"]: variable for variable in variables}
        for chain in chains:
            if len(chain["links"]) != 1:
                continue  # the paired reader provides one regional control for this two-variable chain
            link, = chain["links"]
            if {link["from"], link["to"]} != SUMMER_VARIABLES or not SUMMER_VARIABLES <= selected.keys():
                continue
            # Guillem validated the shorter minimum for the same-summer NDVI and LST link.
            minimum = default_minimum
            greenness_heat = (
                chain["id"] == "greenness_heat" and link["from"] == "ndvi" and link.get("lag_years") == [0, 0]
            )
            if greenness_heat:
                minimum = 10
            pair = [selected[link[role]] for role in ("from", "to")]
            resolution = max(variable["support"]["resolution_m"] for variable in pair)
            season_period = {key: period[key] for key in ("start", "end")}
            grid = {"grid_resolution_m": resolution, "grid_min_years": minimum, "read_period": season_period}
            if greenness_heat:
                grid["require_complete_months"] = True
            local = [add(reads, read, {**_read_params(design, variable, season_period), **grid}, [], variable)
                     for variable in pair]
            ring = [3, 20]
            regional = {**local[-1]["params"], "area": "ring", "ring_km": ring, "ring_index": 1}
            control = add(reads, read, regional, [], pair[-1])
            params = {"chain": {"id": chain["id"], "links": chain["links"]},
                      "regional": pair[-1]["id"], "regional_ring": ring, "min_years": minimum}
            add(computes, chain_operation, params, [*local, control])

    map_steps = []
    layer = operations.get("zone_layer")
    if layer and design == "impact":
        for contrast in computes:
            if contrast["operation"] != "baci_contrast":
                continue
            variable = catalog_variables[contrast["params"]["variable"]]
            if "landsat-c2-l2" in variable["sources"]:
                params = {"variable": variable["id"], "source": "landsat-c2-l2"}
                add(map_steps, layer, params, [contrast], variable)["sources"] = [params["source"]]
    elif layer and design in {"state", "trend", "event", "scenario"}:
        supported = layer["params"]["properties"]["source"]["enum"]
        for variable in variables:
            source = next((source for source in variable["sources"]
                           if source in supported and source != "landsat-c2-l2"), None)
            if source is None:
                continue
            params = {"variable": variable["id"], "source": source}
            if episode_period:
                params["period"] = plan_cache._period({
                    "start": episode_period["start"][:7], "end": episode_period["end"][:7], "months": episode[1],
                })
            if design == "scenario":
                params["scenario"] = "ssp245"
            add(map_steps, layer, params, [], variable)["sources"] = [source]

    steps = reads + computes + map_steps
    writers = [o for o in operations.values() if o["type"] == "write"][:1]
    reviewers = [o for o in operations.values() if o["type"] == "review"][:1]
    if writers:
        last = add(steps, writers[0], {}, (computes or reads) + map_steps)
        if reviewers:
            add(steps, reviewers[0], {}, [last])
    for number, step in enumerate(steps, start=1):  # ids last, so that dependencies can name them
        step["id"] = f"s{number}"
    for step in steps:
        step["depends_on"] = [d["id"] for d in step["depends_on"]]
    return [{k: step[k] for k in ("id", "type", "operation", "version", "params", "depends_on", "sources")
             if k in step} for step in steps]


# what limits a plan ---------------------------------------------------------------------------------------------


def _offline() -> bool:
    try:
        return settings.load().offline
    except ValueError:
        return False


def read_origin(step: dict, plan: dict, catalog: dict, offline: bool | None = None) -> tuple[str, list[dict]]:
    """Rung of the read ladder an ingest step will land on, and the limits that rung brings.

    derived when the store holds this very step; cache or package when the coverage index has files for the
    variable, zone and period; remote for an API source; otherwise package, with the limit that it is missing.
    """
    offline = _offline() if offline is None else offline
    sources = {s["id"]: s for s in catalog["sources"]}
    variables = {v["id"]: v for v in catalog["variables"]}
    variable = variables.get(step["params"].get("variable"), {"id": step["params"].get("variable")})
    en, es = _name(variable)
    inputs = {s: sources[s]["version"] for s in step.get("sources", []) if s in sources and sources[s].get("version")}
    step_fp = fingerprint.step_fingerprint(step["operation"], step["version"], step["params"],
                                           fingerprint.zone_key(plan["zone"]), inputs, plan["period"])
    if store.get_derived(step_fp) is not None:
        return "derived", []
    try:
        rows = coverage.query(variable["id"], plan["zone"]["bbox"], plan["period"])
    except Exception:  # noqa: BLE001 - no index is the same as an empty one: the plan says so in its limits
        rows = []
    found = {row["origin"] for row in rows if row.get("source") in step.get("sources", [])}
    if "cache" in found:
        return "cache", []
    if "package" in found:
        return "package", []
    modes = {sources[s]["access"]["mode"] for s in step.get("sources", []) if s in sources}
    if "api" in modes:
        if offline:
            return "remote", [_t(f"{en}: this server works without network, so this source cannot be asked now.",
                                 f"{es}: este servidor trabaja sin red, así que ahora no se puede consultar "
                                 "la fuente.")]
        return "remote", []
    return "package", [_t(f"{en} is not available for this place and period: its source needs a downloaded package.",
                          f"{es} no está disponible para este lugar y periodo: su fuente necesita un paquete "
                          "descargado.")]


def _support_bounds(variable: dict) -> tuple[int, int]:
    """Available year range of a variable: [since, until]. If until is missing, data reach the last complete year."""
    support = variable.get("support") or {}
    extent = variable.get("extent") or {}
    since = support.get("since")
    since = since if isinstance(since, int) else 1
    until = support.get("until")
    if not isinstance(until, int):
        e_until = extent.get("until")
        until = e_until if isinstance(e_until, int) else LAST_COMPLETE_YEAR
    return since, until


def _held(variable_id: str, zone: dict, period: dict | None = None) -> list[dict]:
    """Rows of the coverage index for this variable in the zone (any time when no period). Empty when unknown."""
    bbox = (zone or {}).get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        return []
    try:
        return coverage.query(variable_id, bbox, period or {"start": "1000-01-01", "end": "9999-12-31"})
    except Exception:  # noqa: BLE001 - no index is the same as an empty one
        return []


def _projection_end(variable: dict, zone: dict) -> int | None:
    """Last year a projected variable reaches: the catalog's `support.until`, else the last year held here for the
    zone (coverage index), else None (unknown)."""
    until = (variable.get("support") or {}).get("until")
    if isinstance(until, int):
        return until
    ends = [int(row["period"]["end"][:4]) for row in _held(variable["id"], zone) if row.get("period")]
    return max(ends) if ends else None


def _dated_input_period(design: str, variables: list[dict], zone: dict, catalog: dict,
                        operations: dict) -> tuple[dict, dict] | None:
    """When the read operation of the design takes catalogued inputs with dates (for example the flood maps of
    read_exposure), the period those inputs cover: the most recent input dated within the variables' record,
    preferring one held on this server for the zone or reached through an API. (period, input) or None."""
    read = operations.get("read_exposure" if design == "exposure" else "read_series") or {}
    properties = (read.get("params") or {}).get("properties") or {}
    dated = [item for prop in properties.values() if isinstance(prop, dict) and isinstance(prop.get("default"), list)
             for item in prop["default"]
             if isinstance(item, dict) and item.get("source") and _DATE.match(str(item.get("start", "")))]
    if not dated or not variables:
        return None
    until = min(_support_bounds(v)[1] for v in variables)
    dated = [item for item in dated if int(item["start"][:4]) <= until]
    if not dated:
        return None
    modes = {s["id"]: (s.get("access") or {}).get("mode") for s in catalog["sources"]}

    def reachable(item):
        if modes.get(item["source"]) == "api":
            return True
        window = {"start": item["start"], "end": item.get("end") or "9999-12-31"}
        return any(row.get("source") == item["source"] for v in variables for row in _held(v["id"], zone, window))

    chosen = max([item for item in dated if reachable(item)] or dated, key=lambda item: item["start"])
    first = int(chosen["start"][:4])
    last = max(first, min(int((chosen.get("end") or chosen["start"])[:4]), until))
    return {"start": f"{first:04d}-01-01", "end": f"{last:04d}-12-31"}, chosen


def _bbox_relation(zone_bbox: list, extent_bbox: list) -> str:
    """inside, partial, or outside (zone vs extent), in EPSG:4326 lon/lat boxes [w,s,e,n]."""
    zw, zs, ze, zn = zone_bbox
    ew, es, ee, en = extent_bbox
    # No intersection.
    if ze <= ew or zw >= ee or zn <= es or zs >= en:
        return "outside"
    # Zone fully inside extent.
    if zw >= ew and ze <= ee and zs >= es and zn <= en:
        return "inside"
    return "partial"


def _spatial_limit(variable: dict, zone: dict) -> tuple[bool, list[dict]]:
    """(usable, limits) for spatial extent (Sara/Guillem 5.3). usable False only when completely outside."""
    en, es = _name(variable)
    extent = variable.get("extent")
    zone_bbox = (zone or {}).get("bbox")
    if extent is None:
        return True, []
    if not isinstance(extent, dict) or extent.get("verified") is False:
        msg = _t(f"{en}: spatial coverage has not been verified for this place.",
                 f"{es}: la cobertura espacial de este lugar no está comprobada.")
        return True, [msg]
    kind = extent.get("kind")
    if kind == "global":
        return True, []
    if (kind == "bbox" and isinstance(extent.get("bbox"), list) and len(extent["bbox"]) == 4
            and isinstance(zone_bbox, list) and len(zone_bbox) == 4):
        rel = _bbox_relation(zone_bbox, extent["bbox"])
        if rel == "inside":
            return True, []
        if rel == "partial":
            return True, [_t(
                f"{en}: only part of this place is covered by the available data extent.",
                f"{es}: solo una parte de este lugar está cubierta por la extensión de los datos disponibles."
            )]
        return False, [_t(
            f"{en}: this place is outside the available data extent.",
            f"{es}: este lugar está fuera de la extensión de los datos disponibles."
        )]
    if kind == "region":
        # Region is a label only (e.g. basin); no bbox to compare -> cannot verify spatially.
        region = extent.get("region", "regional extent")
        region_es = extent.get("region", "extensión regional")
        return True, [_t(
            f"{en}: spatial coverage for this place could not be verified ({region}).",
            f"{es}: la cobertura espacial de este lugar no se ha podido comprobar ({region_es})."
        )]
    # Unknown/missing info -> treat as unverified.
    return True, [_t(f"{en}: spatial coverage has not been verified for this place.",
                     f"{es}: la cobertura espacial de este lugar no está comprobada.")]


def _period_years(period: dict) -> tuple[int, int]:
    return int(period["start"][:4]), int(period["end"][:4])


def _overlap_years(period: dict, variable: dict) -> tuple[int, int, int, int, int]:
    """(asked_first, asked_last, useful_first, useful_last, useful_count).

    useful_count is 0 when there is no overlap.
    """
    first, last = _period_years(period)
    since, until = _support_bounds(variable)
    u_first, u_last = max(first, since), min(last, until)
    if u_first > u_last:
        return first, last, since, until, 0
    return first, last, u_first, u_last, u_last - u_first + 1


def _required_minimum(design: str, variable: dict, operations: dict, period: dict) -> int | None:
    """Minimum useful years the method needs for this variable in this design, or None if not constrained here."""
    if design in ("trend", "scan"):
        schema = (operations.get("trend" if design == "trend" else "scan_trends") or {}).get("params", {})
        minimum = schema.get("properties", {}).get("min_years", {}).get("default")
        return minimum if isinstance(minimum, int) else 9
    if design == "scenario":
        # 17 years within 1995-2014 for the observed reference of a projected variable (Guillem, 5.3).
        baseline = variable.get("baseline")
        if not baseline:
            return None
        ref_first, ref_last = 1995, 2014
        since, until = _support_bounds(variable)  # baseline variable bounds checked when known; fallback on projection
        # actual baseline bounds are resolved by caller when possible
        u_first, u_last = max(ref_first, since), min(ref_last, until)
        useful = 0 if u_first > u_last else u_last - u_first + 1
        # only enforce when we can tell from this variable context; baseline-specific check is done separately
        return 17 if useful < 20 else 17  # marker handled in scenario logic
    if design == "impact":
        # 3 or 4 dates in impact (Guillem, 5.3). Interpreted as at least 3 years across the two windows.
        return None  # handled from windows
    return None


def _period_limit(design: str, variable: dict, zone: dict, period: dict, operations: dict,
                  catalog_variables: dict[str, dict] | None = None) -> tuple[bool, list[dict]]:
    """(usable, limits) for requested period and spatial extent vs variable coverage (Guillem/Sara 5.3).

    usable is False only when no year of the requested period is inside the variable's data OR the place is
    completely outside the spatial extent: the variable cannot answer and must be excluded. Otherwise the variable
    is usable (it runs); limits describe partial coverage, insufficient years (absence with reason), or unverified
    spatial coverage.
    """
    en, es = _name(variable)
    limits: list[dict] = [dict(variable["limit"])] if isinstance(variable.get("limit"), dict) else []

    # Spatial extent (5.3).
    spatial_ok, spatial_limits = _spatial_limit(variable, zone or {})
    if not spatial_ok:
        limits += spatial_limits
        return False, limits
    limits += spatial_limits

    first, last = _period_years(period)
    since, until = _support_bounds(variable)
    _, _, u_first, u_last, useful = _overlap_years(period, variable)

    # No overlap at all -> variable cannot answer this period.
    if useful == 0:
        if last < since:
            limits.append(_t(
                f"{en} starts in {since}: no year of the requested period ({first}–{last}) is covered.",
                f"{es} empieza en {since}: ningún año del periodo pedido ({first}–{last}) está cubierto."))
        elif first > until:
            limits.append(_t(
                f"{en} has data up to {until}: no year of the requested period ({first}–{last}) is covered.",
                f"{es} tiene datos hasta {until}: ningún año del periodo pedido ({first}–{last}) está cubierto."))
        else:
            limits.append(_t(
                f"{en}: no year of the requested period ({first}–{last}) is covered by available data.",
                f"{es}: ningún año del periodo pedido ({first}–{last}) está cubierto por los datos disponibles."))
        return False, limits

    full_inside_time = (first >= since and last <= until)

    # Design-specific minimums (Guillem, 5.3).
    if design in ("trend", "scan"):
        schema = (operations.get("trend" if design == "trend" else "scan_trends") or {}).get("params", {})
        minimum = schema.get("properties", {}).get("min_years", {}).get("default")
        minimum = minimum if isinstance(minimum, int) else 9
        if full_inside_time:
            return True, limits
        # Partial overlap.
        if useful >= minimum:
            limits.append(_t(
                f"{en}: {useful} years available in the requested period ({u_first}–{u_last}); the period outside "
                f"the data is not covered.",
                f"{es}: {useful} años disponibles en el periodo pedido ({u_first}–{u_last}); la parte fuera de "
                f"los datos no está cubierta."))
            return True, limits
        # Partial but below minimum -> runs, variable gives absence with reason.
        limits.append(_t(
            f"{en}: {useful} years available in the requested period ({u_first}–{u_last}); a trend needs at least "
            f"{minimum} years.",
            f"{es}: {useful} años disponibles en el periodo pedido ({u_first}–{u_last}); una tendencia necesita "
            f"al menos {minimum} años."))
        return True, limits

    if design == "impact":
        # "3 or 4 fechas en impacto": handled per windows in template/assess; here only coverage of the asked period.
        if full_inside_time:
            return True, limits
        limits.append(_t(
            f"{en}: {useful} years available in the requested period ({u_first}–{u_last}); the period outside the "
            f"data is not covered.",
            f"{es}: {useful} años disponibles en el periodo pedido ({u_first}–{u_last}); la parte fuera de los "
            f"datos no está cubierta."))
        return True, limits

    if design == "scenario":
        baseline_id = (variable.get("baseline") or {}).get("variable")
        baseline = (catalog_variables or {}).get(baseline_id) if baseline_id else None
        # Projection coverage vs requested period. The observed record ends at `until`; the projections go on to
        # the last year the catalog declares or, failing that, the last year held here (coverage index).
        if first < since:
            limits.append(_t(
                f"{en} starts in {since}: the years of the requested period before it are not covered.",
                f"{es} empieza en {since}: los años del periodo pedido anteriores no están cubiertos."))
        projected_until = _projection_end(variable, zone)
        if projected_until is not None and last > projected_until:
            limits.append(_t(
                f"{en}: the projections held here reach {projected_until}; the years after it are not covered.",
                f"{es}: las proyecciones disponibles aquí llegan a {projected_until}; los años posteriores no están "
                "cubiertos."))
        if last > until:
            reach = min(last, projected_until) if projected_until is not None else last
            observed_en, observed_es = _name(baseline) if baseline is not None else (en, es)
            limits.append(_t(
                f"{en}: the observed record of {observed_en} ends in {until}; from {until + 1} to {reach} the "
                "values come only from climate model projections, not from observations.",
                f"{es}: el registro observado de {observed_es} termina en {until}; de {until + 1} a {reach} los "
                "valores salen solo de las proyecciones de los modelos climáticos, no de observaciones."))
        if baseline is None:
            # No observed baseline: report modelled change only (A8 T3 option B).
            limits.append(_t(
                f"{en}: change relative to 1995–2014 according to the models is shown, "
                "not an observed value for the place.",
                f"{es}: se muestra el cambio respecto a 1995–2014 según los modelos, "
                "no un valor observado del lugar."))
            return True, limits
        # Observed reference of the baseline: 17 years within 1995-2014 (Guillem, 5.3).
        b_since, b_until = _support_bounds(baseline)
        ref_u_first, ref_u_last = max(1995, b_since), min(2014, b_until)
        ref_useful = 0 if ref_u_first > ref_u_last else ref_u_last - ref_u_first + 1
        ref_full = (b_since <= 1995 and b_until >= 2014)
        if not ref_full:
            if ref_useful == 0:
                limits.append(_t(
                    f"{en}: the observed reference (1995–2014) is not covered by { _name(baseline)[0] } data.",
                    f"{es}: la referencia observada (1995–2014) no está cubierta por los datos de "
                    f"{ _name(baseline)[1] }."))
            elif ref_useful >= 17:
                limits.append(_t(
                    f"{en}: observed reference uses {ref_useful} years within 1995–2014 "
                    f"({ref_u_first}–{ref_u_last}).",
                    f"{es}: la referencia observada usa {ref_useful} años dentro de 1995–2014 "
                    f"({ref_u_first}–{ref_u_last})."))
            else:
                limits.append(_t(
                    f"{en}: {ref_useful} years available for the observed reference within 1995–2014 "
                    f"({ref_u_first}–{ref_u_last}); at least 17 years are needed.",
                    f"{es}: {ref_useful} años disponibles para la referencia observada dentro de 1995–2014 "
                    f"({ref_u_first}–{ref_u_last}); se necesitan al menos 17 años."))
        return True, limits

    # Other designs: report partial coverage if any, but no specific minimum here.
    if not full_inside_time:
        limits.append(_t(
            f"{en}: {useful} years available in the requested period ({u_first}–{u_last}); the period outside the "
            f"data is not covered.",
            f"{es}: {useful} años disponibles en el periodo pedido ({u_first}–{u_last}); la parte fuera de los "
            f"datos no está cubierta."))
    return True, limits


def _variable_limits(design: str, variable: dict, zone: dict, period: dict, operations: dict,
                     catalog_variables: dict[str, dict] | None = None) -> list[dict]:
    # Kept for callers; returns limits assuming the variable is usable per 5.3.
    _, limits = _period_limit(design, variable, zone, period, operations, catalog_variables)
    return limits


def assess(plan: dict, catalog: dict, offline: bool | None = None) -> list[dict]:
    """Every limit of a valid plan that the code can see: read origins, variable limits, coverage in time.

    Sets `data.origin` on every ingest step as a side effect. Used by the templates and to correct an LLM's level.
    """
    operations = {o["id"]: o for o in catalog["operations"]}
    variables = {v["id"]: v for v in catalog["variables"]}
    limits = []
    read_variables = set()
    for step in plan["steps"]:
        if step["type"] == "ingest":
            origin, found = read_origin(step, plan, catalog, offline)
            step["data"] = {**(step.get("data") or {}), "origin": origin}
            if step["operation"] != "zone_layer":
                limits += found
                read_variables.add(step["params"].get("variable"))
    for variable_id in plan["variables"]:
        if variable_id in variables and (plan["design"] != "whatif" or variable_id in read_variables):
            limits += _variable_limits(
                plan["design"], variables[variable_id], plan.get("zone", {}),
                plan["period"], operations, variables
            )
    if plan["design"] == "impact":
        limits.append(_t("A contrast shows whether a change coincides in time and place with the reference; "
                         "it does not show what produced it.",
                         "Un contraste muestra si un cambio coincide en tiempo y lugar frente al entorno; "
                         "no muestra qué lo originó."))
    unique = []
    for limit in limits:
        if limit not in unique:
            unique.append(limit)
    return unique


# the template ---------------------------------------------------------------------------------------------------


def _fits(design: str, variables: list[dict], operations: dict) -> list[dict]:
    """The variables that support the design, when the catalog has every required operation of it."""
    read = "read_exposure" if design == "exposure" else "read_series"
    if read not in operations or any(required and op not in operations for op, required in RECIPES[design]):
        return []
    return [v for v in variables if design in (v.get("designs") or [])]


def _alternative(design: str, known: list[dict], catalog: dict, operations: dict) -> tuple[str, list[dict]] | None:
    for other in ALTERNATIVES:
        if other != design and other not in ("impact", "event") and _fits(other, known, operations):
            return other, _fits(other, known, operations)
    for other in ALTERNATIVES:  # nothing asked fits: the first variables of the catalog that answer something
        if other not in ("impact", "event"):
            fit = _fits(other, catalog["variables"], operations)
            if fit:
                return other, fit[:1]
    return None


def _decision(field: str, en: str, es: str) -> dict:
    return {"field": field, "text": _t(en, es)}


def _names(variables: list[dict]) -> tuple[str, str]:
    names = [_name(v) for v in variables]
    return ", ".join(n[0] for n in names), ", ".join(n[1] for n in names)


def _valid_period(period) -> dict | None:
    try:
        return _period(period)
    except MissingInput:
        return None


def decided_period(variables: list[dict]) -> dict:
    """The widest recent period every variable supports: from the latest declared start to the earliest end (the
    last complete year when a variable has none). With no declared start at all, the last year of that range;
    when the variables share no year, the range of the one that starts first."""
    bounds = [_support_bounds(v) for v in variables]
    end = min(until for _, until in bounds)
    starts = [since for (since, _), v in zip(bounds, variables, strict=True)
              if isinstance((v.get("support") or {}).get("since"), int)]
    start = max(starts) if starts else end
    if start > end:  # no common year: the range of the first variable
        since, end = bounds[0]
        start = since if since > 1 else end
    return {"start": f"{start:04d}-01-01", "end": f"{end:04d}-12-31"}


def _thresholds(plan: dict, catalog: dict) -> list[dict]:
    """The catalog threshold each read step carries, said in `decided` so that the person can see and correct it."""
    variables = {v["id"]: v for v in catalog["variables"]}
    decided = []
    for step in plan["steps"]:
        params = step["params"]
        variable = variables.get(params.get("variable"), {})
        if step["operation"] != "read_series" or "percentile" not in params or not variable.get("threshold"):
            continue
        en, es = _name(variable)
        start, end = params["base_period"]["start"][:4], params["base_period"]["end"][:4]
        ref = variable["threshold"]["ref"]
        decided.append(_decision(
            "threshold", f"Threshold from the catalog for {en}: percentile {params['percentile']:g} of summer "
                         f"{start}–{end}, fixed once for every year ({ref}).",
            f"Umbral del catálogo para {es}: percentil {params['percentile']:g} del verano de {start}–{end}, fijado "
            f"una vez para todos los años ({ref})."))
    return decided


def _related(text: str, candidates: list[dict]) -> list[dict]:
    """The candidates whose catalog `answers` words appear in the question."""
    words = (text or "").lower()
    return [v for v in candidates if any(a.lower() in words for a in v.get("answers") or [] if len(a) > 2)]


def template(design: str | None, zone: dict, period: dict | None, variables: list[str] | None, catalog: dict, *,
             request: dict | None = None, before=None, after=None, offline: bool | None = None,
             archetypes: list[str] | None = None, quantity=None) -> dict:
    """A valid plan, built only from the catalog. The person only states the question (AGENTS.md, «El corazón del
    sistema», 6 and 7): what the request does not say, Delfos decides from the catalog and says it in
    `answer.decided`. Only the place is ever asked (MissingInput "place")."""
    if not isinstance(zone, dict) or not zone.get("bbox") or not zone.get("name"):
        raise MissingInput("place", "Which place should be analysed?", "¿Qué lugar hay que analizar?")
    if design == "whatif":
        return whatif(zone, period, variables, catalog, archetypes=archetypes, quantity=quantity, request=request,
                      offline=offline)
    operations = {o["id"]: o for o in catalog["operations"]}
    text = (request or {}).get("text") or ""
    decided, extra_limits, alternative = [], [], None
    if design not in DESIGNS:
        design = "scan" if _fits("scan", catalog["variables"], operations) else "state"
        decided.append(_decision("design", f"The question names no kind of analysis: Delfos shows "
                                           f"{DESIGN_NAMES[design][0]}, the general answer for a place.",
                                 f"La pregunta no dice qué análisis: Delfos muestra {DESIGN_NAMES[design][1]}, la "
                                 "respuesta general para un lugar."))
    asked = [v for v in dict.fromkeys(variables or []) if isinstance(v, str)]
    if not asked:
        fit = [v for v in _fits(design, catalog["variables"], operations) if _spatial_limit(v, zone)[0]]
        related = _related(text, fit)
        chosen = related or fit
        asked = [v["id"] for v in chosen]
        if chosen:
            en, es = _names(chosen)
            why = ("the variables the catalog relates to the question", "las variables que el catálogo relaciona "
                   "con la pregunta") if related else ("the variables of the catalog that answer this analysis "
                                                      "here", "las variables del catálogo que responden a este "
                                                      "análisis aquí")
            decided.append(_decision("variables", f"Variables chosen by Delfos, {why[0]}: {en}.",
                                     f"Variables elegidas por Delfos, {why[1]}: {es}."))
    known = [v for v in catalog["variables"] if v["id"] in asked]
    asked_period = _valid_period(period)
    needs_date = None
    if design == "impact" and (_window(before) is None or _window(after) is None):
        needs_date = (DESIGN_NAMES["impact"], "comparable years before and after the date of the change",
                      "años comparables antes y después de la fecha del cambio")
    if design == "event" and (asked_period is None or asked_period["start"][:4] != asked_period["end"][:4]):
        needs_date = (DESIGN_NAMES["event"], "the dates of the episode within one year",
                      "las fechas del episodio dentro de un año")
    if needs_date is not None:
        names, what_en, what_es = needs_date
        design = next((d for d in ("trend", "scan", "state") if _fits(d, known, operations)), "state")
        en, es = _names(_fits(design, known, operations) or known)
        alternative = _t(f"{DESIGN_NAMES[design][0].capitalize()} of {en}.",
                         f"{DESIGN_NAMES[design][1].capitalize()} de {es}.")
        extra_limits.append(_t(f"{names[0].capitalize()} needs {what_en}, which the question does not give: "
                               f"Delfos shows {DESIGN_NAMES[design][0]} instead.",
                               f"{names[1].capitalize()} necesita {what_es}, que la pregunta no da: Delfos muestra "
                               f"{DESIGN_NAMES[design][1]} en su lugar."))
        before = after = None
        if design == "event":
            asked_period = None
        minimum = _required_minimum(design, {}, operations, asked_period) if asked_period else None
        fit = _fits(design, known, operations)
        if minimum and fit:
            first, last = _period_years(asked_period)
            if last - first + 1 < minimum:
                # The ladder (AGENTS.md, 7): the fallback answers over the period it can compute, and says so.
                asked_period = decided_period(fit)
                start, end = asked_period["start"][:4], asked_period["end"][:4]
                decided.append(_decision(
                    "period", f"The question asks about {first} to {last}; {DESIGN_NAMES[design][0]} needs at "
                              f"least {minimum} years, so Delfos uses the widest period the variables support: "
                              f"{start} to {end}.",
                    f"La pregunta se refiere a {first}–{last}; {DESIGN_NAMES[design][1]} necesita al menos "
                    f"{minimum} años, así que Delfos usa el periodo más amplio que admiten las variables: de "
                    f"{start} a {end}."))
    if asked_period is None:
        fit = _fits(design, known, operations) or known or _fits(design, catalog["variables"], operations)[:1]
        dated = _dated_input_period(design, fit, zone, catalog, operations) if fit else None
        if dated is not None:
            asked_period, used = dated
            names = {s["id"]: s.get("name") or s["id"] for s in catalog["sources"]}
            first, last = asked_period["start"][:4], asked_period["end"][:4]
            years = first if first == last else f"{first} to {last}"
            years_es = first if first == last else f"de {first} a {last}"
            source = names.get(used["source"], used["source"])
            decided.append(_decision("period", f"Period chosen by Delfos, the date of the most recent map of the "
                                               f"catalog that this analysis reads ({source}, {used['start']}): "
                                               f"{years}.",
                                     f"Periodo elegido por Delfos, la fecha del mapa más reciente del catálogo que "
                                     f"lee este análisis ({source}, {used['start']}): {years_es}."))
        elif fit:
            asked_period = decided_period(fit)
            first, last = asked_period["start"][:4], asked_period["end"][:4]
            decided.append(_decision("period", f"Period chosen by Delfos, the widest the variables support: "
                                               f"{first} to {last}.",
                                     f"Periodo elegido por Delfos, el más amplio que admiten las variables: de "
                                     f"{first} a {last}."))
        else:
            asked_period = period
    plan = _template(design, zone, asked_period, asked, catalog, request=request, before=before, after=after,
                     offline=offline)
    decided += _thresholds(plan, catalog)
    if extra_limits and plan["answer"]["level"] != "not_possible":
        limits = extra_limits + list(plan["answer"].get("limits", []))
        plan["answer"] = {"level": "with_limits", "limits": limits, "alternative": alternative}
    if decided:
        plan["answer"]["decided"] = decided
    return plan


def _template(design: str, zone: dict, period: dict, variables: list[str], catalog: dict, *,
              request: dict | None = None, before=None, after=None, offline: bool | None = None) -> dict:
    """The plan for a design whose inputs are all decided (by the request or by `template`)."""
    if design not in DESIGNS:
        raise MissingInput("design", "Which kind of analysis: " + ", ".join(ALL_DESIGNS) + "?",
                           "¿Qué tipo de análisis: " + ", ".join(ALL_DESIGNS) + "?")
    if not isinstance(zone, dict) or not zone.get("bbox") or not zone.get("name"):
        raise MissingInput("place", "Which place should be analysed?", "¿Qué lugar hay que analizar?")
    asked_period = _period(period)
    operations = {o["id"]: o for o in catalog["operations"]}
    order = [v["id"] for v in catalog["variables"]]
    asked = [v for v in dict.fromkeys(variables or []) if isinstance(v, str)]
    known = [v for v in catalog["variables"] if v["id"] in asked]
    limits = []
    for unknown in [v for v in asked if v not in order]:
        limits.append(_t(f"{unknown} is not in the catalog of this tool.",
                         f"{unknown} no está en el catálogo de esta herramienta."))
    chosen_design, chosen = design, _fits(design, known, operations)
    for variable in known:
        if variable not in chosen and chosen:
            en, es = _name(variable)
            limits.append(_t(f"{en} cannot be used for {DESIGN_NAMES[design][0]}.",
                             f"{es} no sirve para {DESIGN_NAMES[design][1]}."))
    alternative = None
    if not chosen:
        found = _alternative(design, known, catalog, operations)
        if found is None:
            raise MissingInput("variables", "No variable of the catalog can answer this. Which variable?",
                               "Ninguna variable del catálogo puede responder a esto. ¿Qué variable?")
        chosen_design, chosen = found
        names = [_name(v) for v in chosen]
        alternative = _t(f"{DESIGN_NAMES[chosen_design][0].capitalize()} of " + ", ".join(n[0] for n in names) + ".",
                         f"{DESIGN_NAMES[chosen_design][1].capitalize()} de " + ", ".join(n[1] for n in names) + ".")
        limits.insert(0, _t(f"This tool cannot compute {DESIGN_NAMES[design][0]} for what was asked.",
                            f"Esta herramienta no puede calcular {DESIGN_NAMES[design][1]} para lo pedido."))

    plan_period, episode = asked_period, None
    if chosen_design == "impact":
        before, after = _window(before), _window(after)
        if before is None or after is None:
            raise MissingInput("before_after", "When did the change happen? Give the years before and after it.",
                               "¿Cuándo ocurrió el cambio? Indica los años de antes y de después.")
    if chosen_design == "event":
        episode = _episode(asked_period)
        # The history before the episode is read only for variables that have a record (a series the catalog lets
        # show as a state, trend or scan). A variable that is only an episode (a pair of passes around it, as the
        # radar) is read over the episode itself: stretching its period would compare passes years apart.
        record = [v for v in chosen if {"state", "trend", "scan"} & set(v.get("designs") or [])]
        since = [s for s in ((v.get("support") or {}).get("since") for v in record) if isinstance(s, int)]
        start = f"{min(since):04d}-01-01" if since and min(since) <= episode[0] else asked_period["start"]
        plan_period = {"start": start, "end": asked_period["end"], "months": episode[1]}

    steps = _steps(chosen_design, chosen, operations, plan_period, before, after, episode,
                   {v["id"]: v for v in catalog["variables"]}, asked_period if episode else None,
                   catalog.get("chains", ()))
    language = (request or {}).get("language") or "en"
    plan = {
        "contract_version": "1.0",
        "id": "plan-" + fingerprint.sha({"design": design, "zone": fingerprint.zone_key(zone), "period": plan_period,
                                         "variables": [v["id"] for v in chosen], "before": before,
                                         "after": after})[:16],
        "request": {"text": "", "language": language, "origin": "form",
                    **{k: v for k, v in (request or {}).items() if k in ("text", "language", "level", "origin")}},
        "answer": {"level": "complete"},
        "design": chosen_design,
        "zone": copy.deepcopy(zone),
        "period": plan_period,
        "variables": [v["id"] for v in chosen],
        "catalog_version": catalog["version"],
        "steps": steps,
    }
    if chosen_design == "impact":
        plan["comparison"] = {"type": "spatial_reference"}
    # Drop variables that cannot answer in the requested period/extent (5.3) before building steps.
    catalog_by_id = {v["id"]: v for v in catalog["variables"]}
    kept, dropped_limits = [], []
    for variable in chosen:
        usable, v_limits = _period_limit(chosen_design, variable, zone, plan_period, operations, catalog_by_id)
        if usable:
            kept.append(variable)
        else:
            dropped_limits += v_limits
    if not kept and alternative is None:
        found = _alternative(chosen_design, [], catalog, operations)
        if found is None:
            raise MissingInput("variables", "No variable of the catalog can answer this. Which variable?",
                               "Ninguna variable del catálogo puede responder a esto. ¿Qué variable?")
        chosen_design, chosen = found
        names = [_name(v) for v in chosen]
        alternative = _t(f"{DESIGN_NAMES[chosen_design][0].capitalize()} of " + ", ".join(n[0] for n in names) + ".",
                         f"{DESIGN_NAMES[chosen_design][1].capitalize()} de " + ", ".join(n[1] for n in names) + ".")
        limits = dropped_limits + limits
        limits.insert(0, _t(f"This tool cannot compute {DESIGN_NAMES[design][0]} for what was asked in this period.",
                            f"Esta herramienta no puede calcular {DESIGN_NAMES[design][1]} para lo pedido en este "
                            f"periodo."))
        # recompute period fit for alternative design/variables
        plan_period2 = plan_period
        steps = _steps(chosen_design, chosen, operations, plan_period2, before, after, episode,
                       catalog_by_id, asked_period if episode else None, catalog.get("chains", ()))
        plan = {
            "contract_version": "1.0",
            "id": "plan-" + fingerprint.sha({"design": design, "zone": fingerprint.zone_key(zone),
                                             "period": plan_period2,
                                             "variables": [v["id"] for v in chosen], "before": before,
                                             "after": after})[:16],
            "request": {"text": "", "language": language, "origin": "form",
                        **{k: v for k, v in (request or {}).items() if k in ("text", "language", "level", "origin")}},
            "answer": {"level": "complete"},
            "design": chosen_design,
            "zone": copy.deepcopy(zone),
            "period": plan_period2,
            "variables": [v["id"] for v in chosen],
            "catalog_version": catalog["version"],
            "steps": steps,
        }
        if chosen_design == "impact":
            plan["comparison"] = {"type": "spatial_reference"}
    elif not kept:
        # alternative was already set but variables dropped - rebuild with alternative choice
        limits = dropped_limits + limits
        steps = _steps(chosen_design, chosen, operations, plan_period, before, after, episode,
                       catalog_by_id, asked_period if episode else None, catalog.get("chains", ()))
        plan["variables"] = [v["id"] for v in chosen]
        plan["steps"] = steps
    else:
        if dropped_limits:
            limits += dropped_limits
        if kept != chosen:
            chosen = kept
            steps = _steps(chosen_design, chosen, operations, plan_period, before, after, episode,
                           catalog_by_id, asked_period if episode else None, catalog.get("chains", ()))
            plan["variables"] = [v["id"] for v in chosen]
            plan["steps"] = steps

    plan = plan_cache.normalize(plan, catalog)
    limits += assess(plan, catalog, offline)
    unique = []
    for limit in limits:
        if limit not in unique:
            unique.append(limit)
    if alternative is not None:
        plan["answer"] = {"level": "not_possible", "limits": unique, "alternative": alternative}
    elif unique:
        plan["answer"] = {"level": "with_limits", "limits": unique}
    else:
        plan["answer"] = {"level": "complete"}
    return plan


# whatif -----------------------------------------------------------------------------------------------------------


def _archetype_list(catalog: dict) -> tuple[str, str]:
    """The archetypes of the catalog: id, name and the unit they are counted in."""
    items = catalog.get("archetypes") or []
    if not items:
        return "the catalog has no archetypes yet", "el catálogo aún no tiene arquetipos"
    en = "; ".join(f"{a['id']} ({(a.get('name') or {}).get('en', a['id'])}, per {a['per']})" for a in items)
    es = "; ".join(f"{a['id']} ({(a.get('name') or {}).get('es', a['id'])}, por {a['per']})" for a in items)
    return en, es


def _not_computable(archetype: dict) -> tuple[str, str] | None:
    """Why resource_estimate will give not_computed for this archetype (en, es), or None when it can compute."""
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    low, central, high = (archetype.get(k) for k in ("low", "central", "high"))
    if not archetype.get("ref"):
        return "it has no published reference yet", "aún no tiene referencia publicada"
    if not number(low) or not number(high) or not 0 <= low <= high or (
            central is not None and (not number(central) or not low <= central <= high)):
        return "its coefficient range is incomplete", "su rango de coeficientes está incompleto"
    return None


def _positive(quantity) -> bool:
    return (isinstance(quantity, (int, float)) and not isinstance(quantity, bool)
            and math.isfinite(quantity) and quantity > 0)


def context_variables(resources: list[str], zone: dict, catalog: dict) -> tuple[list[dict], str]:
    """The state of the place that matters for these resources, chosen from the catalog by a generic rule:
    variables that can show a state, cover the zone and whose `layer` is one of the resources (water, energy,
    land: the same words in both fields). Failing that, those whose `frame` is pressure; failing that, every
    variable that can show a state here. Returns the variables and which rule chose them."""
    usable = [v for v in catalog["variables"]
              if WHATIF_CONTEXT in (v.get("designs") or []) and _spatial_limit(v, zone)[0]]
    for rule, chosen in (("layer", [v for v in usable if v.get("layer") in resources]),
                         ("pressure", [v for v in usable if v.get("frame") == "pressure"]),
                         ("state", usable)):
        if chosen:
            return chosen, rule
    return [], "none"


def _counted_by(archetype: dict, zone: dict, catalog: dict) -> dict | None:
    """The catalog variable that counts the archetype's `per` unit at a place and can show its state (A9, option C
    of the assistant front: today population for archetypes per people), or None. A generic rule on the unit."""
    candidates = [v for v in catalog["variables"]
                  if v.get("unit") == archetype.get("per") and WHATIF_CONTEXT in (v.get("designs") or [])
                  and _spatial_limit(v, zone)[0]]
    if not candidates:
        return None
    for v in candidates:
        if v["id"] == "population":
            return v
    return candidates[0]


def _local_context(variable: dict, zone: dict, period: dict | None) -> bool:
    """Optional context needs a catalogued source with a file for this place and period."""
    return any(row["source"] in variable["sources"] and (paths.data_dir() / row["path"]).exists()
               for row in _held(variable["id"], zone, period))


def whatif(zone: dict, period, variables: list[str] | None, catalog: dict, *, archetypes=None, quantity=None,
           request: dict | None = None, offline: bool | None = None) -> dict:
    """What would it ask for if this were built here, by the answer ladder (AGENTS.md, 6 and 7):

    one resource_estimate per archetype, sized by the question when it states a size (illustrative), else by the
    archetype's typical size from the catalog, else per unit of `per` (approximated); an archetype without
    coefficients and a reference gives an absence, and the answer is the state of the place. The context
    variables and the period are decided by Delfos when the request does not give them, and said in
    `answer.decided`. Only the place is ever asked."""
    from hup_ops.resources import basis

    if not isinstance(zone, dict) or not zone.get("bbox") or not zone.get("name"):
        raise MissingInput("place", "Which place should be analysed?", "¿Qué lugar hay que analizar?")
    operations = {o["id"]: o for o in catalog["operations"]}
    catalog_archetypes = {a["id"]: a for a in catalog.get("archetypes") or []}
    asked = [a for a in dict.fromkeys(archetypes or []) if isinstance(a, str)]
    limits, decided = [], []
    listed_en, listed_es = _archetype_list(catalog)
    unknown = [a for a in asked if a not in catalog_archetypes]
    if unknown:
        limits.append(_t(f"{', '.join(unknown)} is not an archetype of the catalog ({listed_en}).",
                         f"{', '.join(unknown)} no es un arquetipo del catálogo ({listed_es})."))
    chosen = [archetype for key, archetype in catalog_archetypes.items() if key in asked]  # catalog order
    if "resource_estimate" not in operations:
        chosen = []
    if not chosen:
        limits.append(_t("No archetype of the catalog covers what would be built, so there is no estimate: the "
                         "answer is the state of the place.",
                         "Ningún arquetipo del catálogo cubre lo que se construiría, así que no hay estimación: la "
                         "respuesta es el estado del lugar."))

    stated = None
    if quantity is not None:
        units = {a["per"] for a in chosen}
        if _positive(quantity) and len(units) == 1:
            stated = {"value": float(quantity), "unit": units.pop()}
        else:
            limits.append(_t("The size stated in the question cannot be applied to these archetypes; the catalog's "
                             "published example sizes or the unit of reference are used.",
                             "El tamaño que dice la pregunta no se puede aplicar a estos arquetipos; se usan los "
                             "tamaños de ejemplo publicados del catálogo o la unidad de referencia."))

    # context: the variables the request names, or those Delfos relates to the resources asked about
    catalog_by_id = {v["id"]: v for v in catalog["variables"]}
    names = [v for v in dict.fromkeys(variables or []) if isinstance(v, str)]
    limits += [_t(f"{name} is not in the catalog of this tool.", f"{name} no está en el catálogo de esta herramienta.")
               for name in names if name not in catalog_by_id]
    candidates = []
    for variable in (v for v in catalog["variables"] if v["id"] in names):
        en, es = _name(variable)
        if WHATIF_CONTEXT not in (variable.get("designs") or []):
            limits.append(_t(f"{en} cannot be shown as the state of the place.",
                             f"{es} no sirve para mostrar el estado del lugar."))
        else:
            candidates.append(variable)
    asked_period = _valid_period(period)
    registered = set(readers.variables())
    local_variables = [v for v in catalog["variables"] if v["id"] in registered
                       and WHATIF_CONTEXT in (v.get("designs") or []) and _local_context(v, zone, asked_period)]
    local_ids = {v["id"] for v in local_variables}
    candidates = [v for v in candidates if v["id"] in local_ids]
    resources = sorted({a["resource"] for a in chosen})
    rule = "asked"
    if not candidates:
        candidates, rule = context_variables(resources, zone, {**catalog, "variables": local_variables})
    if asked_period is None:
        asked_period = decided_period(candidates) if candidates else {
            "start": f"{LAST_COMPLETE_YEAR}-01-01", "end": f"{LAST_COMPLETE_YEAR}-12-31",
        }
        period_en, period_es = (
            ("the widest the context variables support", "el más amplio que admiten las variables de contexto")
            if candidates else ("the last complete year", "el último año completo")
        )
        decided.append(_decision("period", f"Period chosen by Delfos, {period_en}: "
                                           f"{asked_period['start'][:4]} to {asked_period['end'][:4]}.",
                                 f"Periodo elegido por Delfos, {period_es}: "
                                 f"de {asked_period['start'][:4]} a {asked_period['end'][:4]}."))
    context = []
    for variable in candidates:
        usable, found = _period_limit(WHATIF_CONTEXT, variable, zone, asked_period, operations, catalog_by_id)
        if usable and _local_context(variable, zone, asked_period):
            limits += found
            context.append(variable)
    if not context:
        decided.append(_decision("variables", "No local data were verified for optional context.",
                                 "No se verificaron datos locales para añadir contexto opcional."))
        if not any(_not_computable(a) is None for a in chosen):
            limits.append(_t("No local data were verified for this place and period.",
                             "No se verificaron datos locales para este lugar y periodo."))
    elif rule != "asked":
        en, es = _names(context)
        why = {"layer": ("its layer is the resource asked about", "su capa es el recurso por el que se pregunta"),
               "pressure": ("the catalog has no variable of that resource here; these are the pressures on the place",
                            "el catálogo no tiene variables de ese recurso aquí; estas son las presiones sobre el "
                            "lugar"),
               "state": ("the catalog relates no variable to the resource; this is the general state of the place",
                         "el catálogo no relaciona ninguna variable con el recurso; es el estado general del lugar"),
               }.get(rule, ("", ""))
        decided.append(_decision("variables", f"Context chosen by Delfos ({why[0]}): {en}.",
                                 f"Contexto elegido por Delfos ({why[1]}): {es}."))

    reads, computes = [], []

    def add(target, operation, params, depends_on, sources=None):
        step = {"type": operation["type"], "operation": operation["id"], "version": operation["version"],
                "params": params, "depends_on": depends_on}
        if sources is not None:
            step["sources"] = list(sources)
        target.append(step)
        return step

    read_steps = {}
    for variable in context:
        read_steps[variable["id"]] = add(reads, operations["read_series"],
                                         _read_params(WHATIF_CONTEXT, variable, asked_period), [], variable["sources"])
    for archetype in chosen:
        params = {"archetype": copy.deepcopy(archetype)}
        if stated is not None:
            params["quantity"] = dict(stated)
        label = archetype.get("name") or {}
        en, es = label.get("en", archetype["id"]), label.get("es", archetype["id"])
        why = _not_computable(archetype)
        counted = None if stated is not None or why is not None else _counted_by(archetype, zone, catalog)
        if counted is not None:  # A9, option C: the size is the place's own figure, read from an informant
            usable, found = _period_limit(WHATIF_CONTEXT, counted, zone, asked_period, operations, catalog_by_id)
            if usable:
                limits += found
                if counted["id"] not in read_steps:
                    read_steps[counted["id"]] = add(reads, operations["read_series"],
                                                    _read_params(WHATIF_CONTEXT, counted, asked_period), [],
                                                    counted["sources"])
            else:
                counted = None
        add(computes, operations["resource_estimate"], params, [read_steps[counted["id"]]] if counted else [])
        if why is not None:
            limits.append(_t(f"{en}: {why[0]}, so there is no estimate and the answer is the state of the place.",
                             f"{es}: {why[1]}, así que no hay estimación y la respuesta es el estado del lugar."))
            continue
        step = basis(params, counted is not None)
        if step == "input":
            name_en, name_es = _name(counted)
            decided.append(_decision("size", f"{en}: estimate for the place, with its {name_en} read from the "
                                             "catalog's sources (the last year with a value).",
                                     f"{es}: estimación para el lugar, con su {name_es} leída de las fuentes del "
                                     "catálogo (el último año con valor)."))
        elif step == "assumed":
            limits.append(_t(f"{en}: the size ({stated['value']:g} {stated['unit']}) is the one stated in the "
                             "question, an assumption: the estimate is illustrative.",
                             f"{es}: el tamaño ({stated['value']:g} {stated['unit']}) es el que dice la pregunta, un "
                             "supuesto: la estimación es ilustrativa."))
        elif step == "typical":
            decided.append(_decision("size", f"{en}: estimate for a published example size, the one "
                                             "the catalog gives with its reference.",
                                     f"{es}: estimación para un tamaño de ejemplo publicado, el que da el "
                                     "catálogo con su referencia."))
        else:
            decided.append(_decision("size", f"{en}: the catalog has no published example size, so the "
                                             f"answer is per {archetype['per']}.",
                                     f"{es}: el catálogo no tiene un tamaño de ejemplo publicado, así que la "
                                     f"respuesta es por {archetype['per']}."))
    steps = reads + computes
    writers = [o for o in operations.values() if o["type"] == "write"][:1]
    reviewers = [o for o in operations.values() if o["type"] == "review"][:1]
    if writers:
        last = add(steps, writers[0], {}, computes + reads)
        if reviewers:
            add(steps, reviewers[0], {}, [last])
    for number, step in enumerate(steps, start=1):
        step["id"] = f"s{number}"
    for step in steps:
        step["depends_on"] = [d["id"] for d in step["depends_on"]]
    steps = [{k: step[k] for k in ("id", "type", "operation", "version", "params", "depends_on", "sources")
              if k in step} for step in steps]

    language = (request or {}).get("language") or "en"
    # The existing plan contract requires a catalog variable even in an estimate-only whatif.
    # Keep the catalog reference in the plan, while the steps record what will actually be read.
    context_ref, _ = context_variables(resources, zone, catalog) if not read_steps else ([], "none")
    plan_variables = list(read_steps) or [(context_ref or catalog["variables"])[0]["id"]]
    plan = {
        "contract_version": "1.0",
        "id": "plan-" + fingerprint.sha({"design": "whatif", "zone": fingerprint.zone_key(zone),
                                         "period": asked_period, "variables": [v["id"] for v in context],
                                         "archetypes": [a["id"] for a in chosen], "quantity": stated})[:16],
        "request": {"text": "", "language": language, "origin": "form",
                    **{k: v for k, v in (request or {}).items() if k in ("text", "language", "level", "origin")}},
        "answer": {"level": "complete"},
        "design": "whatif",
        "zone": copy.deepcopy(zone),
        "period": asked_period,
        "variables": plan_variables,
        "catalog_version": catalog["version"],
        "steps": steps,
    }
    plan = plan_cache.normalize(plan, catalog)
    limits += assess(plan, catalog, offline)
    unique = []
    for limit in limits:
        if limit not in unique:
            unique.append(limit)
    plan["answer"] = {"level": "with_limits" if unique else "complete"}
    if unique:
        plan["answer"]["limits"] = unique
    if decided:
        plan["answer"]["decided"] = decided
    return plan
