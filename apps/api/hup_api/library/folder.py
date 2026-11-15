"""Read and write the folder of one analysis: <data>/analyses/<id>/ (docs/paquetes/interfaces.md, section 3).

    plan.json  analysis.json  results.json  events.jsonl  and any layer or clip the steps write

The event stream reads events.jsonl, never process memory, so a running analysis and a saved one are read the
same way. JSON files are written atomically; events are appended one whole line per write, and a reader ignores
a last line that is not finished yet. With HUP_EVENTS_LOCAL_DIR set, a running analysis appends to a local file
instead and the data folder gets whole copies of it (see open_events).
"""
import asyncio
import json
import logging
import re
import threading
import time
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path

from hup_data import paths, settings, store

log = logging.getLogger("hup_api.library")
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")  # common.schema.json#/$defs/id
MAX_ID = 128  # a longer name is refused by some file systems (a 500 instead of a 404); real ids have 22
FINISHED = ("finished", "failed", "cancelled")
FINAL_EVENTS = ("analysis_finished", "analysis_cancelled")


def write_atomic(path: Path, data: bytes) -> None:
    """Write the whole file or nothing. One implementation for the whole project: hup_data.store."""
    store.write_atomic(path, data)


def valid_id(analysis_id: str) -> bool:
    return (isinstance(analysis_id, str) and len(analysis_id) <= MAX_ID and bool(_ID.fullmatch(analysis_id))
            and not analysis_id.endswith("."))


def directory(analysis_id: str) -> Path:
    """The folder of an analysis. Raises ValueError for an id that could point anywhere else."""
    if not valid_id(analysis_id):
        raise ValueError("not an analysis id")
    return paths.analysis(analysis_id)


def exists(analysis_id: str) -> bool:
    return valid_id(analysis_id) and directory(analysis_id).is_dir()


def ids() -> list[str]:
    """Every analysis folder in the store, sorted. Folders whose name is not an analysis id are left out."""
    root = paths.analyses()
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and valid_id(p.name))


def read_json(analysis_id: str, name: str):
    """plan.json, analysis.json or results.json; None when the file is not there."""
    file = directory(analysis_id) / name
    return json.loads(store.read_text(file)) if file.is_file() else None


def write_json(analysis_id: str, name: str, obj) -> None:
    text = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    write_atomic(directory(analysis_id) / name, text.encode("utf-8"))


def read_plan(analysis_id: str) -> dict | None:
    return read_json(analysis_id, "plan.json")


def read_analysis(analysis_id: str) -> dict | None:
    return read_json(analysis_id, "analysis.json")


def read_results(analysis_id: str) -> list[dict] | None:
    return read_json(analysis_id, "results.json")


def write_plan(analysis_id: str, plan: dict) -> None:
    write_json(analysis_id, "plan.json", plan)


def write_analysis(analysis_id: str, analysis: dict) -> None:
    write_json(analysis_id, "analysis.json", analysis)


def write_results(analysis_id: str, results: list[dict]) -> None:
    write_json(analysis_id, "results.json", results)


# Events of a running analysis on local disk (HUP_EVENTS_LOCAL_DIR) -------------------------------------------------
#
# A mounted bucket uploads a whole object on every append and takes about one write per second per object, while an
# analysis emits several events a second. With HUP_EVENTS_LOCAL_DIR set, the run opens its events here: each event
# is appended to a local file, and a copy of that file (always whole lines) replaces events.jsonl in the data folder
# at most once every FLUSH_SECONDS and once more when the run closes its events. Readers in this process read the
# local file while the run is alive and the data folder afterwards. If the process dies, the copy in the data
# folder is at most FLUSH_SECONDS behind and the recovery at the next start closes it as failed.

FLUSH_SECONDS = 1.0


class _Live:
    """The local events of one running analysis."""

    def __init__(self, analysis_id: str, local: Path):
        self.id, self.local = analysis_id, local
        self.target = directory(analysis_id) / "events.jsonl"  # fixed now: the flusher thread reads no settings
        self.append_lock = threading.Lock()  # one line at a time, and a copy never sees half a line
        self.flush_lock = threading.Lock()  # one copy at a time, newest last
        self.dirty = False
        self.closed = False
        self.flushed_at = float("-inf")

    def append(self, line: str) -> None:
        with self.append_lock:
            with open(self.local, "a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
            self.dirty = True

    def flush(self, final: bool = False) -> None:
        """Copy the local file to the data folder. Not more than once every FLUSH_SECONDS: the final copy waits
        out the rest of that second instead of being skipped."""
        with self.flush_lock:
            if self.closed:
                return
            wait = self.flushed_at + FLUSH_SECONDS - time.monotonic()
            if wait > 0:
                if not final:
                    return
                time.sleep(wait)
            with self.append_lock:
                if not self.dirty and not final:
                    return
                data = self.local.read_bytes() if self.local.is_file() else b""
                self.dirty = False
            write_atomic(self.target, data)
            self.flushed_at = time.monotonic()
            if final:
                self.closed = True


_live: dict[str, _Live] = {}
_live_lock = threading.Lock()
_flusher: threading.Thread | None = None


def _flush_loop() -> None:
    while True:
        time.sleep(FLUSH_SECONDS / 4)
        with _live_lock:
            entries = list(_live.values())
        for entry in entries:
            try:
                entry.flush()
            except Exception:  # noqa: BLE001 - a failed copy is tried again on the next pass and at the end
                log.warning("analysis %s: the events could not be copied to the data folder yet", entry.id)


def open_events(analysis_id: str) -> None:
    """Called by the run before its first event. With HUP_EVENTS_LOCAL_DIR unset it does nothing."""
    local_dir = settings.load().events_local_dir
    if local_dir is None:
        return
    global _flusher
    local_dir.mkdir(parents=True, exist_ok=True)
    local = local_dir / f"{directory(analysis_id).name}.events.jsonl"
    local.write_bytes(b"")
    with _live_lock:
        _live[analysis_id] = _Live(analysis_id, local)
        if _flusher is None or not _flusher.is_alive():
            _flusher = threading.Thread(target=_flush_loop, name="events-flush", daemon=True)
            _flusher.start()


def close_events(analysis_id: str) -> None:
    """Called by the run after its last event: the data folder gets the whole file, then readers go there."""
    with _live_lock:
        entry = _live.get(analysis_id)
    if entry is None:
        return
    try:
        entry.flush(final=True)
    finally:
        with _live_lock:
            _live.pop(analysis_id, None)
        entry.local.unlink(missing_ok=True)


def _live_file(analysis_id: str) -> Path | None:
    with _live_lock:
        entry = _live.get(analysis_id)
    return entry.local if entry is not None else None


def append_event(analysis_id: str, event: dict) -> None:
    """One event, one line, one write: to the local file of a live run, or else to events.jsonl."""
    line = json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
    with _live_lock:
        entry = _live.get(analysis_id)
    if entry is not None:
        entry.append(line)
        return
    file = directory(analysis_id) / "events.jsonl"
    file.parent.mkdir(parents=True, exist_ok=True)
    with open(file, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def _read_lines(file: Path) -> list[dict]:
    text = store.read_text(file)
    lines = text.split("\n")[:-1]  # whatever follows the last newline is not finished yet
    return [json.loads(line) for line in lines if line.strip()]


def read_events(analysis_id: str, after: int = 0) -> list[dict]:
    """The events with seq above `after`, in file order. A last line still being written is left for later.

    The freshest copy: the local file while this process runs the analysis, events.jsonl otherwise."""
    local = _live_file(analysis_id)
    events = None
    if local is not None:
        try:
            events = _read_lines(local)
        except FileNotFoundError:  # the run closed its events just now: the data folder has all of them
            events = None
    if events is None:
        file = directory(analysis_id) / "events.jsonl"
        if not file.is_file():
            return []
        events = _read_lines(file)
    return [e for e in events if e["seq"] > after]


def file(analysis_id: str, relative: str) -> Path | None:
    """A file inside the analysis folder, or None when it does not exist or the path leaves the folder."""
    if not exists(analysis_id) or not relative or "\x00" in relative:
        return None
    root = directory(analysis_id).resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def _finished(analysis_id: str) -> bool:
    analysis = read_analysis(analysis_id)
    return bool(analysis) and analysis.get("status") in FINISHED


async def follow_events(analysis_id: str, after: int = 0, poll: float = 0.25) -> AsyncIterator[dict]:
    """Every event after `after`, then the new ones as they are written, until the terminal event."""
    cursor = after
    while True:
        done = _finished(analysis_id)  # read before the events, so no line written in between is lost
        events = read_events(analysis_id, cursor)
        for event in events:
            yield event
            cursor = event["seq"]
            if event["type"] in FINAL_EVENTS:
                return
        if not events and (done or any(e["type"] in FINAL_EVENTS for e in read_events(analysis_id))):
            return
        await asyncio.sleep(poll)


def _time(event: dict) -> datetime:
    return datetime.fromisoformat(event["ts"].replace("Z", "+00:00"))


async def replay_events(analysis_id: str, after: int = 0, speed: float = 1.0) -> AsyncIterator[dict]:
    """The saved events after `after`, with pauses proportional to their timestamps, `speed` times faster."""
    previous = None
    for event in read_events(analysis_id, after):
        if previous is not None:
            await asyncio.sleep(max(0.0, (_time(event) - _time(previous)).total_seconds()) / speed)
        yield event
        previous = event
