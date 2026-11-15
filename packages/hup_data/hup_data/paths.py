"""Where everything lives. Layout: docs/paquetes/interfaces.md, sections 1 and 3."""
from pathlib import Path

from hup_data import settings

REPO_ROOT = settings.REPO_ROOT
CONTRACTS_DIR = REPO_ROOT / "docs" / "contratos"
CATALOG_DIR = REPO_ROOT / "catalog"
LOCALES_DIR = REPO_ROOT / "locales"


def data_dir() -> Path:
    return settings.load().data_dir


def manifest() -> Path:
    return data_dir() / "manifest.jsonl"


def packages() -> Path:
    return data_dir() / "packages"


def cache() -> Path:
    return data_dir() / "cache"


def ready() -> Path:
    return data_dir() / "ready"


def derived() -> Path:
    return data_dir() / "derived"


def places() -> Path:
    return data_dir() / "places"


def analyses() -> Path:
    return data_dir() / "analyses"


def analysis(analysis_id: str) -> Path:
    return analyses() / analysis_id


def requests_log() -> Path:
    return data_dir() / "logs" / "requests.jsonl"


# With HUP_SPLIT_APPENDS=1 (a data folder on an object store) a new line is a small file of its own here.


def manifest_parts() -> Path:
    return data_dir() / "manifest.d"


def requests_log_parts() -> Path:
    return data_dir() / "logs" / "requests"


def llm_spend() -> Path:
    """Spend on LLM calls: one folder per UTC day, one small file per call (HUP_LLM_DAILY_BUDGET_USD)."""
    return data_dir() / "llm_spend"
