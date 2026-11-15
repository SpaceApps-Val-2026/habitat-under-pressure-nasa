"""Plan cache (B5): plan fingerprint → id of a finished analysis, so two wordings of one question give one analysis.

The plan is normalised before its fingerprint: default params made explicit from the operation's JSON Schema in
the catalog, and periods written in one format. The fingerprint itself is hup_data.fingerprint.plan_fingerprint,
which already leaves out the request text, the language, the plan id, the answer and the time estimates.

The cache lives in memory only. It is rebuilt from the analysis folders (each analysis.json carries its
plan_fingerprint) at startup and whenever the set of folders changes, and an analysis is added when it finishes.
Nothing of it is written to disk: in production the container disk is lost on restart.
"""
import calendar
import copy
import logging
import re
import threading

from hup_api import catalog as catalog_module
from hup_api.library import folder
from hup_data import paths
from hup_data.fingerprint import plan_fingerprint

log = logging.getLogger("hup_api.plan_cache")

_DATE = re.compile(r"^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?(?:[T ].*)?$")

_lock = threading.Lock()
_state = {"root": None, "folders": None, "by_fp": {}, "created": {}}


# normalisation ------------------------------------------------------------------------------------------------


def _date(value, end: bool):
    """YYYY, YYYY-MM, YYYY-MM-DD or a date-time → YYYY-MM-DD; a year or a month ends on its last day."""
    match = _DATE.match(value) if isinstance(value, str) else None
    if not match:
        return value
    year, month, day = int(match[1]), match[2], match[3]
    try:
        if month is None:
            month, day = (12, 31) if end else (1, 1)
        else:
            month = int(month)
            day = int(day) if day is not None else (calendar.monthrange(year, month)[1] if end else 1)
        return f"{year:04d}-{month:02d}-{day:02d}" if calendar.monthrange(year, month)[1] >= day >= 1 else value
    except (ValueError, calendar.IllegalMonthError):
        return value


def _period(period: dict) -> dict:
    out = dict(period)
    for key in ("start", "end"):
        if key in out:
            out[key] = _date(out[key], key == "end")
    months = out.get("months")
    if isinstance(months, list) and all(isinstance(m, int) and not isinstance(m, bool) for m in months):
        months = sorted(set(months))
        if months == list(range(1, 13)):
            del out["months"]  # every month is the same as no month filter
        else:
            out["months"] = months
    return out


def _defaults(schema, value):
    """Fill the `default` of every property the value leaves out, recursively through objects."""
    if not isinstance(schema, dict) or not isinstance(value, dict):
        return value
    for name, sub in (schema.get("properties") or {}).items():
        if not isinstance(sub, dict):
            continue
        if name in value:
            value[name] = _defaults(sub, value[name])
        elif "default" in sub:
            value[name] = copy.deepcopy(sub["default"])
    return value


def normalize(plan: dict, catalog: dict) -> dict:
    """A copy of the plan with default params explicit and periods in one format. Never raises on a bad plan."""
    out = copy.deepcopy(plan)
    if isinstance(out.get("period"), dict):
        out["period"] = _period(out["period"])
    operations = {o.get("id"): o for o in catalog.get("operations", []) if isinstance(o, dict)}
    for step in out.get("steps") if isinstance(out.get("steps"), list) else []:
        if isinstance(step, dict) and isinstance(step.get("params"), dict) and step.get("operation") in operations:
            step["params"] = _defaults(operations[step["operation"]].get("params"), step["params"])
    return out


def fingerprint(plan: dict, catalog: dict) -> str:
    """plan_fingerprint of the normalised plan. The plan must be valid (executor.validate_plan)."""
    return plan_fingerprint(normalize(plan, catalog))


# the cache ----------------------------------------------------------------------------------------------------


def _keys(analysis: dict, catalog: dict | None) -> set[str]:
    """The stored fingerprint and, when it can be computed, the one of its plan normalised with this catalog."""
    keys = {analysis["plan_fingerprint"]} if isinstance(analysis.get("plan_fingerprint"), str) else set()
    if catalog is not None and isinstance(analysis.get("plan"), dict):
        try:
            keys.add(fingerprint(analysis["plan"], catalog))
        except (KeyError, TypeError, ValueError):
            pass
    return keys


def _add(analysis: dict, catalog: dict | None) -> bool:
    """Called with the lock held. The newest finished analysis wins a fingerprint."""
    if not isinstance(analysis, dict) or analysis.get("status") != "finished" or not analysis.get("id"):
        return False
    created = str(analysis.get("created") or "")
    for key in _keys(analysis, catalog):
        if key not in _state["by_fp"] or created >= _state["created"][key]:
            _state["by_fp"][key], _state["created"][key] = analysis["id"], created
    return True


def _catalog() -> dict | None:
    try:
        return catalog_module.load()
    except Exception:
        log.exception("plan cache: the catalog could not be loaded; only stored fingerprints are used")
        return None


def _read(analysis_id: str) -> dict | None:
    try:
        return folder.read_analysis(analysis_id)
    except (OSError, ValueError):
        log.warning("plan cache: analysis %s could not be read", analysis_id)
        return None


def rebuild() -> int:
    """Read every analysis folder again. Returns how many finished analyses the cache holds."""
    catalog = _catalog()
    with _lock:
        names = folder.ids()
        _state.update(root=str(paths.analyses()), folders=names, by_fp={}, created={})
        return sum(_add(_read(name), catalog) for name in names)


def _ensure() -> None:
    """Rebuild when the store moved (HUP_DATA_DIR changed) or a folder appeared or went away."""
    if _state["root"] != str(paths.analyses()) or _state["folders"] != folder.ids():
        rebuild()


def record(analysis: dict, catalog: dict | None = None) -> None:
    """An analysis has finished: from now on its plan finds it. Unfinished or failed analyses are ignored."""
    _ensure()
    with _lock:
        _add(analysis, catalog if catalog is not None else _catalog())


def existing(plan_fp: str) -> str | None:
    """Id of the finished analysis with this plan fingerprint, or None."""
    _ensure()
    analysis_id = _state["by_fp"].get(plan_fp)
    if analysis_id is None:
        return None
    analysis = _read(analysis_id) if folder.exists(analysis_id) else None
    return analysis_id if analysis and analysis.get("status") == "finished" else None


def lookup(plan: dict, catalog: dict) -> tuple[dict, str, str | None]:
    """For the assistant (B4): (normalised plan, its fingerprint, existing analysis id or None).

    The plan must be valid: run executor.validate_plan on the normalised plan first.
    """
    normalized = normalize(plan, catalog)
    plan_fp = plan_fingerprint(normalized)
    return normalized, plan_fp, existing(plan_fp)
