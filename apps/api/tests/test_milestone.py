"""Public assistant regressions for questions about a milestone in a place."""
import pytest
from fastapi.testclient import TestClient

from hup_api import main
from hup_api.assistant import milestone, service, templates
from hup_api.catalog import load
from hup_api.writer import compose_fallback
from hup_data import coverage, places

DUBAI = {"id": "dubai", "name": "Dubai", "bbox": [55.1, 25.0, 55.4, 25.3]}
PAIPORTA = {"id": "paiporta", "name": "Paiporta", "bbox": [-0.44, 39.40, -0.40, 39.44]}


def test_milestone_title_can_use_a_gazetteer_alias_from_the_question():
    zone = {"name": "دبي, Dubái, Emiratos Árabes Unidos", "source": "OpenStreetMap (Nominatim), relation"}
    assert milestone.place_in_question("¿Cómo afecta la guerra del golfo con Irán a Dubai?", zone, "دبي") == "Dubái"


@pytest.fixture
def ask(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setenv("HUP_QUESTION_CACHE", "off")
    monkeypatch.setenv("HUP_PREFILTER", "lexical")
    monkeypatch.setattr(templates, "LAST_COMPLETE_YEAR", 2025)
    monkeypatch.setattr(places, "resolve", lambda text, _: {
        "status": "found", "zone": PAIPORTA if text == "Paiporta" else DUBAI,
    })
    monkeypatch.setattr(coverage, "query", lambda variable, *_: [
        {"source": "imerg-monthly", "origin": "package", "period": {"start": "2025-09-01", "end": "2025-09-30"}},
    ] if variable == "precipitation" else [])
    client = TestClient(main.app)

    def request(text, place, milestone, variables=()):
        output = {"kind": "not_possible", "missing": None, "design": None, "place": place,
                  "variables": list(variables), "period": None, "before": None, "after": None,
                  "level": "not_possible", "milestone": milestone}
        monkeypatch.setattr(service, "llm_complete", lambda *_: {"output": output, "call": {}})
        response = client.post("/api/plans", json={"text": text, "language": "es"})
        assert response.status_code == 200, response.text
        return response.json()

    return request


@pytest.mark.parametrize("text", [
    "¿Cómo afecta la guerra del golfo con Irán a Dubai?",
    "What effect has the war in the Gulf had on Dubai?",
    "Quin impacte té a Dubai la guerra amb l'Iran?",
])
def test_conflict_with_a_date_proposes_a_local_temporal_comparison(ask, text):
    reply = ask(text, "Dubai", {"date": "2022-03", "basis": "assumed"})
    assert reply["kind"] == "plan"
    plan = reply["plan"]
    assert plan["zone"] == DUBAI
    assert plan["answer"]["level"] == "with_limits"
    assert any("2022-03" in d["text"]["es"] and "supuesto" in d["text"]["es"]
               for d in plan["answer"]["decided"])
    assert all(step["operation"] != "zone_layer" and "landsat-c2-l2" not in step.get("sources", [])
               for step in plan["steps"])
    assert {s["params"]["baseline"]["end"] for s in plan["steps"] if s["operation"] == "anomaly"} == {"2021-12-31"}


def test_date_written_in_the_question_takes_precedence(ask):
    reply = ask("¿Qué ha pasado en Paiporta desde la DANA del 29 de octubre de 2024?", "Paiporta",
                {"date": "2023-06", "basis": "assumed"}, ["precipitation"])
    plan = reply["plan"]
    assert plan["zone"] == PAIPORTA
    assert plan["period"]["months"] == [9]
    assert any("2024-10-29" in d["text"]["es"] and "pregunta" in d["text"]["es"]
               for d in plan["answer"]["decided"])
    assert {s["params"]["baseline"]["end"] for s in plan["steps"] if s["operation"] == "anomaly"} == {"2023-12-31"}

    rainfall = {"id": "rain", "type": "series", "variable": "precipitation", "unit": "mm/month",
                "data_class": "measured", "sources": [{"source": "imerg-monthly"}],
                "series": [{"t": "2025", "v": 8.3}],
                "method": {"operation": "anomaly", "params": {"baseline": {"end": "2023-12-31"}}}}
    article = compose_fallback(plan, [rainfall], load())["editorial"]["journalistic"]
    assert "Tras «la DANA del 29 de octubre de 2024»" in article["headline"]["es"]
    assert "lluvia mensual por encima de la referencia en Paiporta" in article["headline"]["es"]
    assert "8,30 mm al mes" in article["lead"]["es"]
    assert "8.30 mm per month" in article["lead"]["en"]


def test_unknown_milestone_scans_and_explains_the_missing_date(ask):
    plan = ask("¿Qué ha cambiado en Paiporta desde el suceso de la Luciérnaga Azul?", "Paiporta",
               {"date": None, "basis": None})["plan"]
    assert plan["design"] == "scan"
    assert plan["answer"]["level"] == "with_limits"
    assert any("No se ha podido fechar" in d["text"]["es"] for d in plan["answer"]["decided"])
    assert not any(s["operation"] == "anomaly" for s in plan["steps"])
    article = compose_fallback(plan, [], load())["editorial"]["journalistic"]
    assert article["headline"]["es"] == (
        "Los datos no permiten afirmar que «el suceso de la Luciérnaga Azul» haya afectado a Paiporta"
    )


def test_provisional_hypotheses_flag_adds_only_separate_full_history(ask, monkeypatch):
    text = "¿Qué ha pasado en Paiporta desde la DANA del 29 de octubre de 2024?"
    monkeypatch.delenv("HUP_MILESTONE_HYPOTHESES", raising=False)
    off = ask(text, "Paiporta", None, ["precipitation"])["plan"]
    monkeypatch.setenv("HUP_MILESTONE_HYPOTHESES", "1")
    on = ask(text, "Paiporta", None, ["precipitation"])["plan"]
    assert off["period"] == on["period"]
    optional = [step for step in on["steps"] if step["id"].startswith("hypothesis-")]
    assert len(optional) == 4 and not any(step in off["steps"] for step in optional)
    assert {step["params"]["read_period"]["start"] for step in optional} == {"1998-01-01"}
    compute = next(step for step in on["steps"] if step["id"] == "weather-hypotheses")
    assert compute["depends_on"] == [step["id"] for step in optional]
    assert not any(compute["id"] in step["depends_on"] for step in on["steps"] if step["type"] == "compute")
