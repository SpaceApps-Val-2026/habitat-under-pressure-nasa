"""T2 of the hot-cell threshold: a variable whose catalog entry declares a `threshold` is read with it.

The read step copies the percentile and the base period of the catalog and reads the summer; the plan says so in
`decided`. Without the field nothing changes. Every expected value is read from the catalog; no LLM is called (the
planner's answer is recorded) and nothing is read.
"""
import copy

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import contracts, main
from hup_api.assistant import service, templates
from hup_api.executor import runner
from hup_data import places

from .test_assistant import Recorded, answer

CATALOG = catalog_module.load()
ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [-0.45, 39.10, -0.40, 39.15]}
WITH_THRESHOLD = [v for v in CATALOG["variables"] if v.get("threshold")]
QUESTIONS = (("¿Cuánta gente vive en celdas calientes en Alzira?", "es"),
             ("How many people live in hot cells in Alzira?", "en"))
ADDED = {"percentile", "base_period", "season"}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-real-key-0123456789")
    monkeypatch.setattr(places, "resolve", lambda text, language="en": {"status": "found", "zone": copy.deepcopy(ZONE)}
                        if text.lower() in ("test zone", "alzira") else {"status": "not_found"})


def _without_thresholds(catalog):
    stripped = copy.deepcopy(catalog)
    for variable in stripped["variables"]:
        variable.pop("threshold", None)
    return stripped


def _reads(plan, variable_id):
    return [s for s in plan["steps"] if s["operation"] == "read_series" and s["params"]["variable"] == variable_id]


def test_the_catalog_declares_a_threshold_on_a_variable_with_a_design():
    assert WITH_THRESHOLD and all(v["designs"] for v in WITH_THRESHOLD)


@pytest.mark.parametrize(("text", "language"), QUESTIONS)
@pytest.mark.parametrize("variable", WITH_THRESHOLD, ids=lambda v: v["id"])
def test_the_question_plans_the_read_with_the_catalog_threshold(variable, text, language, monkeypatch):
    design = variable["designs"][0]
    recorded = Recorded(answer(design=design, place="Alzira", variables=[variable["id"]], period=None))
    monkeypatch.setattr(service, "llm_complete", recorded)
    response = TestClient(main.app).post("/api/plans", json={"text": text, "language": language})
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["kind"] == "plan", out
    plan = out["plan"]
    assert contracts.errors("plan", plan) == [] and runner.validate_plan(plan, CATALOG) == []
    read, = _reads(plan, variable["id"])
    assert read["params"]["percentile"] == variable["threshold"]["percentile"]
    assert read["params"]["base_period"] == variable["threshold"]["base_period"]
    assert read["params"]["season"] == "summer"
    shown, = [d for d in plan["answer"]["decided"] if d["field"] == "threshold"]
    start, end = (variable["threshold"]["base_period"][k][:4] for k in ("start", "end"))
    for lang in ("en", "es"):
        assert f"{variable['threshold']['percentile']:g}" in shown["text"][lang]
        assert f"{start}–{end}" in shown["text"][lang] and variable["threshold"]["ref"] in shown["text"][lang]


@pytest.mark.parametrize("variable", WITH_THRESHOLD, ids=lambda v: v["id"])
def test_without_the_field_nothing_is_added(variable):
    design = variable["designs"][0]
    real = templates.template(design, copy.deepcopy(ZONE), None, [variable["id"]], CATALOG, offline=True)
    plain = templates.template(design, copy.deepcopy(ZONE), None, [variable["id"]], _without_thresholds(CATALOG),
                               offline=True)
    read, = _reads(plain, variable["id"])
    assert not ADDED & read["params"].keys()
    assert not [d for d in plain["answer"].get("decided") or [] if d["field"] == "threshold"]
    # The threshold only adds its three parameters: the rest of the plan is the same.
    for with_it, without in zip(real["steps"], plain["steps"], strict=True):
        assert {k: v for k, v in with_it["params"].items() if k not in ADDED} == without["params"]
        assert {k: v for k, v in with_it.items() if k != "params"} == {k: v for k, v in without.items()
                                                                     if k != "params"}


def test_the_plans_of_every_other_variable_stay_the_same():
    stripped = _without_thresholds(CATALOG)
    others = [v for v in CATALOG["variables"] if not v.get("threshold")]
    checked = 0
    for variable in others:
        for design in variable["designs"]:
            if design in ("impact", "event", "whatif"):
                continue  # they need dates the template does not decide here; their read params are the same code
            plans = [templates.template(design, copy.deepcopy(ZONE), None, [variable["id"]], catalog, offline=True)
                     for catalog in (CATALOG, stripped)]
            assert plans[0]["steps"] == plans[1]["steps"], (variable["id"], design)
            checked += 1
    assert checked
