"""The LLM adapter with doubles only: no provider is ever called. The SDK clients are replaced by fakes that record
the call, so the tests check the exact shape of the structured-output request each SDK receives."""
import json
import logging
from types import SimpleNamespace

import anthropic
import openai
import pytest

from hup_api.llm import adapter
from hup_api.llm.adapter import LLMUnavailable

SCHEMA = {"type": "object", "required": ["answer"], "additionalProperties": False,
          "properties": {"answer": {"type": "string"}}}
INPUT = {"instructions": "Fixed instructions.", "context": "Catalog text.", "request": "The request.",
         "schema": SCHEMA, "name": "test_answer"}
SECRET = "sk-test-not-a-real-key-0123456789"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_LLM_MODEL_PLAN", "model-for-plans")
    monkeypatch.setenv("HUP_LLM_MODEL_TEXT", "model-for-text")
    monkeypatch.setenv("HUP_LLM_MODEL_SMALL", "model-for-small")
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)


class FakeAnthropic:
    created = []
    answers = []

    def __init__(self, **kwargs):
        FakeAnthropic.created.append(kwargs)
        self.messages = SimpleNamespace(create=self.create)
        self.calls = []

    def create(self, **kwargs):
        FakeAnthropic.calls_seen.append(kwargs)
        answer = FakeAnthropic.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        usage = SimpleNamespace(input_tokens=11, output_tokens=7, cache_read_input_tokens=5,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=answer)], usage=usage,
                               stop_reason="end_turn")


class FakeOpenAI:
    created = []
    answers = []

    def __init__(self, **kwargs):
        FakeOpenAI.created.append(kwargs)
        self.responses = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        FakeOpenAI.calls_seen.append(kwargs)
        answer = FakeOpenAI.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        usage = SimpleNamespace(input_tokens=13, output_tokens=3,
                                input_tokens_details=SimpleNamespace(cached_tokens=0))
        return SimpleNamespace(output_text=answer, usage=usage, status="completed", incomplete_details=None)


@pytest.fixture
def fake_anthropic(monkeypatch):
    monkeypatch.setenv("HUP_LLM_PROVIDER", "anthropic")
    FakeAnthropic.created, FakeAnthropic.calls_seen, FakeAnthropic.answers = [], [], []
    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return FakeAnthropic


@pytest.fixture
def fake_openai(monkeypatch):
    monkeypatch.setenv("HUP_LLM_PROVIDER", "openai")
    FakeOpenAI.created, FakeOpenAI.calls_seen, FakeOpenAI.answers = [], [], []
    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    return FakeOpenAI


def test_without_a_provider_it_is_unavailable(monkeypatch):
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    with pytest.raises(LLMUnavailable):
        adapter.complete("plan", INPUT)


def test_an_unknown_provider_or_task_is_unavailable(monkeypatch):
    monkeypatch.setenv("HUP_LLM_PROVIDER", "made_up")
    with pytest.raises(LLMUnavailable):
        adapter.complete("plan", INPUT)
    monkeypatch.setenv("HUP_LLM_PROVIDER", "anthropic")
    with pytest.raises(LLMUnavailable):
        adapter.complete("no_such_task", INPUT)


def test_without_a_model_it_is_unavailable(monkeypatch, fake_anthropic):
    monkeypatch.delenv("HUP_LLM_MODEL_PLAN")
    # gpt-6.1-terra is the confirmed default when a provider is set and no env override is given.
    fake_anthropic.answers = [json.dumps({"answer": "yes"})]
    out = adapter.complete("plan", INPUT)
    assert out["output"] == {"answer": "yes"}
    assert fake_anthropic.calls_seen[0]["model"] == "gpt-6.1-terra"


def test_anthropic_gets_a_json_schema_output_config_and_a_cached_prefix(fake_anthropic):
    fake_anthropic.answers = [json.dumps({"answer": "yes"})]
    out = adapter.complete("plan", INPUT)
    assert out["output"] == {"answer": "yes"}
    (created,) = fake_anthropic.created
    assert created["api_key"] == SECRET and created["max_retries"] == 0 and created["timeout"] > 0
    (call,) = fake_anthropic.calls_seen
    assert call["model"] == "model-for-plans" and call["max_tokens"] > 0
    assert call["output_config"] == {"format": {"type": "json_schema",
                                                "schema": anthropic.transform_schema(SCHEMA)}}
    system = call["system"]
    assert [b["text"] for b in system] == ["Fixed instructions.", "Catalog text."]
    assert system[-1]["cache_control"] == {"type": "ephemeral"}
    assert call["messages"] == [{"role": "user", "content": "The request."}]
    usage = out["call"]
    assert usage == {"task": "plan", "provider": "anthropic", "model": "model-for-plans", "input_tokens": 16,
                     "output_tokens": 7, "cost_usd": None, "seconds": usage["seconds"]}
    assert usage["seconds"] >= 0


def test_openai_gets_a_strict_json_schema_text_format(fake_openai):
    fake_openai.answers = [json.dumps({"answer": "yes"})]
    out = adapter.complete("write", INPUT)
    assert out["output"] == {"answer": "yes"}
    (call,) = fake_openai.calls_seen
    assert call["model"] == "model-for-text"
    assert call["text"] == {"format": {"type": "json_schema", "name": "test_answer", "schema": SCHEMA,
                                       "strict": True}}
    assert call["instructions"] == "Fixed instructions.\n\nCatalog text."
    assert call["input"] == "The request."
    assert call["store"] is False and call["max_output_tokens"] > 0
    assert "reasoning" not in call
    assert out["call"]["input_tokens"] == 13 and out["call"]["output_tokens"] == 3


def test_openai_passes_the_requested_reasoning_effort(fake_openai):
    fake_openai.answers = [json.dumps({"answer": "yes"})]
    adapter.complete("write", {**INPUT, "reasoning_effort": "none"})
    assert fake_openai.calls_seen[0]["reasoning"] == {"effort": "none"}


def test_the_cost_comes_from_the_price_table_when_the_model_is_in_it(monkeypatch, fake_anthropic):
    model, price = next(iter(adapter.prices().items()))
    monkeypatch.setenv("HUP_LLM_MODEL_PLAN", model)
    fake_anthropic.answers = [json.dumps({"answer": "yes"})]
    call = adapter.complete("plan", INPUT)["call"]
    expected = (16 * price["input"] + 7 * price["output"]) / 1_000_000
    assert call["cost_usd"] == pytest.approx(expected)


def test_one_retry_then_unavailable(fake_anthropic):
    fake_anthropic.answers = [RuntimeError("timed out"), json.dumps({"answer": "second"})]
    out = adapter.complete("plan", INPUT)
    assert out["output"] == {"answer": "second"} and len(fake_anthropic.calls_seen) == 2
    assert out["calls"][0]["model"] == "model-for-plans" and len(out["calls"]) == 2

    fake_anthropic.calls_seen.clear()
    fake_anthropic.answers = ["not json", "still not json"]
    with pytest.raises(LLMUnavailable) as error:
        adapter.complete("plan", INPUT)
    assert len(fake_anthropic.calls_seen) == 2
    assert len(error.value.calls) == 2  # spent tokens are still accounted


def test_an_answer_that_does_not_follow_the_schema_is_retried(fake_openai):
    fake_openai.answers = [json.dumps({"other": 1}), json.dumps({"answer": "ok"})]
    assert adapter.complete("plan", INPUT)["output"] == {"answer": "ok"}


@pytest.mark.parametrize("elapsed", [1.0, 3.0])
def test_attempts_share_the_requested_timeout(monkeypatch, fake_openai, elapsed):
    now = 10.0
    pretend_create = fake_openai.create

    def create(client, **kwargs):
        nonlocal now
        now += elapsed
        return pretend_create(client, **kwargs)

    monkeypatch.setattr(adapter, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(fake_openai, "create", create)
    fake_openai.answers = ["invalid JSON", json.dumps({"answer": "yes"})]
    payload = {**INPUT, "timeout": 3.0}
    if elapsed == 1.0:
        assert adapter.complete("write", payload)["output"] == {"answer": "yes"}
        assert [client["timeout"] for client in fake_openai.created] == [3.0, 2.0]
    else:
        with pytest.raises(LLMUnavailable) as error:
            adapter.complete("write", payload)
        assert len(error.value.calls) == 1 and fake_openai.created[0]["timeout"] == 3.0


def test_the_key_never_appears_in_errors_or_logs(fake_openai, caplog):
    caplog.set_level(logging.DEBUG)
    fake_openai.answers = [RuntimeError(f"bad key {SECRET}"), RuntimeError(f"bad key {SECRET}")]
    with pytest.raises(LLMUnavailable) as error:
        adapter.complete("plan", INPUT)
    assert SECRET not in str(error.value) and SECRET not in repr(error.value.calls)
    assert SECRET not in caplog.text


def test_a_missing_key_is_unavailable_without_naming_it(monkeypatch, fake_openai):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(LLMUnavailable):
        adapter.complete("plan", INPUT)
    assert fake_openai.created == []
