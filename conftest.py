"""Shared test setup: no test ever sees this computer's .env.

A developer's .env holds real API keys and a real provider choice. Without this, a test could behave differently
on each computer and, worse, make a paid call. Every test starts with no provider and empty keys; a test that
needs a provider sets it itself and uses a stand-in instead of the network.
"""
import pytest

BLANK = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TYPESAFE_API_KEY", "DISCORD_WEBHOOK_AGENTE",
         "HUP_LLM_MODEL_PLAN", "HUP_LLM_MODEL_TEXT", "HUP_LLM_MODEL_SMALL")


@pytest.fixture(autouse=True)
def no_local_env(monkeypatch):
    # Set, not deleted: a variable that exists is never overwritten when the code loads .env.
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    monkeypatch.setenv("HUP_RECOVERY_QUIET_SECONDS", "0")  # a test start closes what it finds at once
    # And the code never loads .env during a test, so a variable a test deletes stays deleted.
    monkeypatch.setattr("hup_data.settings.load_dotenv", lambda *args, **kwargs: False)
    for name in BLANK:
        monkeypatch.setenv(name, "")
