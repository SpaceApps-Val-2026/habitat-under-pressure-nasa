"""Jev (TypeSafe), a typed-decision service: it answers typed questions about a `state`; it writes no text.

    ask(state, questions, timeout=4.0) -> {"answers": {id: answer}, "usage": {...}, "model": str}

Shapes as documented at https://docs.typesafe.ai/api/system-one.md (read on 14 November 2026):
- POST https://api.typesafe.ai/v1/systemone, header `Authorization: Bearer <key>`, JSON body
  {"model": "jev-latest", "state": <string, object or array>, "questions": {<id>: <question>}}.
- question: {"type": "noul", "instructions": str, "criteria": {"true": str, "false": str} | null}
            {"type": "choice", "instructions": str, "criteria": {<option>: <description>}}  (1 to 255 options)
            {"type": "score", "instructions": str, "criteria": [<level>, ...]}             (up to 10 levels)
- answer, under the same id: noul → {"noul": p}; choice → {"choice", "confidence", "probabilities"};
  score → {"score", "confidence", "legend", "probabilities"}. "usage": {"input_tokens", "output_tokens"}.
- 400 (with "detail"), 422 (validation), 429 (quota).
No real call has been made from this code: the tests use a fake transport. The key is TYPESAFE_API_KEY, read at the
moment of the call through hup_data.settings, and it never appears in a log, an error or an answer. The official
SDK (typesafe-sdk) is not a dependency; this is plain HTTP with httpx, already installed with the LLM SDKs.
"""
import logging

from hup_data import settings

log = logging.getLogger("hup_api.llm")

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
KEY = "TYPESAFE_API_KEY"
TIMEOUT = 4.0


class JevUnavailable(Exception):
    """No usable answer: no key, no network, a timeout, an error status or an answer of the wrong shape."""


def configured() -> bool:
    return bool(settings.load().secret(KEY))


def _transport(url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict | None]:
    # `requests` is a declared dependency of the workspace and is in the image; `httpx` is only in the dev
    # environment, and importing it here made every call fall back in the container without anyone noticing.
    import requests

    response = requests.post(url, headers=headers, json=body, timeout=timeout)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response.status_code, payload


def ask(state, questions: dict, timeout: float = TIMEOUT, transport=None) -> dict:
    """One request with every question. Raises JevUnavailable."""
    key = settings.load().secret(KEY)
    if not key:
        raise JevUnavailable("no key")
    if not questions:
        raise JevUnavailable("no questions")
    body = {"model": MODEL, "state": state, "questions": questions}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        status, payload = (transport or _transport)(URL, headers, body, timeout)
    except Exception as error:  # noqa: BLE001 - only the kind of failure is kept: a message could quote the key
        log.warning("jev: request failed (%s)", type(error).__name__)
        raise JevUnavailable(type(error).__name__) from None
    if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        log.warning("jev: status %s", status)
        raise JevUnavailable(f"status {status}")
    missing = [q for q in questions if not isinstance(payload["answers"].get(q), dict)]
    if missing:
        raise JevUnavailable(f"{len(missing)} questions without an answer")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    log.info("jev %s: %s input tokens, %d questions", payload.get("model"), usage.get("input_tokens"),
             len(questions))
    return {"answers": payload["answers"], "usage": usage, "model": payload.get("model")}


def noul(answer: dict) -> float | None:
    value = answer.get("noul") if isinstance(answer, dict) else None
    return float(value) if isinstance(value, int | float) and 0 <= value <= 1 else None


def probabilities(answer: dict) -> dict[str, float]:
    value = answer.get("probabilities") if isinstance(answer, dict) else None
    return {k: float(v) for k, v in value.items() if isinstance(v, int | float)} if isinstance(value, dict) else {}
