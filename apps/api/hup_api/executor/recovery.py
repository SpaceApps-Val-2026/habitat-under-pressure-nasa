"""A restart never leaves an analysis hanging.

Runs at startup, before anything else starts an analysis. One process runs every analysis (architecture 3.5), so
an analysis still marked `running` when the process starts belongs to a process that is gone: it gets a general
`error` event, a final `analysis_finished` with status failed, and its analysis.json says failed. A last event line
that the dead process left half written is cut off first, so that the file stays one whole event per line.

Another process may start on the same data folder while the server is working (a script, a bench, a second
copy of the app). It must not close what the server is running, so an analysis whose events were written in the
last `quiet_seconds` is left alone; the server looks again once that time has passed.
"""
import json
import logging
import threading
import time

from hup_api import contracts
from hup_api.executor.runner import active, now
from hup_api.library import folder

log = logging.getLogger("hup_api.executor")
_lock = threading.Lock()


def _whole_lines(analysis_id: str) -> bytes:
    """events.jsonl without a last line that the dead process left half written ("" when there is no file)."""
    file = folder.directory(analysis_id) / "events.jsonl"
    if not file.is_file():
        return b""
    raw = file.read_bytes()
    return raw if not raw or raw.endswith(b"\n") else raw[:raw.rfind(b"\n") + 1]


def _close(analysis_id: str, analysis: dict, *, cancel: bool = False) -> str:
    raw = _whole_lines(analysis_id)
    events = [json.loads(line) for line in raw.decode("utf-8").split("\n") if line.strip()]
    if events and (kind := events[-1]["type"]) in folder.FINAL_EVENTS:
        status = "cancelled" if kind == "analysis_cancelled" else events[-1]["data"].get("status", "failed")
        if raw != (folder.directory(analysis_id) / "events.jsonl").read_bytes():
            folder.write_atomic(folder.directory(analysis_id) / "events.jsonl", raw)
    else:
        seq = events[-1]["seq"] if events else 0
        error = {"code": "interrupted", "text": {"en": "The server restarted before this analysis finished.",
                                                 "es": "El servidor se reinició antes de que terminara este análisis."}}
        status = "cancelled" if cancel else "failed"
        closing = [] if cancel else [{"analysis": analysis_id, "seq": seq + 1, "type": "error", "ts": now(),
                                      "data": error}]
        closing.append({"analysis": analysis_id, "seq": seq + len(closing) + 1,
                        "type": "analysis_cancelled" if cancel else "analysis_finished",
                        "ts": now(), "data": {"status": status}})
        # One whole write, not two appends: on a mounted bucket each append uploads the whole object again.
        tail = "".join(json.dumps(e, ensure_ascii=False, allow_nan=False) + "\n" for e in closing)
        folder.write_atomic(folder.directory(analysis_id) / "events.jsonl", raw + tail.encode("utf-8"))
    closed = {**analysis, "status": status}
    for problem in contracts.errors("analysis", closed):
        log.error("analysis %s does not validate after recovery: %s", analysis_id, problem)
    folder.write_analysis(analysis_id, closed)
    return status


def cancel_stored(analysis_id: str) -> tuple[bool, str] | None:
    """Cancel an interrupted run that still says running during the startup grace period."""
    with _lock:
        analysis = folder.read_analysis(analysis_id)
        if analysis is None:
            return None
        status = analysis["status"]
        if status != "running":
            return False, status
        status = _close(analysis_id, analysis, cancel=True)
        return status == "cancelled", status


def _quiet_for(analysis_id: str) -> float:
    """Seconds since the analysis last wrote anything; a folder with nothing to look at counts as long quiet."""
    stamps = [file.stat().st_mtime for file in folder.directory(analysis_id).glob("*.json*") if file.is_file()]
    # Clamped at zero: on Windows the file stamp can be a few milliseconds ahead of time.time(), and a negative
    # age would read as "written a moment ago" even when the caller asked for quiet_seconds=0.
    return max(0.0, time.time() - max(stamps)) if stamps else float("inf")


def recover_interrupted(quiet_seconds: float = 0) -> list[str]:
    """Close every analysis left `running` and quiet for at least `quiet_seconds`. Returns their ids.

    Never raises for one broken folder.
    """
    closed = []
    for analysis_id in folder.ids():
        try:
            with _lock:
                if active(analysis_id):
                    continue
                analysis = folder.read_analysis(analysis_id)
                if isinstance(analysis, dict) and analysis.get("status") == "running":
                    if _quiet_for(analysis_id) < quiet_seconds:
                        log.warning("startup: analysis %s is running and was written a moment ago; left alone",
                                    analysis_id)
                        continue
                    _close(analysis_id, analysis)
                    closed.append(analysis_id)
        except Exception:
            log.exception("startup: analysis %s could not be recovered", analysis_id)
    if closed:
        log.warning("startup: closed %d analyses left running by a previous process", len(closed))
    return closed
