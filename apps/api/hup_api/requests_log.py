"""Request log (B5): one JSON line per request in <data>/logs/requests.jsonl (interfaces.md, section 3).

What a line holds is closed: the FIELDS below and nothing else. There is no field for an IP address, a header, an
account or anything else that identifies a person, and record() takes only keyword arguments with those names, so
nothing can slip in by accident.

All writing goes through write_line(). On a bucket, where a file cannot be appended to without uploading it whole,
HUP_SPLIT_APPENDS=1 writes each line as a small file of its own, logs/requests/<time>-<random>.jsonl; the lines are
the same.
"""
import json
import logging
import secrets
import threading
from datetime import UTC, datetime

from hup_data import paths, settings, store

log = logging.getLogger("hup_api.requests_log")

FIELDS = ("ts", "kind", "origin", "language", "text", "place", "plan_fingerprint", "answer_level", "absences",
          "cached", "phases", "llm_calls", "analysis_id")
LLM_FIELDS = ("task", "provider", "model", "input_tokens", "output_tokens", "cost_usd", "seconds")

_lock = threading.Lock()


def write_line(line: str, *, stream: str = "requests") -> None:
    """Append one whole line, or write it as its own file in split mode. The only function that touches the disk."""
    if settings.load().split_appends:
        name = f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{secrets.token_hex(4)}.jsonl"
        store.write_atomic(paths.requests_log_parts().with_name(stream) / name, line.encode("utf-8"))
        return
    file = paths.requests_log().with_name(f"{stream}.jsonl")
    file.parent.mkdir(parents=True, exist_ok=True)
    with _lock, open(file, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)


def _text(value) -> str | None:
    return value if isinstance(value, str) else None


def _place(zone) -> dict | None:
    """The resolved place: id, name and bbox. Never its geometry."""
    if not isinstance(zone, dict):
        return None
    return {"id": _text(zone.get("id")), "name": _text(zone.get("name")),
            "bbox": zone["bbox"] if isinstance(zone.get("bbox"), list) else None}


def _llm_call(call) -> dict | None:
    return {k: call[k] for k in LLM_FIELDS if k in call} if isinstance(call, dict) else None


def plan_fields(plan) -> dict:
    """origin, language, text, place and answer_level read from a plan, each None when the plan does not say."""
    plan = plan if isinstance(plan, dict) else {}
    request = plan.get("request") if isinstance(plan.get("request"), dict) else {}
    answer = plan.get("answer") if isinstance(plan.get("answer"), dict) else {}
    return {"origin": _text(request.get("origin")), "language": _text(request.get("language")),
            "text": _text(request.get("text")), "place": plan.get("zone"), "answer_level": _text(answer.get("level"))}


def record(*, kind: str | None = None, origin: str | None = None, language: str | None = None,
           text: str | None = None, place: dict | None = None, plan_fingerprint: str | None = None,
           answer_level: str | None = None, absences: list[str] | None = None, cached: bool | None = None,
           phases: dict[str, float] | None = None, llm_calls: list[dict] | None = None,
           analysis_id: str | None = None) -> dict:
    """Write one line and return it. A value nobody knows is null. Never raises: a lost line never breaks a request.

    kind: "plan" (assistant), "validate" (form or edited plan) or "run". phases: seconds per phase.
    llm_calls: one dict per LLM call with task, provider, model, input_tokens, output_tokens, cost_usd, seconds.
    """
    line = {
        "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "kind": _text(kind), "origin": _text(origin), "language": _text(language), "text": _text(text),
        "place": _place(place), "plan_fingerprint": _text(plan_fingerprint), "answer_level": _text(answer_level),
        "absences": [str(a) for a in absences] if isinstance(absences, list) else None,
        "cached": cached if isinstance(cached, bool) else None,
        "phases": {str(k): round(float(v), 3) for k, v in phases.items()
                   if isinstance(v, int | float) and not isinstance(v, bool)} if isinstance(phases, dict) else None,
        "llm_calls": [c for c in map(_llm_call, llm_calls) if c is not None] if isinstance(llm_calls, list) else None,
        "analysis_id": _text(analysis_id),
    }
    try:
        write_line(json.dumps(line, ensure_ascii=False, allow_nan=False) + "\n")
    except Exception:  # noqa: BLE001 - the log is best effort; the message carries no request content
        log.warning("request log: a line could not be written")
    return line
