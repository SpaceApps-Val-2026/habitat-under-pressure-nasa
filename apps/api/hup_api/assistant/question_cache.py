"""Do not pay twice for the same question: normalised question → what the model understood and planned.

    key(text, language, catalog, versions, place=None) -> str
    get(key) -> entry | None         put(key, entry) -> None

The key is the question normalised (lower case, no accents, no punctuation, single spaces), its language, a hash of
the catalog's content, the versions of the prompts and, when the person already chose a place, that zone's id. The
entry holds the understanding and the plan request the model gave (design, the resolved zone, variables, period,
windows, archetypes, size); a hit rebuilds the plan with the templates and makes no model call.

Only plans are stored. A clarification is never stored: it can come from something passing (an assistant that did
not answer, a place lookup without network). Different wordings of one question meet anyway in the plan cache
(library/plan_cache.py): same plan fingerprint, same finished analysis (`existing_analysis`).

One small file per question in <data>/question_cache/<2 hex>/<key>.json, written whole (store.write_atomic), never
appended: it survives a restart and works on a mounted bucket. The question text itself is not stored. Off with
HUP_QUESTION_CACHE=off (the real bench runs with it off, so it measures the model and not the cache).
"""
import hashlib
import json
import logging
import os
from functools import lru_cache

from hup_api.assistant.understand import norm
from hup_data import paths, settings, store

log = logging.getLogger("hup_api.assistant")

VERSION = "question-cache-1"


def enabled() -> bool:
    settings.load()  # a local .env first, as every other setting
    return os.environ.get("HUP_QUESTION_CACHE", "").strip().lower() not in ("off", "0", "false", "no")


def normalize(text: str | None) -> str:
    return norm(text)


@lru_cache(maxsize=8)
def _catalog_hash(serialized: str) -> str:
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def catalog_hash(catalog: dict) -> str:
    """A hash of the catalog's content: its version string alone does not change when an entry does."""
    return _catalog_hash(json.dumps(catalog, ensure_ascii=False, sort_keys=True, default=str))


def key(text: str, language: str, catalog: dict, versions: list[str], place=None) -> str:
    place_id = None
    if isinstance(place, dict):
        place_id = place.get("id") or json.dumps(place.get("bbox"))
    elif isinstance(place, str) and place.strip():
        place_id = "name:" + norm(place)
    parts = [VERSION, normalize(text), language or "", catalog_hash(catalog), *versions, place_id or ""]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()[:40]


def _path(cache_key: str):
    return paths.data_dir() / "question_cache" / cache_key[:2] / f"{cache_key}.json"


def get(cache_key: str) -> dict | None:
    if not enabled():
        return None
    try:
        entry = json.loads(_path(cache_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) and isinstance(entry.get("plan_request"), dict) else None


def put(cache_key: str, entry: dict) -> None:
    """Store a plan request. Never raises: a cache that cannot be written only costs a model call later."""
    if not enabled() or not isinstance(entry.get("plan_request"), dict):
        return
    try:
        data = json.dumps({"version": VERSION, **entry}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        store.write_atomic(_path(cache_key), data)
    except Exception:  # noqa: BLE001
        log.warning("question cache: an entry could not be written")
