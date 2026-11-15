"""How long a plan will take, from the read times measured by the executor (`data/logs/data-reads.jsonl`).

`estimate(plan, catalog)` gives every step a median and an interquartile range, and the plan its critical path: reads
run in parallel, so the total is the longest chain of dependencies, not the sum. A read is keyed by (source, shape,
expected origin): shape is "series", "grid" or "ring" for read_series and the operation otherwise; the origin is
"derived" when the step (or, for a Landsat grid, the reader's own cache) already holds the answer, "cached" when the
data is local and "cold" when it has to be fetched. With at least MIN_SAMPLES measured reads of that key the median of
the telemetry is used ("measured"); otherwise PRIORS ("prior"). No network and no data is read: only file names, the
coverage index and the telemetry, summarised once in memory and refreshed when the file grows.

Priors: medians and quartiles of the `step_finished` seconds of the 163 analyses saved up to 15 November 2026, by
source and origin (note .dev/notes/2026-11-15-investigacion-tiempos-de-analisis.md), and of `data-reads.jsonl`.
"""
import json
import logging
import threading
from collections import defaultdict, deque
from pathlib import Path

from hup_data import fingerprint, paths, settings

log = logging.getLogger("hup_api.executor.timing")

MIN_SAMPLES = 3
MAX_LINES = 20_000  # the newest lines are enough; older ones describe code that has changed
LANDSAT_YEARS = 13  # the Landsat priors and most measured reads cover 2013-2025: a cold read scales with the years
WRITE_LIMIT = 9.5  # writer/llm.py TIME_LIMIT: L2 gives up there and the template is written

# (source or "*", shape, origin) -> (median, low, high). Seconds. Where each comes from is in the note of 15 November.
PRIORS: dict[tuple[str, str, str], tuple[float, float, float]] = {
    ("landsat-c2-l2", "series", "cold"): (94.0, 64.0, 136.0),  # events: zone medians, 37 remote steps, median 107
    ("landsat-c2-l2", "grid", "cold"): (83.0, 64.0, 120.0),  # Utiel 74-91 s, Peñíscola 58-64 s, 22:28 runs 88-127 s
    ("landsat-c2-l2", "ring", "cold"): (90.0, 24.0, 130.0),  # Utiel 130 s; coastal ring without water tile 24-53 s
    ("landsat-c2-l2", "series", "cached"): (0.7, 0.5, 0.8),  # events: 7 steps from the year files
    ("nasa-power", "series", "cold"): (1.5, 0.9, 2.2),  # events: 18 remote steps
    ("nasa-power", "series", "cached"): (0.4, 0.1, 0.7),
    ("imerg-monthly", "series", "cached"): (1.5, 1.0, 2.2),  # data-reads: 22 package reads, median 1.19; events 1.95
    ("imerg-monthly", "zone_layer", "cached"): (0.4, 0.3, 0.5),
    ("imerg-daily", "series", "cached"): (2.0, 1.0, 7.0),  # events: imerg p90 7 s; daily not measured on its own
    ("nex-gddp-cmip6", "series", "cached"): (3.0, 1.5, 11.4),  # events: 8 steps
    ("mur-sst", "series", "cold"): (39.0, 30.0, 45.0),  # events: one remote read
    ("sentinel-1-grd", "series", "cold"): (30.0, 15.0, 60.0),  # not in the telemetry: two dates read in parallel
    ("ghsl-built-s", "series", "cached"): (0.3, 0.1, 0.6),  # absent without a tile in 0.01 s; with tiles ~1.3 s
    ("ghsl", "series", "cached"): (1.3, 0.6, 1.4),  # events: 2 steps
    ("black-marble", "series", "cached"): (0.35, 0.05, 0.6),  # data-reads: 5 package reads
    ("grace-fo", "series", "cached"): (0.2, 0.1, 0.3),  # data-reads: 5 package reads
    ("copernicus-ems-emsr773", "series", "cached"): (1.2, 0.3, 1.4),  # events: 6 package steps
    ("patricova", "read_exposure", "cached"): (2.0, 0.5, 31.0),  # one 31 s exposure on 14 November
    ("ine-population", "series", "cached"): (1.0, 0.3, 3.0),  # not measured: local tables
    ("*", "*", "derived"): (0.05, 0.02, 0.4),  # events: derived steps, median 0.04-0.1 s
    ("*", "*", "cached"): (1.0, 0.3, 3.0),
    ("*", "*", "cold"): (10.0, 2.0, 40.0),
}
COMPUTE = {"scan_trends": (0.05, 0.04, 0.12), "trend": (0.04, 0.03, 0.18), "baci_contrast": (0.12, 0.05, 0.5),
           "evaluate_chain": (0.03, 0.02, 0.05), "resource_estimate": (0.29, 0.2, 0.46),
           "anomaly": (0.06, 0.04, 0.08), "climate_delta": (0.04, 0.03, 0.09), "exposure": (0.03, 0.02, 0.04)}
COMPUTE_DEFAULT = (0.1, 0.03, 0.5)  # events: every compute operation has a median under 0.3 s
WRITE = (6.0, 4.0, WRITE_LIMIT)  # L2 completed in 5.85-7.36 s (log of 15 November); the limit caps the slow ones
WRITE_OFF = (0.05, 0.02, 0.1)  # no provider: the deterministic writer
REVIEW = (0.1, 0.03, 0.2)  # events: 157 reviews, median 0.03, p90 0.09


def _quantile(values: list[float], q: float) -> float:
    position = q * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def _origin(origins: list) -> str | None:
    if "remote" in origins:
        return "cold"
    if "cache" in origins or "package" in origins:
        return "cached"
    return "derived" if "derived" in origins else None


def _shape(kind: str, area) -> str | None:
    if kind == "read_series":
        return "series"
    if kind == "read_grid_series":
        return "ring" if area == "ring" else "grid"
    return {"read_zone_layer": "zone_layer", "read_exposure": "read_exposure"}.get(kind)


class Telemetry:
    """The read times of the telemetry, grouped by key. Reads only what was appended since the last time."""

    def __init__(self):
        self._lock = threading.Lock()
        self._lines: deque = deque(maxlen=MAX_LINES)
        self._offset = 0
        self._parts: set[str] = set()
        self._stats: dict | None = None

    def _file(self) -> Path:
        return paths.requests_log().with_name("data-reads.jsonl")

    def _new_lines(self) -> list[str]:
        lines = []
        file = self._file()
        size = file.stat().st_size if file.is_file() else 0
        if size < self._offset:  # replaced or truncated: start again
            self._offset, self._lines, self._stats = 0, deque(maxlen=MAX_LINES), None
        if size > self._offset:
            with open(file, "rb") as handle:
                handle.seek(self._offset)
                chunk = handle.read(size - self._offset)
            whole = chunk[:chunk.rfind(b"\n") + 1]  # a line still being written waits for the next time
            self._offset += len(whole)
            lines += whole.decode("utf-8", "replace").splitlines()
        folder = file.with_suffix("")  # logs/data-reads/ with HUP_SPLIT_APPENDS=1
        if folder.is_dir():
            for part in sorted(folder.glob("*.jsonl")):
                if part.name not in self._parts:
                    self._parts.add(part.name)
                    lines += part.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines

    def stats(self) -> dict:
        """{(source, shape, origin): sorted seconds}. If another request is refreshing, the last summary is used."""
        if not self._lock.acquire(blocking=self._stats is None):
            return self._stats or {}
        try:
            new = self._new_lines()
            for text in new:
                try:
                    line = json.loads(text)
                    if line.get("kind") == "derived_result" or line.get("outcome") not in ("ok", "absent"):
                        continue
                    shape, origin = _shape(line["kind"], line.get("area")), _origin(line.get("origins") or [])
                    seconds = float(line["seconds"])
                    sources = [s for s in line.get("sources") or [] if isinstance(s, str)]
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue  # a malformed line says nothing
                if shape and origin and seconds >= 0:
                    main = [s for s in sources if s != "jrc-gsw"] or sources  # the water mask rides with Landsat
                    self._lines.extend((source, shape, origin, seconds) for source in main)
            if new or self._stats is None:
                grouped = defaultdict(list)
                for source, shape, origin, seconds in self._lines:
                    grouped[source, shape, origin].append(seconds)
                self._stats = {key: sorted(values) for key, values in grouped.items()}
            return self._stats
        except OSError:
            log.warning("read timings could not be read")
            return self._stats or {}
        finally:
            self._lock.release()


TELEMETRY = Telemetry()


# ------------------------------------------------------------------------------------------------ expected origin
def _period(step: dict, plan: dict) -> dict:
    """The period the read asks the reader for, as hup_ops.ingest builds it (season months included)."""
    params = step["params"]
    period = dict(params.get("read_period") or plan["period"])
    if params.get("season") and not period.get("months"):
        from hup_ops.seasons import season_months

        period["months"] = season_months(params["season"], plan["zone"])
    return period


_RINGS: dict = {}  # (zone, edges) -> ring zones; the same geometry the executor builds, kept for the next estimate
_TILES: dict = {}  # (bbox, manifest size and time) -> the water tile line, or None
_GRID_HITS: set = set()  # keys found in the store: a saved read is never removed while the server runs


def _ring(zone: dict, edges: list, index: int) -> dict:
    from hup_data import places

    key = json.dumps([zone.get("geometry") or zone["bbox"], edges])
    if key not in _RINGS:
        if len(_RINGS) > 256:
            _RINGS.clear()
        _RINGS[key] = places.rings(zone, edges)
    return _RINGS[key][index]


def _grid_cached(step: dict, plan: dict, root: Path, tiles: dict) -> bool:
    """Whether the Landsat grid reader already holds this read (data or a deterministic absence)."""
    from hup_data.readers import landsat, landsat_grid

    params = step["params"]
    zone = plan["zone"]
    if params.get("area") == "ring" and params.get("ring_index") is not None:
        zone = _ring(zone, sorted(set(params["ring_km"])), params["ring_index"])
    elif params.get("area", "zone") != "zone":
        return False
    box = (tuple(zone["bbox"]), tiles["manifest"])
    if box not in _TILES:  # the manifest is read again only when it changes
        if len(_TILES) > 256:
            _TILES.clear()
        _TILES[box] = landsat.water_tile(zone)
    tile = _TILES[box]
    fields = {"landsat_grid": landsat_grid.VERSION, "variable": params["variable"],
              "zone": zone.get("geometry") or zone["bbox"], "period": _period(step, plan),
              "metres": params["grid_resolution_m"], "water": tile["sha256"] if tile else None}
    if params.get("tier1_only", True):
        fields["tier1_only"] = True
    key = fingerprint.sha(fields)
    if key in _GRID_HITS or _stored(root, key):
        _GRID_HITS.add(key)
        return True
    return False


def _stored(root: Path, fp: str) -> bool:
    """store.derived_path without reading the settings again for every step."""
    return (root / fp[:2] / f"{fp}.json").is_file()


def _read_origin(step: dict, plan: dict, catalog: dict, root: Path, tiles: dict) -> str:
    """cold, cached or derived for an ingest step whose own result is not stored."""
    if step["params"].get("grid_resolution_m"):
        try:
            return "derived" if _grid_cached(step, plan, root, tiles) else "cold"
        except Exception:  # noqa: BLE001 - a forecast never fails a request: fall back to what the plan says
            pass
    said = (step.get("data") or {}).get("origin")
    if said in ("cache", "package"):
        return "cached"
    if said == "derived":
        return "derived"
    sources = {s["id"]: s for s in catalog.get("sources", [])}
    modes = {(sources.get(s) or {}).get("access", {}).get("mode") for s in step.get("sources", [])}
    return "cold" if said == "remote" or "api" in modes else "cached"


def _reads_in(step: dict) -> int:
    """read_series over rings without an index reads one ring after another."""
    params = step["params"]
    if step["operation"] == "read_series" and params.get("area") == "ring" and params.get("ring_index") is None:
        return max(1, len(set(params.get("ring_km") or [1, 2, 3])))
    return 1


def _years(period: dict) -> int:
    try:
        return max(1, int(period["end"][:4]) - max(int(period["start"][:4]), 2013) + 1)
    except (KeyError, TypeError, ValueError):
        return LANDSAT_YEARS


# ------------------------------------------------------------------------------------------------------- estimate
def _step(step, plan, catalog, fps, stats, writer_seconds, root, tiles) -> dict:
    kind = {"ingest": "read", "write": "write", "review": "review"}.get(step.get("type"), "compute")
    out = {"id": step.get("id"), "kind": kind, "source": None, "source_name": None, "expected_origin": None}
    if kind == "write":
        return {**out, **_numbers(writer_seconds, "prior", 0)}
    if kind == "review":
        return {**out, **_numbers(REVIEW, "prior", 0)}
    fp = fps.get(step.get("id"))
    if fp and _stored(root, fp):
        origin = "derived"
    elif kind == "compute":
        return {**out, **_numbers(COMPUTE.get(step.get("operation"), COMPUTE_DEFAULT), "prior", 0)}
    else:
        origin = _read_origin(step, plan, catalog, root, tiles)
    if kind == "compute":
        return {**out, "expected_origin": "derived", **_numbers(PRIORS["*", "*", "derived"], "prior", 0)}
    params = step.get("params") or {}
    shape = ("ring" if params.get("area") == "ring" else "grid") if params.get("grid_resolution_m") else \
        "series" if step.get("operation") == "read_series" else step.get("operation")
    sources = [s for s in step.get("sources") or [] if s != "jrc-gsw"] or step.get("sources") or []
    source = sources[0] if sources else None
    names = {s["id"]: s.get("name") for s in catalog.get("sources", [])}
    name = names.get(source)
    out.update(source=source, expected_origin=origin,
               source_name=name if isinstance(name, dict) else {"en": name, "es": name} if name else None)
    measured = stats.get((source, shape, origin)) or []
    if len(measured) >= MIN_SAMPLES:
        numbers = (_quantile(measured, 0.5), _quantile(measured, 0.25), _quantile(measured, 0.75))
        basis, n = "measured", len(measured)
    else:
        numbers = PRIORS.get((source, shape, origin)) or PRIORS["*", "*", origin]
        basis, n = "prior", len(measured)
        if source == "landsat-c2-l2" and origin == "cold":  # the priors were measured on 13 years
            scale = _years(_period(step, plan)) / LANDSAT_YEARS
            numbers = tuple(value * scale for value in numbers)
    count = _reads_in(step)
    return {**out, **_numbers(tuple(value * count for value in numbers), basis, n)}


def _numbers(values, basis, n) -> dict:
    seconds, low, high = (round(float(v), 3) for v in values)
    return {"seconds": seconds, "low_seconds": min(low, seconds), "high_seconds": max(high, seconds),
            "basis": basis, "n": n}


def _fingerprints(plan: dict, catalog: dict) -> dict:
    """The fingerprint each step gets in the executor, for the cacheable ones (runner.Run.step)."""
    from hup_api.executor.validation import order

    sources = {s["id"]: s for s in catalog.get("sources", [])}
    zone, period = fingerprint.zone_key(plan["zone"]), plan["period"]
    fps, cacheable = {}, {}
    for step in order(plan["steps"]):
        inputs = {s: sources[s]["version"] for s in step.get("sources", []) if sources.get(s, {}).get("version")}
        inputs.update({d: fps[d] for d in step.get("depends_on", []) if d in fps})
        fps[step["id"]] = fingerprint.step_fingerprint(step["operation"], step["version"], step["params"], zone,
                                                       inputs, period)
        cacheable[step["id"]] = (step["operation"] not in {"read_exposure", "zone_layer", "evaluate_chain"}
                                 and not step["params"].get("grid_resolution_m")
                                 and step["type"] not in ("write", "review")
                                 and all(cacheable.get(d, False) for d in step.get("depends_on", [])))
    return {key: fp for key, fp in fps.items() if cacheable[key]}


def estimate(plan: dict, catalog: dict) -> dict:
    """The contract of POST /api/plans/estimate. The plan is a valid, normalised plan."""
    from hup_api.writer.llm import SCAN_TIME_LIMIT, opening_budget, uses_llm

    stats = TELEMETRY.stats()
    current = settings.load()
    writer_on = current.llm_provider in ("openai", "anthropic") and not current.offline and uses_llm(plan)
    writer_seconds = WRITE_OFF
    if writer_on:
        writer_seconds = (3.0, 1.0, SCAN_TIME_LIMIT) if opening_budget(plan) == SCAN_TIME_LIMIT else WRITE
    try:
        fps = _fingerprints(plan, catalog)
    except Exception:  # noqa: BLE001 - without fingerprints nothing counts as derived
        fps = {}
    root = paths.derived()
    manifest = root.parent / "manifest.jsonl"
    tiles = {"manifest": (manifest.stat().st_size, manifest.stat().st_mtime_ns) if manifest.is_file() else None}
    steps = [_step(step, plan, catalog, fps, stats, writer_seconds, root, tiles) for step in plan["steps"]]
    by_id = {step["id"]: step for step in steps}
    depends = {step["id"]: step.get("depends_on", []) for step in plan["steps"]}
    totals = {}
    for field in ("seconds", "low_seconds", "high_seconds"):
        finish: dict = {}

        def end(step_id, field=field, finish=finish):
            if step_id not in finish:
                finish[step_id] = by_id[step_id][field] + max(
                    (end(d) for d in depends[step_id] if d in by_id), default=0.0)
            return finish[step_id]

        totals[field] = round(max((end(step_id) for step_id in by_id), default=0.0), 3)
    bases = {step["basis"] for step in steps}
    basis = "measured" if bases == {"measured"} else "prior" if bases <= {"prior"} else "mixed"
    return {"total_seconds": totals["seconds"], "low_seconds": totals["low_seconds"],
            "high_seconds": totals["high_seconds"], "basis": basis, "steps": steps}


def forecast(plan: dict, catalog: dict) -> dict | None:
    """The estimate at the start of a run, or None: a forecast never stops an analysis."""
    try:
        return estimate(plan, catalog)
    except Exception:  # noqa: BLE001
        log.warning("the time forecast could not be computed", exc_info=True)
        return None


def record(analysis_id: str, predicted: dict | None, seconds: float, status: str) -> None:
    """One line in logs/forecasts.jsonl: what was forecast at the start and what the run took."""
    if predicted is None:
        return
    from datetime import UTC, datetime

    from hup_api import requests_log

    line = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "analysis_id": analysis_id,
            "status": status, "actual_seconds": round(seconds, 3), "estimated_seconds": predicted["total_seconds"],
            "low_seconds": predicted["low_seconds"], "high_seconds": predicted["high_seconds"],
            "basis": predicted["basis"]}
    try:
        requests_log.write_line(json.dumps(line, ensure_ascii=False) + "\n", stream="forecasts")
    except Exception:  # noqa: BLE001
        log.warning("the forecast line could not be written")
