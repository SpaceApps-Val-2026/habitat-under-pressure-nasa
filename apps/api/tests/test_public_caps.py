"""Caps for a public URL with paid LLM calls (R3, R5): request log one file per line, daily LLM budget, analyses at
once and requests per client. Every cap is off unless its variable is set. Fake providers only: no LLM, no network.
"""
import copy
import json
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from hup_api import executor, limits, main, requests_log
from hup_api.llm import adapter, budget
from hup_api.llm.adapter import LLMUnavailable
from hup_data import paths

SCHEMA = {"type": "object", "required": ["answer"], "additionalProperties": False,
          "properties": {"answer": {"type": "string"}}}
INPUT = {"instructions": "Fixed.", "context": "Catalog.", "request": "The request.", "schema": SCHEMA, "name": "t"}
CAPS = ("HUP_LLM_DAILY_BUDGET_USD", "HUP_MAX_RUNNING_ANALYSES", "HUP_RATE_LIMIT_PER_MINUTE",
        "HUP_TRUST_FORWARDED_FOR", "HUP_SPLIT_APPENDS", "HUP_EVENTS_LOCAL_DIR")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    for name in CAPS:
        monkeypatch.delenv(name, raising=False)
    budget._state.update(key=None, spent=0.0)
    yield
    budget._state.update(key=None, spent=0.0)


@pytest.fixture
def provider(monkeypatch):
    """A configured provider whose SDK call is a fake: 1M input and 100k output tokens of a priced model."""
    monkeypatch.setenv("HUP_LLM_PROVIDER", "anthropic")
    for task in ("PLAN", "TEXT", "SMALL"):
        monkeypatch.setenv(f"HUP_LLM_MODEL_{task}", "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    calls = []

    def fake(key, model, data):
        calls.append(data.get("timeout"))
        return json.dumps({"answer": "ok"}), 1_000_000, 100_000

    monkeypatch.setitem(adapter.PROVIDERS, "anthropic", fake)
    return calls


# R3: request log ------------------------------------------------------------------------------------------------

def test_request_log_unset_appends_one_file():
    requests_log.record(kind="validate", text="a")
    requests_log.record(kind="validate", text="b")
    lines = paths.requests_log().read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["text"] for line in lines] == ["a", "b"]
    assert not paths.requests_log_parts().exists()


def test_request_log_split_writes_one_file_per_line(monkeypatch):
    monkeypatch.setenv("HUP_SPLIT_APPENDS", "1")
    threads = [threading.Thread(target=requests_log.record, kwargs={"kind": "run", "text": f"q{i}"})
               for i in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not paths.requests_log().exists()
    files = sorted(paths.requests_log_parts().glob("*.jsonl"))
    assert len(files) == 10
    texts = sorted(json.loads(f.read_text(encoding="utf-8"))["text"] for f in files)
    assert texts == sorted(f"q{i}" for i in range(10))


def test_a_log_that_cannot_be_written_never_breaks_the_request(monkeypatch):
    monkeypatch.setenv("HUP_SPLIT_APPENDS", "1")

    def broken(*args, **kwargs):
        raise OSError("bucket says no")

    monkeypatch.setattr(requests_log.store, "write_atomic", broken)
    assert requests_log.record(kind="run")["kind"] == "run"


# R5a: daily LLM budget ------------------------------------------------------------------------------------------

def test_without_a_budget_nothing_is_counted_or_written(provider):
    adapter.complete("plan", INPUT)
    assert not paths.llm_spend().exists()


def test_the_budget_stops_calls_once_spent_and_survives_a_restart(provider, monkeypatch):
    # haiku: 1M input at 0.10 + 100k output at 0.50 = 0.15 dollars a call
    monkeypatch.setenv("HUP_LLM_DAILY_BUDGET_USD", "0.25")
    adapter.complete("plan", INPUT)
    adapter.complete("write", INPUT)  # 0.15 < 0.25 before it: allowed, ends the day at 0.30
    assert budget.spent_today() == pytest.approx(0.30)
    with pytest.raises(LLMUnavailable, match="budget"):
        adapter.complete("plan", INPUT)
    assert len(provider) == 2  # no provider call once spent
    files = list(paths.llm_spend().glob("*/*.json"))
    assert len(files) == 2
    budget._state.update(key=None, spent=0.0)  # a new process reads the day from disk
    assert budget.spent_today() == pytest.approx(0.30)
    with pytest.raises(LLMUnavailable):
        adapter.complete("plan", INPUT)
    monkeypatch.setenv("HUP_LLM_DAILY_BUDGET_USD", "1")
    assert adapter.complete("plan", INPUT)["output"] == {"answer": "ok"}


def test_a_model_without_a_price_counts_at_the_dearest(provider, monkeypatch):
    monkeypatch.setenv("HUP_LLM_DAILY_BUDGET_USD", "100")
    monkeypatch.setenv("HUP_LLM_MODEL_PLAN", "a-model-with-no-price")
    result = adapter.complete("plan", INPUT)
    assert result["call"]["cost_usd"] is None  # the accounting never makes up a price
    dearest = max(adapter.prices().values(), key=lambda p: p["output"])
    assert budget.spent_today() == pytest.approx(dearest["input"] + dearest["output"] / 10)


def test_the_timeout_left_is_the_sdk_timeout(provider):
    adapter.complete("plan", {**INPUT, "timeout": 5.0})
    assert 0 < provider[0] <= 5.0


def test_a_spent_budget_turns_the_assistant_into_the_form(provider, monkeypatch):
    request = {"text": "Rain in Paiporta since 2000", "language": "en"}
    TestClient(main.app).post("/api/plans", json=request)
    assert provider  # with budget left the assistant does call the provider (the fake's answer is no plan)
    provider.clear()
    monkeypatch.setenv("HUP_LLM_DAILY_BUDGET_USD", "0")
    answer = TestClient(main.app).post("/api/plans", json=request)
    assert answer.status_code == 200
    assert answer.json()["kind"] in {"plan", "clarification"}
    assert provider == []


def test_a_spent_budget_leaves_the_writer_on_templates(provider, monkeypatch):
    from hup_api.writer import write_findings

    result = {"id": "r-s1-trend", "type": "value", "variable": "soil_density", "unit": "1", "value": 2,
              "data_class": "reanalysis", "evidence_level": "E1", "fingerprint": "a" * 64,
              "method": {"operation": "trend"}, "sources": [{"source": "test", "url": "urn:test"}]}
    plan = {"design": "trend", "zone": {"name": "Tromsø"}, "request": {"text": "x"}}
    catalog = {"variables": [{"id": "soil_density", "name": {"en": "Soil density", "es": "Densidad del suelo"}}],
               "blocks": []}
    monkeypatch.setenv("HUP_OFFLINE", "1")
    templated = write_findings(plan, [result], catalog)
    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setenv("HUP_LLM_DAILY_BUDGET_USD", "0")
    assert write_findings(plan, [result], catalog) == templated
    assert provider == []
    monkeypatch.setenv("HUP_LLM_DAILY_BUDGET_USD", "10")
    write_findings({**plan, "zone": {"name": "Tromsø, again"}}, [result], catalog)
    assert provider  # with budget left the writer does call the provider


# R5b: analyses at once and requests per client ------------------------------------------------------------------

EXAMPLES = paths.CONTRACTS_DIR / "examples"
CATALOG = json.loads((EXAMPLES / "catalog.json").read_text(encoding="utf-8"))
PLAN = json.loads((EXAMPLES / "plan.json").read_text(encoding="utf-8"))


def held_registry(gate):
    def fn(params, inputs, ctx):
        gate.wait(10)
        raise RuntimeError("stand-in that only waits")
    return {o["id"]: SimpleNamespace(id=o["id"], version=o["version"], fn=fn) for o in CATALOG["operations"]}


def test_analyses_at_once_are_capped(monkeypatch):
    monkeypatch.setattr(executor.runner, "WRITER", "hup_api_tests_no_writer")
    monkeypatch.setattr(executor.runner, "REVIEWER", "hup_api_tests_no_reviewer")
    monkeypatch.setenv("HUP_MAX_RUNNING_ANALYSES", "1")
    gate = threading.Event()
    registry = held_registry(gate)
    first = executor.run(copy.deepcopy(PLAN), catalog=CATALOG, registry=registry)
    try:
        with pytest.raises(executor.runner.Busy):
            executor.run(copy.deepcopy(PLAN), catalog=CATALOG, registry=registry)
        monkeypatch.setattr(executor.runner, "default_registry", lambda: registry)
        monkeypatch.setattr("hup_api.catalog.load", lambda *a, **k: CATALOG)
        answer = TestClient(main.app).post("/api/analyses", json={"plan": PLAN})
        assert answer.status_code == 429
        assert answer.json() == limits.BUSY and set(answer.json()["error"]["text"]) == {"en", "es"}
    finally:
        gate.set()
    for _ in range(500):
        if executor.runner.running() == 0:
            break
        threading.Event().wait(0.02)
    assert executor.runner.running() == 0
    assert folder_status(first) in ("finished", "failed")
    second = executor.run(copy.deepcopy(PLAN), catalog=CATALOG, registry=registry)  # room again
    assert folder_status(second) in ("finished", "failed")


def folder_status(analysis_id):
    from hup_api.library import folder

    for _ in range(500):
        analysis = folder.read_analysis(analysis_id)
        if analysis["status"] != "running":
            return analysis["status"]
        threading.Event().wait(0.02)
    return "running"


def test_requests_per_client_are_capped(monkeypatch):
    monkeypatch.setenv("HUP_RATE_LIMIT_PER_MINUTE", "2")
    client = TestClient(main.app)
    body = {"text": "", "language": "en"}
    codes = [client.post("/api/plans", json=body).status_code for _ in range(3)]
    assert codes[:2] != [429, 429] and codes[2] == 429
    refused = client.post("/api/plans", json=body)
    assert refused.json()["error"]["code"] == "rate_limited"
    assert set(refused.json()["error"]["text"]) == {"en", "es"} and refused.headers["retry-after"] == "60"
    assert client.post("/api/analyses", json={}).status_code != 429  # its own window
    assert client.get("/health").status_code == 200  # other routes are never limited
    assert client.post("/api/plans/validate", json={}).status_code != 429


def test_the_window_slides_and_clients_are_told_apart(monkeypatch):
    now = [0.0]
    limiter = limits.RateLimit(app=None, clock=lambda: now[0])
    key = ("1.2.3.4", "/api/plans")
    assert limiter.allowed(key, 2) and limiter.allowed(key, 2) and not limiter.allowed(key, 2)
    assert limiter.allowed(("5.6.7.8", "/api/plans"), 2)
    now[0] = 61.0
    assert limiter.allowed(key, 2)
    scope = {"headers": [(b"x-forwarded-for", b"9.9.9.9, 10.0.0.1")], "client": ("10.0.0.2", 1)}
    assert limiter.client(scope, trust_forwarded=True) == "9.9.9.9"
    assert limiter.client(scope, trust_forwarded=False) == "10.0.0.2"


def test_unset_caps_change_nothing():
    client = TestClient(main.app)
    assert all(client.post("/api/plans", json={"text": ""}).status_code != 429 for _ in range(30))
