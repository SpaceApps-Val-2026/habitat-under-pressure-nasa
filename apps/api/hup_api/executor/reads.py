"""Where each datum came from: a thin recording proxy around hup_data.readers for ingest steps.

The operation gets `Recording(readers)` as ctx.data. Every attribute is forwarded untouched; each read_series call
is also written down (origin, sources, period, coverage) so that the executor can emit one data_read event per
read after the operation returns, or a warning when the reader answered with an absence.
"""
import json
import logging
import threading
import time
from datetime import UTC, datetime

from hup_api import requests_log
from hup_api.library import folder
from hup_api.library.plan_cache import _date
from hup_data import cancellation, store

ORIGINS = ("derived", "cache", "package", "remote")
log = logging.getLogger("hup_api.executor.reads")


class Recording:
    def __init__(self, readers, analysis_id=None, step=None, sources=(), *, area=None):
        self.readers = readers
        self._analysis_id = analysis_id
        self._step = step
        self._sources = sources
        self._area = area
        self._lock = threading.Lock()
        self.reads: list[dict] = []

    def __getattr__(self, name):
        return getattr(self.readers, name)

    def _timed(self, kind, variable, fn, *args, **options):
        cancellation.checkpoint()
        started = time.perf_counter()
        got, outcome = None, "error"
        try:
            got = fn(*args, **options)
            outcome = "miss" if got is None else "absent" if isinstance(got, dict) and got.get("absence") else "ok"
            return got
        finally:
            ended = time.perf_counter()
            signal = cancellation.signal.get()
            requested_at = signal.requested_at if signal is not None else None
            if self._analysis_id is not None:
                try:
                    self._timing(kind, variable, got or {}, outcome, ended - started,
                                 ended - requested_at if requested_at is not None and requested_at <= ended else None)
                except Exception:
                    log.warning("data read timings: a line could not be written")

    def _timing(self, kind, variable, got, outcome, seconds, stop_seconds):
        parts = (got.get("categories") or {}).values() if got.get("categories") else [got]
        sources = sorted({ref["source"] for part in parts for ref in part.get("sources", [])})
        origins = sorted({part["origin"] for part in parts if part.get("origin") in ORIGINS})
        line = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "analysis_id": self._analysis_id,
                "step": self._step, "kind": kind, "variable": variable, "sources": sources or list(self._sources),
                "origins": ["derived"] if kind == "derived_result" else origins,
                "outcome": "cancelled" if stop_seconds is not None else outcome, "seconds": round(seconds, 6)}
        if self._area is not None:
            line["area"] = self._area
        if stop_seconds is not None:
            line["stop_seconds"] = round(max(0, stop_seconds), 6)
        if reason := (got.get("absence") or {}).get("reason"):
            line["absence"] = reason
        elif outcome == "ok" and kind != "derived_result" and not got.get("categories") \
                and (count := _count(got)) is not None:
            line["n_items"] = count  # scenes, months or features read: seconds per item tells a slow source apart
        requests_log.write_line(json.dumps(line, ensure_ascii=False) + "\n", stream="data-reads")

    def derived(self, variable, fingerprint):
        return self._timed("derived_result", variable, store.get_derived, fingerprint)

    def read_series(self, variable: str, zone: dict, period: dict, **options) -> dict:
        kind = "read_grid_series" if options.get("grid_resolution_m") is not None else "read_series"
        got = self._timed(kind, variable, self.readers.read_series, variable, zone, period, **options)
        return self._record(variable, zone, period, got)

    def read_exposure(self, params: dict, zone: dict, period: dict) -> dict:
        got = self._timed("read_exposure", params["variable"], self.readers.read_exposure, params, zone, period)
        if categories := got.get("categories"):
            for result in categories.values():
                self._record(params["variable"], zone, period, result)
            return got
        return self._record(params["variable"], zone, period, got)

    def read_zone_layer(self, variable, zone, period, scenario, *, windows=None, margin_km=None):
        from hup_data import layers

        options = {"windows": windows, "margin_km": margin_km} if windows is not None else {}
        meta = self._timed("read_zone_layer", variable, layers.zone_layers, variable, zone, period,
                           scenario, **options)
        self._record(variable, zone, period, {**meta, "data": meta["frames"]})
        if meta["absence"]:
            frame = {"t": period["start"][:4], "absence": meta["absence"]}
            return {"missing": layers.layer_result({**meta, "period": period}, frame, None) | {"period": period}}
        bodies = {}
        for frame in meta["frames"]:
            cancellation.checkpoint()
            path = None
            if frame.get("file"):
                fp = meta["fingerprint"]
                path = f"layers/{variable}-{fp[:16]}{frame['file']}"
                target = folder.directory(self._analysis_id) / path
                if not target.is_file():
                    folder.write_atomic(target, store.derived_path(fp, frame["file"]).read_bytes())
            bodies[frame.get("window", frame["t"])] = layers.layer_result(meta, frame, path)
        return bodies

    def _record(self, variable, zone, period, got):
        cancellation.checkpoint()
        with self._lock:
            self.reads.append({"variable": variable, "zone": zone, "period": period, "got": got})
        return got


def _span(data, asked: dict | None) -> dict | None:
    """First and last date of what came back, as a contract period; the period asked for if the data has none."""
    try:
        times = [str(t)[:10] for t in data["t"].tolist()]
    except (TypeError, KeyError, AttributeError, IndexError):
        times = []
    start, end = (_date(min(times), False), _date(max(times), True)) if times else (None, None)
    if isinstance(start, str) and isinstance(end, str) and len(start) == len(end) == 10:
        return {"start": start, "end": end}
    if isinstance(asked, dict) and isinstance(asked.get("start"), str) and isinstance(asked.get("end"), str):
        return {"start": asked["start"], "end": asked["end"]}
    return None


def _count(got: dict) -> int | None:
    """Items read: the sources' n_items when they say it, otherwise the dates in the data."""
    declared = [ref["n_items"] for ref in got.get("sources") or [] if isinstance(ref.get("n_items"), int)]
    if declared:
        return sum(declared)
    data = got.get("data")
    try:
        return len(data)
    except TypeError:
        n_dates = (got.get("coverage") or {}).get("n_dates")
        return int(n_dates) if isinstance(n_dates, int | float) else None


def localized_absence(absence, variable, name):
    """Replace the missing-reader diagnostic with the catalog's public label."""
    detail = absence.get("detail") or {}
    if absence.get("reason") != "not_computed" or detail.get("en") != (
        f"No reader is available for the variable {variable}."
    ):
        return absence
    return {**absence, "detail": {
        "en": f"No reader is available for “{name['en']}”.",
        "es": f"No hay lector disponible para «{name.get('es', name['en'])}».",
    }}


def events(record: dict, name=None) -> tuple[str, dict] | None:
    """The event one read leaves: ("data_read", data), ("warning", data), or None when the answer is unusable."""
    got = record["got"] if isinstance(record.get("got"), dict) else {}
    absence = got.get("absence")
    if isinstance(absence, dict) and absence.get("reason"):
        if name:
            absence = localized_absence(absence, record["variable"], name)
        detail = absence.get("detail") if isinstance(absence.get("detail"), dict) else {}
        text = {k: v for k, v in detail.items() if isinstance(v, str)}
        if "en" not in text:
            text["en"] = f"No data for {record['variable']}: {absence['reason']}."
        return "warning", {"code": str(absence["reason"]), "text": text}
    origin, count = got.get("origin"), _count(got)
    names = sorted({ref["source"] for ref in got.get("sources") or [] if isinstance(ref.get("source"), str)})
    if origin not in ORIGINS or count is None or count < 0 or not names:
        return None
    data = {"source": ", ".join(names), "origin": origin, "n_items": count}
    period = _span(got.get("data"), record.get("period"))
    if period:
        data["period"] = period
    return "data_read", data


def legacy_events(results: list[dict]) -> list[dict]:
    """Recover counts only when a legacy result retains the original read metadata."""
    recorded = []
    for result in results:
        sources = result.get("sources", [])
        counts = [ref["n_items"] for ref in sources if type(ref.get("n_items")) is int]
        count = sum(counts) if counts else None
        if count is None and result.get("method", {}).get("params", {}).get("aggregate", "none") == "none":
            count = result.get("coverage", {}).get("n_dates")
        names = sorted({ref["source"] for ref in sources})
        if type(count) is not int or count < 0 or not names:
            continue
        recorded.append({"source": ", ".join(names), "n_items": count})
    return recorded
