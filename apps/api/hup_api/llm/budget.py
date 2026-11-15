"""A cap on LLM spend per UTC day (HUP_LLM_DAILY_BUDGET_USD). Unset: no cap and nothing is written.

The adapter asks allow() before every attempt and calls spend() after it, with the cost of its own accounting.
Spend is kept as one small file per call in <data>/llm_spend/<YYYY-MM-DD>/, never by appending, so it survives a
restart and works on a mounted bucket. The day's total is read from those files once per day and process, then
kept in memory.

A model with no known price would otherwise count as zero: it is counted at the dearest price in prices.yaml.
Calls already in flight when the cap is reached still finish, so the day can end a few calls above the cap.
"""
import json
import logging
import secrets
import threading
from datetime import UTC, datetime

from hup_data import paths, settings, store

log = logging.getLogger("hup_api.llm")

_lock = threading.Lock()
_state: dict = {"key": None, "spent": 0.0}


def _day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _total(day: str) -> float:
    folder = paths.llm_spend() / day
    total = 0.0
    if folder.is_dir():
        for file in folder.glob("*.json"):
            try:
                total += float(json.loads(file.read_text(encoding="utf-8")).get("cost_usd") or 0)
            except (OSError, ValueError, AttributeError):
                log.warning("llm budget: a spend file could not be read and counts as zero")
    return total


def _current() -> float:
    """Dollars spent today, read from disk the first time a day (or data folder) is seen. Holds _lock."""
    key = (str(paths.llm_spend()), _day())
    if _state["key"] != key:
        _state.update(key=key, spent=_total(key[1]))
    return _state["spent"]


def cap() -> float | None:
    return settings.load().llm_daily_budget_usd


def spent_today() -> float:
    with _lock:
        return _current()


def allow() -> bool:
    """False when the day's cap is set and reached."""
    limit = cap()
    if limit is None:
        return True
    with _lock:
        return _current() < limit


def spend(cost_usd: float | None, task: str, model: str) -> None:
    """Count one call. Does nothing without a cap or for a call that cost nothing. Never raises."""
    if cap() is None or not cost_usd:
        return
    try:
        with _lock:
            _current()
            _state["spent"] += cost_usd
            day = _state["key"][1]
        record = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                  "task": task, "model": model, "cost_usd": round(cost_usd, 6)}
        name = f"{datetime.now(UTC):%H%M%S%f}-{secrets.token_hex(4)}.json"
        store.write_atomic(paths.llm_spend() / day / name, json.dumps(record).encode("utf-8"))
    except Exception:  # noqa: BLE001 - the count in memory is kept; only the copy on disk is lost
        log.warning("llm budget: a spend file could not be written")
