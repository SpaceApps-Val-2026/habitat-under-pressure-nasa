"""One door to the LLM (architecture, section 5): complete(task, input) -> {"output", "call", "calls"}.

    input = {"instructions": str, "context": str, "request": str, "schema": <JSON Schema>, "name": str,
             "max_tokens"?: int, "timeout"?: float}

The provider comes from HUP_LLM_PROVIDER (openai, anthropic or none) and the model from HUP_LLM_MODEL_PLAN,
HUP_LLM_MODEL_TEXT or HUP_LLM_MODEL_SMALL, by task. The answer is structured output against `schema` and is
checked again here with jsonschema. A timeout per call, an optional shared timeout across attempts, one retry,
and every attempt accounted (tokens, cost,
seconds) even when it fails. With provider none, a missing model, a missing key or two failed attempts:
LLMUnavailable. Keys are read at the moment of the call and never appear in a log, an error or an answer.
With HUP_LLM_DAILY_BUDGET_USD set, no attempt starts once the day's spend reaches it (budget.py): LLMUnavailable.
The time left of the shared timeout is the SDK client's own timeout, so the provider call itself is cut.

The SDK calls follow the installed SDKs (anthropic 2.8.0: messages.create with output_config.format json_schema
and anthropic.transform_schema; openai 3.51.0: responses.create with text.format json_schema strict). No real
provider call has been made from this code yet: the tests use fakes of both SDK clients.
"""
import json
import logging
import time
from functools import cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from hup_api.llm import budget
from hup_data import settings

log = logging.getLogger("hup_api.llm")

TIMEOUT = 60.0
ATTEMPTS = 2  # one call and one retry
MAX_TOKENS = 4000
# Task → the setting with its model (docs/paquetes/interfaces.md, section 2; architecture, section 5).
TASKS = {"plan": "llm_model_plan", "write": "llm_model_text", "review": "llm_model_small", "library": "llm_model_small",
         "understand": "llm_model_small"}
KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}


class LLMUnavailable(Exception):
    """No LLM answer. `calls` holds the accounting of every attempt that was made, if any."""

    def __init__(self, reason: str, calls: list[dict] | None = None):
        super().__init__(reason)
        self.calls = calls or []


@cache
def prices() -> dict[str, dict]:
    """Model → {"input", "output"} in dollars per million tokens, from prices.yaml with its source."""
    data = yaml.safe_load((Path(__file__).parent / "prices.yaml").read_text(encoding="utf-8"))
    return data["models"]


def cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    """Dollars for one call, or None when the model has no known price (never a made-up zero)."""
    price = prices().get(model)
    if price is None or input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


# providers ------------------------------------------------------------------------------------------------------


def _anthropic(key: str, model: str, data: dict) -> tuple[str, int | None, int | None]:
    import anthropic

    client = anthropic.Anthropic(api_key=key, timeout=data.get("timeout", TIMEOUT), max_retries=0)
    message = client.messages.create(
        model=model,
        max_tokens=int(data.get("max_tokens") or MAX_TOKENS),
        # Fixed instructions and catalog first, and the cache breakpoint after them: the prefix is reused.
        system=[{"type": "text", "text": data["instructions"]},
                {"type": "text", "text": data["context"], "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": data["request"]}],
        output_config={"format": {"type": "json_schema", "schema": anthropic.transform_schema(data["schema"])}},
    )
    usage = message.usage
    read = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", None) or 0) \
        + (getattr(usage, "cache_creation_input_tokens", None) or 0)
    if message.stop_reason not in ("end_turn", "stop_sequence"):
        raise _Attempt(f"stopped: {message.stop_reason}", read, usage.output_tokens)
    text = "".join(block.text for block in message.content if getattr(block, "type", None) == "text")
    return text, read, usage.output_tokens


def _openai(key: str, model: str, data: dict) -> tuple[str, int | None, int | None]:
    import openai

    client = openai.OpenAI(api_key=key, timeout=data.get("timeout", TIMEOUT), max_retries=0)
    response = client.responses.create(
        model=model,
        instructions=f"{data['instructions']}\n\n{data['context']}",  # the stable prefix, cached by the provider
        input=data["request"],
        text={"format": {"type": "json_schema", "name": data["name"], "schema": data["schema"], "strict": True}},
        max_output_tokens=int(data.get("max_tokens") or MAX_TOKENS),
        store=False,
        **({"reasoning": {"effort": data["reasoning_effort"]}} if "reasoning_effort" in data else {}),
    )
    usage = response.usage
    if response.status not in (None, "completed"):
        raise _Attempt(f"status: {response.status}", usage.input_tokens, usage.output_tokens)
    return response.output_text, usage.input_tokens, usage.output_tokens


PROVIDERS = {"anthropic": _anthropic, "openai": _openai}


class _Attempt(Exception):
    """An attempt that got an answer it cannot use, with the tokens it spent."""

    def __init__(self, reason: str, input_tokens=None, output_tokens=None):
        super().__init__(reason)
        self.input_tokens, self.output_tokens = input_tokens, output_tokens


# the door -------------------------------------------------------------------------------------------------------


def _choose(task: str) -> tuple[str, str, str]:
    try:
        current = settings.load()
    except ValueError as error:
        raise LLMUnavailable("HUP_LLM_PROVIDER is not one of openai, anthropic or none") from error
    if current.llm_provider not in PROVIDERS:
        raise LLMUnavailable("no LLM provider is configured")
    if task not in TASKS:
        raise LLMUnavailable(f"unknown task {task!r}")
    model = getattr(current, TASKS[task])
    if not model:
        raise LLMUnavailable(f"no model is configured for the task {task!r}")
    key = current.secret(KEYS[current.llm_provider])
    if not key:
        raise LLMUnavailable(f"the key for {current.llm_provider} is not set")
    return current.llm_provider, model, key


def complete(task: str, input: dict) -> dict:
    """One structured answer: {"output": <object valid against input["schema"]>, "call": <last attempt>,
    "calls": [<every attempt>]}. Raises LLMUnavailable."""
    deadline = time.monotonic() + input["timeout"] if "timeout" in input else None
    provider, model, key = _choose(task)
    validator = Draft202012Validator(input["schema"])
    calls = []
    for attempt in range(1, ATTEMPTS + 1):
        if not budget.allow():  # the assistant then answers with the form, the writer with templates
            log.warning("llm %s %s %s: the daily LLM budget is spent; no call made", task, provider, model)
            raise LLMUnavailable("the daily LLM budget is spent", calls)
        started = time.monotonic()
        if deadline is None:
            # "attempt_timeout" cuts each attempt apart: a hung provider call is retried soon, not after TIMEOUT.
            payload = {**input, "timeout": input["attempt_timeout"]} if "attempt_timeout" in input else input
        else:
            remaining = deadline - started
            if remaining <= 0:
                raise LLMUnavailable("LLM deadline expired", calls)
            payload = {**input, "timeout": min(TIMEOUT, remaining)}
        input_tokens = output_tokens = None
        try:
            text, input_tokens, output_tokens = PROVIDERS[provider](key, model, payload)
            output = json.loads(text)
            problems = [e.message[:200] for e in validator.iter_errors(output)]
            if problems:
                raise _Attempt("the answer does not follow the schema: " + "; ".join(problems))
        except Exception as error:  # noqa: BLE001 - any SDK, network or parsing failure is one failed attempt
            if isinstance(error, _Attempt):
                input_tokens = error.input_tokens if error.input_tokens is not None else input_tokens
                output_tokens = error.output_tokens if error.output_tokens is not None else output_tokens
            calls.append(_call(task, provider, model, input_tokens, output_tokens, started))
            # Only the kind of failure is logged: an SDK message could quote a key or the request.
            log.warning("llm %s %s %s: attempt %d failed (%s)", task, provider, model, attempt, type(error).__name__)
            continue
        calls.append(_call(task, provider, model, input_tokens, output_tokens, started))
        log.info("llm %s %s %s: %s input and %s output tokens", task, provider, model, input_tokens, output_tokens)
        return {"output": output, "call": calls[-1], "calls": calls}
    raise LLMUnavailable(f"the {provider} model gave no usable answer after {ATTEMPTS} attempts", calls)


def budget_cost(model: str, input_tokens: int | None, output_tokens: int | None) -> float:
    """What a call counts against the daily budget: its cost, or at the dearest known price when the model has
    none. An attempt with no token count (no answer at all) counts as zero."""
    known = cost(model, input_tokens, output_tokens)
    if known is not None:
        return known
    dearest = max(prices().values(), key=lambda p: p["output"], default={"input": 0.0, "output": 0.0})
    return ((input_tokens or 0) * dearest["input"] + (output_tokens or 0) * dearest["output"]) / 1_000_000


def _call(task, provider, model, input_tokens, output_tokens, started) -> dict:
    budget.spend(budget_cost(model, input_tokens, output_tokens), task, model)
    return {"task": task, "provider": provider, "model": model, "input_tokens": input_tokens,
            "output_tokens": output_tokens, "cost_usd": cost(model, input_tokens, output_tokens),
            "seconds": round(time.monotonic() - started, 3)}
