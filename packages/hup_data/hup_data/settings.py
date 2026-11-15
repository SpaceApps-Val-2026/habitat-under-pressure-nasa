"""The only place that reads the environment. Names and defaults: docs/paquetes/interfaces.md, section 2."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _repo_root() -> Path:
    """The folder that holds docs/contratos: the checkout on a laptop, /app in the container."""
    explicit = os.environ.get("HUP_REPO_ROOT")
    if explicit:
        return Path(explicit).resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "contratos").is_dir():
            return parent
    return Path.cwd().resolve()


REPO_ROOT = _repo_root()
PROVIDERS = ("openai", "anthropic", "none")


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    offline: bool
    llm_provider: str
    llm_model_plan: str | None
    llm_model_text: str | None
    llm_model_small: str | None
    # For a data folder on an object store (a mounted Cloud Storage bucket). Unset, everything is as on a disk.
    events_local_dir: Path | None = None  # HUP_EVENTS_LOCAL_DIR: a running analysis keeps its events here
    split_appends: bool = False  # HUP_SPLIT_APPENDS=1: manifest and request log lines go one file each
    # Protection for a public URL. Unset: no cap.
    llm_daily_budget_usd: float | None = None  # HUP_LLM_DAILY_BUDGET_USD
    max_running_analyses: int | None = None  # HUP_MAX_RUNNING_ANALYSES
    rate_limit_per_minute: int | None = None  # HUP_RATE_LIMIT_PER_MINUTE, per client and route
    trust_forwarded_for: bool = False  # HUP_TRUST_FORWARDED_FOR=1: the client is the first X-Forwarded-For address

    def secret(self, name: str) -> str | None:
        """Read a key at the moment it is needed. Never log it, print it or return it to a client."""
        return os.environ.get(name) or None


def _number(name: str, kind):
    """An optional number from the environment: None when unset or empty. A wrong value stops the app at once,
    so a cap that was meant to be on is never silently off."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = kind(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, not {raw!r}") from None
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


def load() -> Settings:
    """Read the settings now. A local .env is loaded first and never overrides the real environment."""
    load_dotenv(REPO_ROOT / ".env", override=False)
    provider = os.environ.get("HUP_LLM_PROVIDER", "none").strip().lower() or "none"
    if provider not in PROVIDERS:
        raise ValueError(f"HUP_LLM_PROVIDER must be one of {PROVIDERS}, not {provider!r}")
    events_local_dir = os.environ.get("HUP_EVENTS_LOCAL_DIR", "").strip()
    # Default recommended model (decision prepared; can be overridden by env). No paid calls are made here.
    default_model = "gpt-6.1-terra"
    return Settings(
        data_dir=Path(os.environ.get("HUP_DATA_DIR") or REPO_ROOT / "data").resolve(),
        offline=os.environ.get("HUP_OFFLINE", "0").strip() == "1",
        llm_provider=provider,
        llm_model_plan=os.environ.get("HUP_LLM_MODEL_PLAN") or (default_model if provider != "none" else None),
        llm_model_text=os.environ.get("HUP_LLM_MODEL_TEXT") or (default_model if provider != "none" else None),
        llm_model_small=os.environ.get("HUP_LLM_MODEL_SMALL") or (default_model if provider != "none" else None),
        events_local_dir=Path(events_local_dir).resolve() if events_local_dir else None,
        split_appends=os.environ.get("HUP_SPLIT_APPENDS", "0").strip() == "1",
        llm_daily_budget_usd=_number("HUP_LLM_DAILY_BUDGET_USD", float),
        max_running_analyses=_number("HUP_MAX_RUNNING_ANALYSES", int),
        rate_limit_per_minute=_number("HUP_RATE_LIMIT_PER_MINUTE", int),
        trust_forwarded_for=os.environ.get("HUP_TRUST_FORWARDED_FOR", "0").strip() == "1",
    )
