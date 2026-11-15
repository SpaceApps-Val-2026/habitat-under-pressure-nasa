from hup_data import paths, settings


def test_defaults(monkeypatch):
    for name in ("HUP_DATA_DIR", "HUP_OFFLINE", "HUP_LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings, "load_dotenv", lambda *a, **k: False)
    s = settings.load()
    assert s.data_dir == (settings.REPO_ROOT / "data").resolve()
    assert s.offline is False
    assert s.llm_provider == "none"


def test_environment_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    assert settings.load().offline is True
    assert paths.manifest() == tmp_path.resolve() / "manifest.jsonl"
    assert paths.analysis("a1") == tmp_path.resolve() / "analyses" / "a1"


def test_unknown_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("HUP_LLM_PROVIDER", "somebody-else")
    try:
        settings.load()
    except ValueError as error:
        assert "HUP_LLM_PROVIDER" in str(error)
    else:
        raise AssertionError("an unknown provider must be rejected")


def test_contracts_are_where_the_code_expects():
    assert (paths.CONTRACTS_DIR / "plan.schema.json").is_file()
