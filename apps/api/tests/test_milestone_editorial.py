"""Context survives planning, editorial review and deterministic publication."""
import json

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog, main
from hup_api.assistant import milestone, service
from hup_api.reviewer.text import validate_text
from hup_api.writer import compose, compose_fallback, llm, write_findings
from hup_data import places


@pytest.fixture(autouse=True)
def local_store(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_QUESTION_CACHE", "off")
    monkeypatch.setenv("HUP_PREFILTER", "lexical")
    monkeypatch.setenv("HUP_OFFLINE", "0")


@pytest.mark.parametrize(("place", "text", "language", "suggested"), [
    ("Járkov", "¿Qué ha cambiado en Járkov desde la guerra de Ucrania?", "es", None),
    ("Kiev", "¿Qué ha cambiado en Kiev desde que empezó la guerra de Ucrania?", "es", "2022-02-24"),
    ("Odesa", "What has changed in Odesa since the war in Ukraine began?", "en", "2014-02"),
    ("Kíiv", "Què ha canviat a Kíiv des de la guerra a Ucraïna?", "es", "2014"),
])
def test_the_documented_event_phase_is_independent_of_language_and_place(monkeypatch, place, text, language, suggested):
    event = {"en": "War in Ukraine", "es": "Guerra de Ucrania"}
    zone = {"id": "zone", "name": place, "bbox": [30.0, 50.0, 30.1, 50.1]}
    monkeypatch.setattr(places, "resolve", lambda *_: {"status": "found", "zone": zone})

    def recorded(_, data):
        assert data["name"] == "plan_fields"
        return {"output": {"kind": "plan", "missing": None, "design": "scan", "place": place,
                           "variables": ["air_temperature"], "period": None, "before": None, "after": None,
                           "level": "with_limits", "milestone": {
                               "event": event if place != "Kíiv" else None,
                               "date": suggested, "basis": "assumed" if suggested else None}},
                "call": {}}

    monkeypatch.setattr(service, "llm_complete", recorded)
    response = TestClient(main.app).post("/api/plans", json={"text": text, "language": language})
    plan = response.json()["plan"]
    decisions = milestone.decisions_of(plan)
    assert "la invasión rusa a gran escala de Ucrania" == decisions["event"]["es"]
    assert "2022-02-24" in decisions["milestone"]["es"]
    assert "supuesto" in decisions["milestone"]["es"] and "un.org" in decisions["milestone_source"]["en"]
    assert any(s["operation"] == "anomaly" for s in plan["steps"])


def test_an_explicit_start_survives_the_default_event_phase():
    text = "¿Qué ha cambiado en Odesa desde la guerra de Ucrania en 2014?"
    assert milestone.event_reference(text, None) is None
    assert milestone.dating(text, None) == ("2014", "stated")


@pytest.mark.parametrize(("name", "expected"), [
    ("Guerra del Golfo con Irán", "la guerra del Golfo con Irán"),
    ("Invasión rusa de Ucrania", "la invasión rusa de Ucrania"),
    ("DANA de 2024", "la DANA de 2024"),
    ("La DANA de 2024", "la DANA de 2024"),
])
def test_event_articles_are_normalized_in_sentence(name, expected):
    assert milestone.event_display({"en": "the event", "es": name})["es"] == expected


def test_l2_uses_named_event_and_keeps_the_observed_evidence(monkeypatch):
    monkeypatch.setenv("HUP_LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    period = {"start": "2024-01-01", "end": "2025-12-31"}
    plan = {"design": "scan", "zone": {"name": "دبي, Dubái, Emiratos Árabes Unidos",
                                      "source": "OpenStreetMap (Nominatim), ODbL"},
            "request": {"text": "¿Cómo afecta la guerra del Golfo a Dubai?", "language": "es"}, "period": period,
            "answer": {"level": "with_limits", "limits": [milestone.UNATTRIBUTED], "decided": [
                {"field": "milestone", "text": milestone.UNLOCATED},
                {"field": "event", "text": {"en": "the Gulf war", "es": "la guerra del Golfo"}},
            ]}}
    sources = [{"source": "imerg-monthly"}]
    results = [
        {"id": "r-scan", "type": "table", "variable": "precipitation", "method": {"operation": "scan_trends"},
         "table": {"columns": ["direction_assertable"], "rows": [[False]]}, "evidence_level": "E0",
         "data_class": "measured", "sources": sources},
        {"id": "r-series", "type": "series", "variable": "precipitation", "unit": "mm/month",
         "method": {"operation": "read_series"}, "data_class": "measured", "sources": sources, "period": period,
         "series": [{"t": "2024", "v": 5.1}, {"t": "2025", "v": 6.1}]},
    ]
    current = catalog.load()
    pair = {"en": "Rainfall provides local context.", "es": "La lluvia aporta contexto local."}
    headline = {"en": "The data cannot establish whether the Gulf war affected Dubái",
                "es": "Los datos no permiten afirmar que la guerra del Golfo haya afectado a Dubái"}
    calls = []

    def recorded(payload, _):
        context = json.loads(payload["request"])["milestone"]
        assert context["place"] == "Dubái"
        assert "2024 a 2025" in context["observations"]["es"]
        calls.append(context)
        return {"output": {"findings": [], "editorial": {"journalistic": {
            "headline": headline, "lead": pair, "meaning": pair, "cannot_say": pair,
            "results": ["r-scan"], "blocks": ["EvidenceBadge", "ScanMatrix"],
        }}}, "calls": []}

    monkeypatch.setattr(llm, "_submit", recorded)
    findings = write_findings(plan, results, current)
    piece = compose(plan, results, findings, current)["editorial"]["journalistic"]
    assert len(calls) == 1
    assert piece["headline"] == headline
    assert "La lluvia aporta contexto local." in piece["lead"]["es"]
    assert "2024 a 2025" in piece["lead"]["es"]
    assert "no permiten atribuir" in piece["cannot_say"]["es"]
    fallback = compose_fallback(plan, results, current)["editorial"]["journalistic"]
    assert fallback["headline"]["es"] == headline["es"]


@pytest.mark.parametrize(("line", "error"), [
    ("Los datos no permiten afirmar que el suceso provocó el cambio.", None),
    ("El suceso provocó el cambio.", "causal_language"),
    ("Los datos no permiten afirmar que hubo un cambio, pero el suceso lo provocó.", "causal_language"),
    ("Los datos no permiten afirmar que A causó B. El suceso provocó el cambio.", "causal_language"),
    ("Los datos no permiten afirmar que el suceso haya afectado al lugar.", None),
    ("El suceso afectó al lugar.", "causal_language"),
])
def test_review_distinguishes_attribution_from_an_explicit_limit(line, error):
    assert validate_text({"text": {"es": line}}, [], strict_attribution=True) == error


@pytest.mark.parametrize(("question", "model_design", "model_place", "kind", "design"), [
    ("Proyecta el calor en Sagunt a 2050 con y sin la fábrica.", None, "Sagunt", "plan", "scenario"),
    ("¿La fábrica de Sagunt ha subido la temperatura desde 2013?", "trend", "Sagunt", "plan", "trend"),
    ("¿Ha aumentado la temperatura de superficie en Sagunt desde que está la fábrica, desde 2013?",
     "scan", "Sagunt", "plan", "trend"),
    ("asdf qwer zxcv", None, None, "clarification", None),
])
def test_planning_replays_the_cases_reported_by_the_real_bench(
    monkeypatch, question, model_design, model_place, kind, design,
):
    zone = {"id": "sagunt", "name": "Sagunt", "bbox": [-0.32, 39.66, -0.23, 39.71]}
    monkeypatch.setattr(places, "resolve", lambda *_: {"status": "found", "zone": zone})
    output = {"kind": "plan" if model_design else "not_possible", "missing": None, "design": model_design,
              "place": model_place, "variables": ["lst"] if model_design else [], "period": None,
              "before": None, "after": None, "level": "with_limits"}
    monkeypatch.setattr(service, "llm_complete", lambda *_: {"output": output, "call": {}})
    response = TestClient(main.app).post("/api/plans", json={"text": question, "language": "es"}).json()
    assert response["kind"] == kind
    if design:
        plan = response["plan"]
        assert plan["design"] == design
        if design == "trend":
            assert plan["variables"] == ["lst"]
            assert any("the date of the change" in item["en"] for item in plan["answer"]["limits"])
