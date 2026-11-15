"""The whatif design (C26, 14 November): a place and catalog archetype(s) give one estimate per archetype with its
range and published reference, next to the state of the place. The person types no figure (AGENTS.md, 6 and 7):
the size is the one the question states (illustrative), else the archetype's typical size, else one unit of `per`;
the context variables and the period are decided by Delfos and said in `answer.decided`.

No network and no LLM. The archetypes are built here (catalog/archetypes.yaml is edited by the methods front), and
put into a copy of the real catalog; the context variable is chosen by what the catalog says it supports.
"""
import copy
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import contracts, executor, main
from hup_api.assistant import service, templates
from hup_api.executor import runner
from hup_api.library import folder
from hup_api.reviewer import review
from hup_api.writer import fill, llm
from hup_data import coverage, fingerprint, places

REAL = catalog_module.load()
WATER = {"id": "test-household-water", "name": {"en": "Household water use", "es": "Consumo doméstico de agua"},
         "resource": "water", "unit": "L/day", "per": "inhabitant", "low": 133, "central": 157, "high": 165,
         "ref": "INE, pp. 1-2 y 6, https://www.ine.es/prensa/essa_2020.pdf"}
LAND = {"id": "test-household-land", "name": {"en": "Household land", "es": "Suelo doméstico"},
        "resource": "land", "unit": "m2", "per": "inhabitant", "low": 20, "central": None, "high": 40,
        "ref": "Synthetic reference for tests, https://example.org/land"}
PENDING = {"id": "test-pending", "name": {"en": "Pending archetype", "es": "Arquetipo pendiente"},
           "resource": "energy", "unit": "MWh/year", "per": "inhabitant", "low": None, "central": None,
           "high": None, "ref": None}
TYPICAL = {**WATER, "id": "test-typical-water", "name": {"en": "Typical water", "es": "Agua típica"},
           "typical": {"low": 2000, "central": 5000, "high": 10000, "ref": "Synthetic size, https://example.org/size"}}
MW = {"id": "test-per-mw", "name": {"en": "Per MW", "es": "Por MW"}, "resource": "water", "unit": "m3/year",
      "per": "MW", "low": 1, "central": 2, "high": 3, "ref": "Synthetic, https://example.org/mw"}
CATALOG = {**copy.deepcopy(REAL), "archetypes": [WATER, LAND, PENDING, MW, TYPICAL]}
ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [10.0, 40.0, 10.1, 40.1]}
PERIOD = {"start": "2020-01-01", "end": "2024-12-31"}
CONTEXT = next(v["id"] for v in REAL["variables"] if "state" in v["designs"]
               and templates._period_limit("state", v, ZONE, PERIOD, {}, {})[0])


class Readers:
    """A monthly counter for any variable: a stand-in, not a measurement."""

    def read_series(self, variable, zone, period, **_):
        dates = pd.date_range(period["start"], period["end"], freq="MS")
        found = next(v for v in REAL["variables"] if v["id"] == variable)
        source = next(s for s in REAL["sources"] if s["id"] == found["sources"][0])
        table = pd.DataFrame({"t": [d.strftime("%Y-%m-%d") for d in dates], "v": [float(i) for i in range(len(dates))]})
        return {"data": table, "absence": None, "origin": "cache",
                "sources": [{"source": source["id"], "url": source["access"]["url"]}], "support": {},
                "coverage": {"valid_pct": 100.0, "n_dates": len(dates)}, "unit": found["unit"],
                "input_fingerprint": None}


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    monkeypatch.setattr(runner, "_readers", lambda: Readers())
    (tmp_path / "context.csv").write_text("t,v\n2024-01-01,0\n", encoding="utf-8")
    sources = {v["id"]: v["sources"][0] for v in REAL["variables"]}
    monkeypatch.setattr(coverage, "query", lambda variable, *_: [
        {"source": sources[variable], "path": "context.csv", "origin": "cache"},
    ] if variable in {CONTEXT, "precipitation", "soil_moisture"} else [])
    monkeypatch.setattr(catalog_module, "load", lambda: copy.deepcopy(CATALOG))
    monkeypatch.setattr(places, "resolve", lambda text, language="en": {"status": "found", "zone": copy.deepcopy(ZONE)}
                        if text.lower() == "test zone" else {"status": "not_found"})


@pytest.fixture
def client():
    return TestClient(main.app)


def build(archetypes=("test-household-water",), quantity=10000, variables=None):
    return templates.template("whatif", copy.deepcopy(ZONE), PERIOD, [CONTEXT] if variables is None else variables,
                              CATALOG, archetypes=list(archetypes), quantity=quantity)


def form(client, **changes):
    body = {"text": "", "language": "en", "design": "whatif", "place": "Test zone", "variables": [CONTEXT],
            "period": {"start": "2020", "end": "2024"}, "archetypes": ["test-household-water"], "quantity": 10000,
            **changes}
    return client.post("/api/plans", json={k: v for k, v in body.items() if v is not None})


def run(plan):
    analysis_id = executor.run(plan, catalog=CATALOG)
    deadline = time.monotonic() + 60
    while folder.read_analysis(analysis_id)["status"] not in ("finished", "failed"):
        assert time.monotonic() < deadline, "the analysis did not finish"
        time.sleep(0.05)
    analysis = folder.read_analysis(analysis_id)
    errors = [e for e in folder.read_events(analysis_id) if e["type"] == "error"]
    assert errors == [] and analysis["status"] == "finished"
    assert contracts.errors("analysis", analysis) == []
    results = folder.read_results(analysis_id)
    for result in results:
        assert contracts.errors("result", result) == [], result["id"]
    return analysis, results


def estimates(results):
    return [r for r in results if r["method"]["operation"] == "resource_estimate"]


def test_the_operation_is_registered_with_the_catalog_version():
    from hup_ops.registry import REGISTRY

    declared = next(o for o in REAL["operations"] if o["id"] == "resource_estimate")
    assert REGISTRY["resource_estimate"].version == declared["version"]
    assert declared["evidence_level"] == "E0"
    assert "stress_index" not in REGISTRY


def test_whatif_only_schedules_context_with_a_reader_and_local_data():
    plan = build(variables=[CONTEXT, "soil_moisture", "groundwater_level"])
    assert [s["params"]["variable"] for s in plan["steps"] if s["operation"] == "read_series"] == [CONTEXT]
    assert executor.validate_plan(plan, CATALOG) == []


def test_whatif_can_compute_an_estimate_without_local_context(monkeypatch):
    monkeypatch.setattr(coverage, "query", lambda *_: [])
    plan = build()
    assert not [s for s in plan["steps"] if s["type"] == "ingest"]
    assert executor.validate_plan(plan, CATALOG) == []
    assert not templates.assess(plan, CATALOG, offline=True)
    assert any(s["operation"] == "resource_estimate" for s in plan["steps"])


def test_the_form_with_only_place_and_archetype_gives_a_plan_decided_by_delfos(client):
    out = form(client, variables=None, period=None, quantity=None).json()
    assert out["kind"] == "plan", out
    plan = out["plan"]
    assert executor.validate_plan(plan, CATALOG) == [] and plan["design"] == "whatif"
    expected, rule = templates.context_variables(["water"], ZONE, CATALOG)
    assert rule == "layer" and all(v["layer"] == "water" for v in expected)
    assert set(plan["variables"]) <= {v["id"] for v in expected} and plan["variables"]
    fields = {d["field"] for d in plan["answer"]["decided"]}
    assert {"variables", "period", "size"} <= fields
    step, = [s for s in plan["steps"] if s["operation"] == "resource_estimate"]
    assert step["params"] == {"archetype": WATER} and step["depends_on"] == []
    assert form(client, variables=None, period=None, quantity=None, archetypes=None,
                archetype="test-household-water").json()["plan_fingerprint"] == out["plan_fingerprint"]


def test_ladder_per_unit_without_a_typical_size():
    analysis, results = run(build(quantity=None))
    estimate, = estimates(results)
    row = dict(zip(estimate["table"]["columns"], estimate["table"]["rows"][0], strict=True))
    assert (estimate["value"], row["low"], row["high"], row["basis"]) == (157, 133, 165, "per_unit")
    assert estimate["data_class"] == "approximated" and estimate["evidence_level"] == "E0"
    finding, = [f for f in analysis["findings"] if f["results"] == [estimate["id"]]]
    en = fill(finding["text"]["en"], results, "en")
    assert en.startswith("For each unit of reference, Household water use would ask for an estimated 157 L/day")
    assert "per inhabitant; range: 133 to 165 L/day; reference: INE" in en
    assert "por inhabitant; rango: de 133 a 165 L/día" in fill(finding["text"]["es"], results, "es")


def test_ladder_example_size_gives_the_coefficient_range_at_that_size_and_the_sizes_apart():
    """Guillem, 14 November (A9, «si a les recomanacions»): an example size, not a «typical» one; the range is the
    coefficients' at the central size, and the smallest and largest published sizes are said apart."""
    plan = build(archetypes=["test-typical-water"], quantity=None)
    assert any(d["field"] == "size" and "published example size" in d["text"]["en"] for d in plan["answer"]["decided"])
    analysis, results = run(plan)
    estimate, = estimates(results)
    row = dict(zip(estimate["table"]["columns"], estimate["table"]["rows"][0], strict=True))
    assert estimate["value"] == 5000 * 157 and (row["low"], row["high"]) == (5000 * 133, 5000 * 165)
    assert (row["quantity_low"], row["quantity_high"]) == (2000, 10000)
    assert row["basis"] == "typical" and estimate["data_class"] == "approximated"
    assert [s["source"] for s in estimate["sources"]] == ["test-typical-water", "test-typical-water-typical"]
    assert any("from 2000 to 10000 inhabitant" in limit["en"] for limit in estimate["limits"])
    finding, = [f for f in analysis["findings"] if f["results"] == [estimate["id"]]]
    en, es = (fill(finding["text"][lang], results, lang) for lang in ("en", "es"))
    assert en.startswith("For a published example size, Typical water would ask for an estimated 785,000 L/day")
    assert "published example size: 5,000 inhabitant, reference: Synthetic size" in en
    assert "range at that size: 665,000 to 825,000 L/day" in en
    assert "the published example sizes go from 2,000 to 10,000 inhabitant" in en
    assert es.startswith("Para un tamaño de ejemplo publicado")
    assert "tamaño típico" not in es and "instalación típica" not in es


def test_a_size_stated_in_the_question_is_used_and_labelled_as_an_assumption():
    analysis, results = run(build())
    estimate, = estimates(results)
    assert estimate["value"] == 10000 * 157
    row = dict(zip(estimate["table"]["columns"], estimate["table"]["rows"][0], strict=True))
    assert (row["low"], row["high"], row["quantity"], row["per"]) == (10000 * 133, 10000 * 165, 10000, "inhabitant")
    assert row["reference"] == WATER["ref"] and row["basis"] == "assumed"
    assert estimate["data_class"] == "illustrative" and estimate["evidence_level"] == "E0"
    assert estimate["sources"] == [{"source": WATER["id"], "url": "https://www.ine.es/prensa/essa_2020.pdf",
                                    "product": WATER["ref"]}]
    assert "period" not in estimate
    finding, = [f for f in analysis["findings"] if f["results"] == [estimate["id"]]]
    assert finding["review"]["status"] == "approved" and finding["data_class"] == "illustrative"
    en, es = (fill(finding["text"][lang], results, lang) for lang in ("en", "es"))
    assert en == ("With the quantity assumed in the question, Household water use would ask for an estimated "
                  "1,570,000 L/day, from published unit coefficients (assumed: 10,000 inhabitant; range: 1,330,000 "
                  "to 1,650,000 L/day; reference: INE, pp. 1-2 y 6, https://www.ine.es/prensa/essa_2020.pdf).")
    assert es == ("Con la cantidad supuesta en la pregunta, Consumo doméstico de agua pediría unos 1.570.000 L/día "
                  "estimados, a partir de coeficientes unitarios publicados (supuesto: 10.000 inhabitant; rango: de "
                  "1.330.000 a 1.650.000 L/día; referencia: INE, pp. 1-2 y 6, "
                  "https://www.ine.es/prensa/essa_2020.pdf).")
    blocks = {style: [b["block"] for b in story] for style, story in analysis["story"].items() if style != "editorial"}
    assert set(blocks) == {"journalistic", "institutional", "scientific"}
    allowed = {"KeyFigure", "TrendChart", "LimitsBox", "MethodBox", "SourcesTable", "EvidenceBadge"}
    assert all(set(names) <= allowed for names in blocks.values())
    assert any(b.get("finding") == finding["id"] for story in analysis["story"].values() for b in story)
    assert any(r["method"]["operation"] == "read_series" and r["variable"] == CONTEXT for r in results)


def test_whatif_title_uses_the_question_in_its_language_and_the_resource_elsewhere():
    plan = build()
    question = "¿Cuánta agua necesitarían los hogares?"
    plan["request"] = {"language": "es", "text": question, "origin": "assistant"}
    analysis, _ = run(plan)
    assert analysis["title"] == {"en": "Test zone: Household water use", "es": question}


def test_an_archetype_without_figures_is_not_computed_and_still_gives_a_story():
    plan = build(archetypes=["test-household-water", "test-pending"])
    assert any("Pending archetype" in limit["en"] and "no estimate" in limit["en"]
               for limit in plan["answer"]["limits"])
    analysis, results = run(plan)
    pending, = [r for r in estimates(results) if r["method"]["params"]["archetype"]["id"] == "test-pending"]
    assert pending["absence"]["reason"] == "not_computed"
    assert "reference" in pending["absence"]["detail"]["en"] and pending["absence"]["detail"]["es"]
    assert analysis["story"]["journalistic"]
    assert any(b["block"] == "LimitsBox" and pending["id"] in b["results"] for b in analysis["story"]["journalistic"])


def test_two_archetypes_give_two_estimates_and_a_missing_central_is_the_midpoint():
    _, results = run(build(archetypes=["test-household-land", "test-household-water"]))
    by_id = {r["method"]["params"]["archetype"]["id"]: r for r in estimates(results)}
    assert by_id["test-household-water"]["value"] == 10000 * 157
    assert by_id["test-household-land"]["value"] == 10000 * 30


@pytest.mark.parametrize("changes", [
    {"archetypes": ["made-up-archetype"]},
    {"archetypes": None},
    {"quantity": 0},
    {"quantity": -5},
    {"quantity": None},
    {"variables": None},
    {"archetypes": ["test-household-water", "test-per-mw"]},
    {"period": None},
    {"variables": None, "period": None, "quantity": None, "archetypes": None},
])
def test_nothing_but_the_place_is_ever_asked(client, changes):
    response = form(client, **changes)
    assert response.status_code == 200, response.text
    out = response.json()
    assert out["kind"] == "plan", out
    assert executor.validate_plan(out["plan"], CATALOG) == []


def test_without_an_archetype_the_answer_is_the_state_of_the_place_and_says_why(client):
    plan = form(client, archetypes=["made-up-archetype"], variables=None, quantity=None).json()["plan"]
    assert not [s for s in plan["steps"] if s["operation"] == "resource_estimate"]
    limits = " ".join(limit["en"] for limit in plan["answer"]["limits"])
    assert "made-up-archetype is not an archetype" in limits and "No archetype of the catalog covers" in limits
    assert plan["variables"]


def test_a_size_that_does_not_fit_is_left_out_with_a_limit(client):
    for changes in ({"quantity": -5}, {"archetypes": ["test-household-water", "test-per-mw"]}):
        plan = form(client, **changes).json()["plan"]
        assert all("quantity" not in s["params"] for s in plan["steps"] if s["operation"] == "resource_estimate")
        assert any("size stated in the question cannot be applied" in limit["en"]
                   for limit in plan["answer"]["limits"])


def test_a_quantity_that_is_not_a_number_is_invalid(client):
    assert form(client, quantity="a lot").status_code == 422


def test_the_fingerprint_changes_with_the_quantity():
    assert fingerprint.plan_fingerprint(build(quantity=10000)) != fingerprint.plan_fingerprint(build(quantity=20000))
    assert build(quantity=10000)["id"] != build(quantity=20000)["id"]
    assert build(quantity=10000) == build(quantity=10000)


def test_the_reviewer_needs_the_assumption_and_reference_and_rejects_literal_figures():
    _, results = run(build())
    estimate, = estimates(results)
    assert {"value", "unit", "table"} <= llm._required_fields(estimate)
    rid = estimate["id"]
    without = {"en": f"Household water use would ask for about {{{rid}.value}} {{{rid}.unit}}.",
               "es": f"Consumo doméstico de agua pediría unos {{{rid}.value}} {{{rid}.unit}}."}
    assert not llm._has_required_fields(without, {(rid, f) for f in llm._required_fields(estimate)})
    literal = {"id": "f-1", "order": 1, "results": [rid], "data_class": "illustrative", "evidence_level": "E0",
               "review": {"status": "pending"},
               "text": {"en": f"For 10000 inhabitants it would ask for {{{rid}.value}} {{{rid}.unit}}.",
                        "es": f"Para 10000 habitantes pediría {{{rid}.value}} {{{rid}.unit}}."}}
    checked = review(results, [literal], CATALOG)
    assert checked["findings"] == [] and checked["warnings"][-1]["code"] == "literal_figure"


class Recorded:
    def __init__(self, *answers):
        self.answers = list(answers)

    def __call__(self, task, data):
        call = {"task": task, "provider": "recorded", "model": "recorded", "input_tokens": 1, "output_tokens": 1,
                "cost_usd": None, "seconds": 0.0}
        return {"output": copy.deepcopy(self.answers.pop(0)), "call": call, "calls": [call]}


def answer(**changes):
    return {"kind": "plan", "missing": None, "design": "whatif", "place": "Test zone", "variables": [CONTEXT],
            "period": {"start": "2020", "end": "2024"}, "before": None, "after": None, "level": "with_limits",
            "archetypes": ["test-household-water"], "quantity": 10000, **changes}


def test_the_assistant_proposes_a_whatif_with_or_without_a_size(client, monkeypatch):
    schema = service.answer_schema(CATALOG)
    assert "whatif" in schema["properties"]["design"]["anyOf"][0]["enum"]
    assert schema["properties"]["archetypes"]["items"]["enum"] == [a["id"] for a in CATALOG["archetypes"]]
    monkeypatch.setattr(service, "llm_complete", Recorded(answer(), answer(quantity=None, variables=[], period=None)))
    monkeypatch.setenv("HUP_QUESTION_CACHE", "off")  # the same text twice, two model answers: no question cache
    text = {"text": "What would ten thousand people living in the test zone ask for?", "language": "en"}
    out = client.post("/api/plans", json=text).json()
    assert out["kind"] == "plan" and out["plan"]["design"] == "whatif"
    assert out["plan_fingerprint"] == form(client).json()["plan_fingerprint"]
    sized = client.post("/api/plans", json=text).json()
    assert sized["kind"] == "plan"
    step, = [s for s in sized["plan"]["steps"] if s["operation"] == "resource_estimate"]
    assert "quantity" not in step["params"]


def test_free_text_without_period_or_variables_gives_a_plan_not_a_question(client, monkeypatch):
    bare = answer(design=None, variables=[], period=None, archetypes=[], quantity=None, level="complete")
    monkeypatch.setattr(service, "llm_complete", Recorded(bare, {**bare, "kind": "clarification",
                                                                  "missing": "period"}))
    text = {"text": "What is changing in the test zone?", "language": "en"}
    for _ in range(2):
        out = client.post("/api/plans", json=text).json()
        assert out["kind"] == "plan", out
        plan = out["plan"]
        assert executor.validate_plan(plan, CATALOG) == [] and plan["design"] in ("scan", "state")
        assert {"design", "variables", "period"} <= {d["field"] for d in plan["answer"]["decided"]}
