import json
from pathlib import Path

import pytest

from hup_api import contracts
from hup_api.llm import adapter
from hup_api.reviewer import review
from hup_api.writer import STYLES, compose, fill, llm, write_findings

ANALYSIS_EXAMPLE = Path("docs/contratos/examples/analysis.json")

RESULT = {
    "id": "r-trend", "type": "value", "variable": "air_temperature",
    "value": 0.0272, "unit": "°C/year", "data_class": "reanalysis", "evidence_level": "E1",
    "method": {"operation": "trend"}, "sources": [{"source": "power"}],
    "interval": {"low": 0.01324, "high": 0.0426, "level": 0.95},
    "period": {"start": "1981-01-01", "end": "2025-12-31"},
    "limits": [{"en": "The grid is wider than Torrent.", "es": "La celda es más grande que Torrent."}],
}
PLAN = {
    "design": "trend", "zone": {"name": "Torrent"},
    "request": {"language": "es", "text": "¿Se calienta Torrent?"},
    "answer": {"level": "with_limits", "limits": RESULT["limits"],
               "decided": [{"field": "period", "text": {"en": "Full record", "es": "Serie completa"}}]},
}
CATALOG = {
    "sources": [{"id": "power", "name": {"en": "NASA POWER"}}],
    "blocks": [
        {"id": name, "needs": needs, "styles": list(STYLES)}
        for name, needs in (
            ("KeyFigure", {"result_types": ["value"], "fields": ["interval"]}),
            ("EvidenceBadge", {}), ("LimitsBox", {}), ("SourcesTable", {}),
        )
    ],
}


def recorded_answer():
    text = {
        "en": "Air temperature rises {r-trend.value} {r-trend.unit}, {r-trend.period} ({r-trend.interval}).",
        "es": "La temperatura del aire aumenta {r-trend.value} {r-trend.unit}, {r-trend.period} ({r-trend.interval}).",
    }
    editorial = {
        "headline": {
            "en": "Air temperature rises around Torrent", "es": "Sube la temperatura del aire en torno a Torrent",
        },
        "lead": text,
        "meaning": {
            "en": "The estimate describes the wider area.", "es": "La estimación describe una zona más amplia.",
        },
        "cannot_say": {"en": "The data cannot distinguish streets.", "es": "Los datos no distinguen entre calles."},
        "results": [RESULT["id"]], "blocks": ["EvidenceBadge", "KeyFigure"],
    }
    return {
        "output": {
            "findings": [{"text": text, "results": [RESULT["id"]], "evidence_level": "E1"}],
            "editorial": {"journalistic": editorial},
        },
        "calls": [],
    }


@pytest.mark.parametrize(("direction", "headline"), [
    (True, "El escaneo detecta cambios con una dirección clara"),
    (False, "Sin una dirección clara en los indicadores evaluados"),
    (None, "Registros insuficientes para evaluar temperatura del aire en Torrent"),
])
def test_scan_has_a_journalistic_opening_without_a_model(monkeypatch, direction, headline):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    primary = {
        "id": "r-scan", "type": "table", "variable": RESULT["variable"], "data_class": "reanalysis",
        "evidence_level": "E1", "method": {"operation": "scan_trends"}, "sources": RESULT["sources"],
    }
    if direction is None:
        primary["absence"] = {"reason": "insufficient_qa", "detail": {"en": "No suitable series."}}
    else:
        primary["table"] = {"columns": ["direction_assertable"], "rows": [[direction]]}
    plan = {**PLAN, "design": "scan"}
    catalog = {**CATALOG, "blocks": CATALOG["blocks"] + [
        {"id": "ScanMatrix", "styles": list(STYLES), "needs": {"result_types": ["table"], "fields": ["table"]}},
    ]}
    story = compose(plan, [primary], [], catalog)
    piece = story["editorial"]["journalistic"]
    assert piece["headline"]["es"] == headline and piece["headline"]["en"]
    assert piece["results"] == ["r-scan"]
    assert ("featured" in piece) == (direction is not None)
    assert any(b["block"] == "LimitsBox" for b in story["journalistic"]) == (direction is None)


@pytest.mark.parametrize("outcome", ["accepted", "timeout", "rejected"])
def test_general_scan_gets_a_bounded_opening_from_its_evidence(monkeypatch, tmp_path, outcome):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HUP_LLM_MODEL_TEXT", "editorial-test")
    plan = {**PLAN, "design": "scan"}
    table = {**RESULT, "id": "r-s1-scan", "type": "table", "method": {"operation": "scan_trends"},
             "table": {"columns": ["variable", "slope", "p_adjusted", "direction_assertable"],
                       "rows": [["air_temperature", RESULT["value"], 0.03, True],
                                ["precipitation", None, 0.7, False]]}}
    trend = {**RESULT, "id": "r-s1-trend-0", "method": {"operation": "scan_trends"}, "direction_assertable": True}
    results = [table, trend]
    calls = []

    def submit(payload, budget):
        calls.append(budget)
        context = json.loads(payload["request"])
        assert context["style"] == "journalistic"
        if outcome == "timeout":
            raise TimeoutError()
        focus = context["headline_focus"]
        headline = {"en": f"Torrent and {focus['en']}", "es": f"Torrent y {focus['es']}"}
        if outcome == "rejected":
            headline["es"] += ". La lluvia cae"
        return {"output": {"headline": headline,
                           "lead": {"en": "The records offer a local overview.",
                                    "es": "Los registros ofrecen una visión de la zona."}}}

    monkeypatch.setattr(llm, "_submit", submit)
    findings = write_findings(plan, results, CATALOG)
    piece = compose(plan, results, findings, CATALOG)["editorial"]["journalistic"]
    assert len(calls) == 1 and 0 < calls[0] <= llm.SCAN_TIME_LIMIT < llm.TIME_LIMIT
    assert "Torrent" in piece["headline"]["es"] and "temperatura del aire" in piece["headline"]["es"].lower()
    assert "lluvia" in piece["lead"]["es"].lower() and "dirección clara" in piece["lead"]["es"]
    assert "{r-s1-trend-0.value}" in piece["lead"]["es"]
    assert "1981 a 2025" in fill(piece["lead"]["es"], results, "es")
    assert ("visió" in piece["lead"]["es"]) == (outcome == "accepted")
    assert "La lluvia cae" not in piece["headline"]["es"]


@pytest.mark.parametrize(("variant", "headline"), [
    ("detected", "La zona cambió de forma distinta a su entorno"),
    ("pretrend", "La diferencia con el entorno ya cambiaba antes del hito"),
    ("inconclusive", "Sin una diferencia clara entre la zona y su entorno"),
    ("unavailable", "Los registros no permiten comparar el antes y el después"),
])
def test_impact_has_a_journalistic_opening_with_its_evidence_limit(monkeypatch, variant, headline):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    primary = {
        **RESULT, "id": "r-impact", "variable": "lst", "unit": "°C", "evidence_level": "E2",
        "method": {"operation": "baci_contrast", "params": {"before": [2019, 2021], "after": [2023, 2025]}},
    }
    if variant == "pretrend":
        primary["diagnostics"] = {"pre_trend_control": True}
    if variant in {"inconclusive", "unavailable"}:
        del primary["value"]
        primary["absence"] = {"reason": "not_computed", "detail": {"en": "No contrast."}}
        if variant == "inconclusive":
            primary["direction_assertable"] = False
            primary["interval"] = {"low": -0.5, "high": 0.2, "level": 0.95}
        else:
            del primary["interval"]
    plan = {**PLAN, "design": "impact"}
    checked = review([primary], write_findings(plan, [primary], CATALOG), CATALOG)
    story = compose(plan, checked["results"], checked["findings"], CATALOG)
    piece = story["editorial"]["journalistic"]
    assert piece["headline"]["es"] == headline and piece["headline"]["en"]
    assert piece["results"] == ["r-impact"]
    lead = fill(piece["lead"]["es"], checked["results"], "es")
    if variant in {"detected", "pretrend"}:
        assert "°C" in lead and "95 %" in lead and "2019 a 2021" in lead
    if variant == "pretrend":
        assert checked["results"][0]["evidence_level"] == "E1"
        assert "ya cambiaba antes del hito" in piece["cannot_say"]["es"]


@pytest.fixture(autouse=True)
def configured(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HUP_LLM_MODEL_TEXT", "test-model")
    monkeypatch.setenv("HUP_OFFLINE", "0")


def test_editorial_receives_question_and_reuses_reviewed_piece(monkeypatch):
    calls = []
    monkeypatch.setenv("HUP_LLM_MODEL_TEXT", "gpt-6.1-terra")

    def complete(task, payload):
        assert payload["reasoning_effort"] == "none"
        assert 0 < payload["timeout"] <= 9.5
        assert payload["max_tokens"] == 1500
        assert set(payload["schema"]["properties"]["editorial"]["properties"]) == {"journalistic"}
        calls.append(json.loads(payload["request"]))
        return recorded_answer()

    monkeypatch.setattr(adapter, "complete", complete)
    candidates = write_findings(PLAN, [RESULT], CATALOG)
    assert calls[0]["request"] == PLAN["request"]
    assert calls[0]["answer"] == PLAN["answer"]
    assert calls[0]["source_names"] == {"power": {"en": "NASA POWER"}}
    same_question = {**PLAN, "request": {**PLAN["request"], "text": "  ¿SE   CALIENTA  TORRENT?  "}}
    assert write_findings(same_question, [RESULT], CATALOG) == candidates
    findings = review([RESULT], candidates, CATALOG)["findings"]
    story = compose(PLAN, [RESULT], findings, CATALOG)
    assert set(story["editorial"]) == {"journalistic"}
    assert story["institutional"][0]["block"] == "KeyFigure"
    assert story["scientific"][0]["block"] == "KeyFigure"
    assert story["editorial"]["journalistic"]["results"] == [RESULT["id"]]
    assert "blocks" not in story["editorial"]["journalistic"]
    assert [b["block"] for b in story["journalistic"]] == ["EvidenceBadge", "KeyFigure", "LimitsBox", "SourcesTable"]
    assert fill(story["editorial"]["journalistic"]["lead"]["es"], [RESULT], "es").startswith(
        "La temperatura del aire aumenta 0,27 °C por década"
    )
    assert not contracts.errors("analysis", {
        **json.loads(ANALYSIS_EXAMPLE.read_text(encoding="utf8")), "story": story,
    })
    assert len(calls) == 1
    new_question = {**PLAN, "request": {**PLAN["request"], "text": "¿A qué ritmo se calienta Torrent?"}}
    assert write_findings(new_question, [RESULT], CATALOG) != candidates
    assert len(calls) == 2


@pytest.mark.parametrize(("invalid", "expected"), [
    ({}, {"body", "closing", "featured"}),
    ({"body": [{"en": "It caused extra heat.", "es": "Provocó calor adicional."}]}, {"closing", "featured"}),
    ({"body": [{"en": "At {r-trend.value}.", "es": "En la zona."}]}, {"closing", "featured"}),
    ({"body": [{"en": "Interval of {r-trend.interval}.", "es": "Intervalo de {r-trend.interval}."}]},
     {"closing", "featured"}),
    ({"body": [{"en": "The interval ({r-trend.interval}).", "es": "El intervalo ({r-trend.interval})."}]},
     {"closing", "featured"}),
    ({"closing": {"en": "It reports 17 days.", "es": "Presenta 17 días."}}, {"body", "featured"}),
    ({"closing": {"en": "It is associated with traffic.", "es": "Se asocia con el tráfico."}}, {"body", "featured"}),
    ({"featured": "r-missing"}, {"body", "closing"}),
    ({"featured": "r-trend"}, {"body", "closing"}),  # a number is not a chart or map
    ({"body": [], "closing": None, "featured": None}, set()),
])
def test_editorial_parts_are_reviewed_and_published_independently(monkeypatch, invalid, expected):
    series = {**RESULT, "id": "r-series", "type": "series", "method": {"operation": "read_series"}}
    series.pop("value")
    results = [RESULT, series]
    catalog = {**CATALOG, "blocks": [*CATALOG["blocks"], {
        "id": "TrendChart", "needs": {"result_types": ["series"]}, "styles": list(STYLES),
    }]}
    calls = []

    def complete(task, payload):
        calls.append(json.loads(payload["request"]))
        response = recorded_answer()
        response["output"]["editorial"]["journalistic"].update({
            "body": [{
                "en": "The regional record puts the rate at {r-trend.value} {r-trend.unit}.",
                "es": "El registro regional sitúa el ritmo en {r-trend.value} {r-trend.unit}.",
            }],
            "closing": {"en": "Read this as a regional result.", "es": "La lectura es regional."},
            "featured": series["id"],
            **invalid,
        })
        response["output"]["editorial"]["journalistic"]["blocks"].append("TrendChart")
        return response

    monkeypatch.setattr(adapter, "complete", complete)
    findings = review(results, write_findings(PLAN, results, catalog), catalog)["findings"]
    story = compose(PLAN, results, findings, catalog)
    piece = story["editorial"]["journalistic"]
    assert set(piece) == {*llm.EDITORIAL_FIELDS, "results", *expected}
    assert piece["headline"] == recorded_answer()["output"]["editorial"]["journalistic"]["headline"]
    assert any(block["block"] == "TrendChart" and series["id"] in block["results"] for block in story["journalistic"])
    assert not contracts.errors("analysis", {**json.loads(ANALYSIS_EXAMPLE.read_text(encoding="utf8")), "story": story})
    assert len(calls) == 1
    assert calls[0]["figure_results"] == {"TrendChart": [series["id"]]}
    assert compose(PLAN, results, findings, catalog) == story


@pytest.mark.parametrize(("field", "bad", "reason"), [
    ("blocks", ["EvidenceBadge", "BeforeAfterMap"], "invalid_editorial_blocks"),
    ("blocks", ["KeyFigure"], "invalid_editorial_blocks"),
    ("results", ["r-other"], "invalid_editorial_citations"),
    ("lead", {"en": "It reports 0.27.", "es": "Da 0,27."}, "missing_lead_figure"),
    ("meaning", {"en": "The increase caused stress.", "es": "El aumento provocó estrés."}, "causal_language"),
    ("meaning", {"en": "Five streets.", "es": "Cinco calles."}, "unexpected_markup_or_literal_figure"),
    ("meaning", {"en": "A direct measurement.", "es": "Una medición directa."}, "data_class"),
    ("meaning", {"en": "Rate: {r-trend.support}.", "es": "Ritmo: {r-trend.support}."}, "invalid_placeholder"),
    ("meaning", {"en": "Result: {r-other.value}.", "es": "Resultado: {r-other.value}."}, "invalid_placeholder"),
    ("cannot_say", {"en": "", "es": "Los datos no distinguen calles."}, "invalid_editorial_prose"),
    ("headline", {"en": "Air temperature decreases", "es": "Desciende la temperatura"}, "invalid_editorial_prose"),
])
def test_invalid_editorial_preserves_the_full_fallback(monkeypatch, field, bad, reason):
    requests = []

    def complete(task, payload):
        requests.append(json.loads(payload["request"]))
        response = recorded_answer()
        response["output"]["editorial"]["journalistic"][field] = bad
        return response

    monkeypatch.setattr(adapter, "complete", complete)
    findings = review([RESULT], write_findings(PLAN, [RESULT], CATALOG), CATALOG)["findings"]
    story = compose(PLAN, [RESULT], findings, CATALOG)
    assert requests[1]["retry_reason"] == reason
    assert findings[0]["id"] == "f-r-trend"
    assert story["editorial"]["journalistic"]["headline"]["es"] == "Qué dicen los datos sobre esta pregunta"
    assert [b["block"] for b in story["journalistic"]] == ["KeyFigure", "EvidenceBadge", "LimitsBox", "SourcesTable"]


def test_review_changes_prevent_editorial_publication(monkeypatch):
    monkeypatch.setattr(adapter, "complete", lambda *_: recorded_answer())
    candidates = write_findings(PLAN, [RESULT], CATALOG)
    approved = review([RESULT], candidates, CATALOG)["findings"]
    assert "editorial" in compose(PLAN, [RESULT], approved, CATALOG)
    approved[0]["text"] = {lang: line + " " for lang, line in approved[0]["text"].items()}
    story = compose(PLAN, [RESULT], approved, CATALOG)
    assert story["editorial"]["journalistic"]["headline"]["es"] == "Qué dicen los datos sobre esta pregunta"


@pytest.mark.parametrize("meaning", [
    "The question is outside the record once the available period ends.",
    "The record ends on January 20, 2020, before the requested period.",
])
def test_only_absences_fill_source_coverage_dates_without_any_findings(monkeypatch, meaning):
    missing = {k: v for k, v in RESULT.items() if k not in {"value", "interval", "unit"}}
    missing["absence"] = {
        "reason": "no_coverage",
        "detail": {
            "en": "This copy covers 2002-06-01 to 2020-01-20, with nothing for the requested period.",
            "es": "La copia cubre del 2002-06-01 al 2020-01-20, sin datos del periodo consultado.",
        },
    }
    calls = []

    def complete(task, payload):
        calls.append(json.loads(payload["request"]))
        pair = {"en": "The record is missing for this period.", "es": "Faltan datos de este periodo."}
        return {
            "output": {
                "findings": [],
                "editorial": {"journalistic": {
                    **dict.fromkeys(llm.EDITORIAL_FIELDS, pair), "results": [missing["id"]], "blocks": [],
                    "meaning": {"en": meaning, "es": pair["es"]},
                    "cannot_say": {
                        "en": "The source stopped in January 2020.",
                        "es": "El registro termina en enero de 2020.",
                    },
                }},
            },
        }

    monkeypatch.setattr(adapter, "complete", complete)
    for _ in range(2):
        assert write_findings(PLAN, [missing], CATALOG) == []
        story = compose(PLAN, [missing], [], CATALOG)
        piece = story["editorial"]["journalistic"]
        assert piece["headline"]["es"] == "Faltan datos de este periodo."
        assert piece["cannot_say"]["es"].endswith("20 de enero de 2020.")
        assert piece["cannot_say"]["en"].endswith("January 20, 2020.")
        assert "2020" not in piece["meaning"]["en"]
        if "once" in meaning:
            assert piece["meaning"]["en"] == meaning
        assert story["journalistic"] == [{"block": "LimitsBox", "results": [missing["id"]]}]
    assert len(calls) == 1 and not calls[0]["has_evidence_for_findings"]
    assert calls[0]["evidence"][0]["absence"]["reason"] == missing["absence"]["reason"]
    assert "2020-01-20" not in json.dumps(calls[0]["evidence"])


@pytest.mark.parametrize("failure", [None, "direction", "figure"])
def test_inconclusive_trend_can_publish_without_claiming_a_direction_or_figure(monkeypatch, failure):
    result = {k: v for k, v in RESULT.items() if k != "value"}
    result.update(
        direction_assertable=False, evidence_level="E1", interval={"low": -1, "high": 1},
        absence={"reason": "not_computed", "detail": {"en": "The interval includes zero.", "es": "Incluye el cero."}},
    )
    calls = []

    def complete(task, payload):
        calls.append(json.loads(payload["request"]))
        assert calls[-1]["evidence"][0]["inconclusive_trend"] is True
        assert calls[-1]["evidence"][0]["evidence_level"] == "E0"
        assert not calls[-1]["has_evidence_for_findings"]
        headline = {"en": "No clear trend in rainfall", "es": "No hay un cambio claro en la lluvia"}
        lead = {"en": "The evidence gives no clear direction.", "es": "Los datos no indican una dirección clara."}
        if failure == "direction":
            lead = {"en": "Rainfall decreases.", "es": "La lluvia disminuye."}
        elif failure == "figure":
            lead = {"en": "The evidence gives no clear direction over twenty years.",
                    "es": "Los datos no indican una dirección clara."}
        return {"output": {"findings": [], "editorial": {"journalistic": {
            **dict.fromkeys(llm.EDITORIAL_FIELDS, {
                "en": "The record gives no clear direction.", "es": "El registro no indica una dirección clara.",
            }),
            "lead": lead, "headline": headline, "results": [result["id"]], "blocks": [],
            "cannot_say": {
                "en": "The evidence cannot establish whether rainfall increased or decreased.",
                "es": "Los datos no permiten saber si la lluvia aumentó o disminuyó.",
            },
        }}}}

    monkeypatch.setattr(adapter, "complete", complete)
    assert write_findings(PLAN, [result], CATALOG) == []
    after = review([result], [], CATALOG)["results"]
    assert after[0]["evidence_level"] == "E0"
    story = compose(PLAN, after, [], CATALOG)
    if failure == "direction":
        assert story["editorial"]["journalistic"]["headline"]["es"] == "Los datos no muestran una dirección clara"
    if failure == "figure":
        assert "twenty" not in story["editorial"]["journalistic"]["lead"]["en"]
    assert len(calls) == (2 if failure == "direction" else 1)
    assert result["direction_assertable"] is False and "value" not in result


@pytest.mark.parametrize(("spanish", "english", "publishes"), [
    ("Faltan datos en 2024.", "Records are missing for 2024.", True),
    ("Faltan datos en 2025.", "Records are missing for 2025.", False),
    ("Faltan datos para 2024 mm.", "Data are missing for 2024 mm.", False),
    ("Faltan datos para 2024 personas.", "Data are missing for 2024 people.", False),
])
def test_absence_can_repeat_only_the_single_year_requested_in_the_plan(monkeypatch, spanish, english, publishes):
    plan = {**PLAN, "design": "state", "period": {"start": "2024-01-01", "end": "2024-12-31"},
            "request": {"text": "¿Cómo estaba el mar en 2024?", "language": "es"}}
    missing = {k: v for k, v in RESULT.items() if k not in {"value", "interval", "unit"}}
    missing["absence"] = {"reason": "no_coverage", "detail": {"en": "No coverage.", "es": "Sin cobertura."}}

    def complete(task, payload):
        assert json.loads(payload["request"])["requested_year"] == "2024"
        pair = {"en": english, "es": spanish}
        return {"output": {"findings": [], "editorial": {"journalistic": {
            **dict.fromkeys(llm.EDITORIAL_FIELDS, pair), "results": [missing["id"]], "blocks": [],
        }}}}

    monkeypatch.setattr(adapter, "complete", complete)
    assert write_findings(plan, [missing], CATALOG) == []
    published = compose(plan, [missing], [], CATALOG)["editorial"]["journalistic"]["headline"]["es"]
    assert published == (spanish if publishes else "Los datos disponibles dejan abierta la pregunta")


def test_whatif_repairs_long_headline_and_keeps_estimate_figure_without_retry(monkeypatch):
    result = {k: v for k, v in RESULT.items() if k not in {"interval", "period"}}
    result.update(
        variable="water", unit="L/day", method={"operation": "resource_estimate"},
        data_class="approximated", evidence_level="E0",
        table={"columns": ["low", "high", "reference", "quantity", "per"], "rows": [[0.02, 0.04, "Test", 1, "people"]]},
    )
    plan = {**PLAN, "design": "whatif"}
    calls = []

    def complete(task, payload):
        calls.append(payload)
        assert "KeyFigure" in json.loads(payload["request"])["eligible_blocks"]["journalistic"]
        draft = recorded_answer()
        output = draft["output"]
        lead = {
            "en": "Estimated water use is {r-trend.value} {r-trend.unit}.",
            "es": "El consumo de agua estimado es de {r-trend.value} {r-trend.unit}.",
        }
        text = {lang: line.rstrip(".") + " ({r-trend.table})." for lang, line in lead.items()}
        output["findings"][0].update(text=text, evidence_level="E0")
        output["editorial"]["journalistic"].update(
            lead=lead,
            headline={
                "en": "An excessively long headline about the estimated use of water for new homes in the city",
                "es": "Un titular demasiado largo sobre el consumo de agua de las nuevas viviendas de esta ciudad",
            },
            blocks=["EvidenceBadge"],
        )
        return draft

    monkeypatch.setattr(adapter, "complete", complete)
    written = write_findings(plan, [result], CATALOG)
    checked = review([result], written, CATALOG)
    story = compose(plan, checked["results"], checked["findings"], CATALOG)
    assert len(calls) == 1
    assert story["editorial"]["journalistic"]["headline"]["es"] == "Estimación de consumo de agua"
    assert story["journalistic"][0]["block"] == "KeyFigure"
    assert story["journalistic"][0]["results"] == [RESULT["id"]]
    assert story["journalistic"][0]["finding"] == written[0]["id"]


def test_rainfall_editorial_keeps_one_limit_for_two_cited_results():
    result = {**RESULT, "method": {"operation": "rain_extreme"}, "support": {"resolution_m": 11000}}
    anomaly = {**result, "id": "r-rain-anomaly", "method": {"operation": "same_date_anomaly"}, "unit": "mm"}
    pieces = {"journalistic": {
        "cannot_say": {"en": "The grid is coarse.", "es": "La rejilla es gruesa."},
        "results": [result["id"], anomaly["id"]],
    }}
    limit = llm._with_interpretation_limits(pieces, [result, anomaly])["journalistic"]["cannot_say"]
    for language, expected in (("es", "lluvias locales intensas"), ("en", "intense local rainfall")):
        line = fill(limit[language], [result, anomaly], language)
        assert line.count(".") == 1 and expected in line and "11 km" in line


def test_rejection_pinpoints_causal_connector_and_whatif_accepts_estimation(monkeypatch, caplog):
    result = {**RESULT, "method": {"operation": "resource_estimate"}, "unit": "L/day",
              "data_class": "approximated", "evidence_level": "E0"}
    plan = {**PLAN, "design": "whatif", "request": {"text": "How much water would homes need?", "language": "en"}}
    requests = []

    def complete(task, payload):
        requests.append(json.loads(payload["request"]))
        draft = recorded_answer()
        line = {
            "en": "The homes would require {r-trend.value} {r-trend.unit}, {r-trend.period} ({r-trend.interval}).",
            "es": "Las viviendas requerirían {r-trend.value} {r-trend.unit}, {r-trend.period} ({r-trend.interval}).",
        }
        draft["output"]["findings"][0].update(text=line, evidence_level="E0")
        piece = draft["output"]["editorial"]["journalistic"]
        piece.update(headline={"en": "Estimated household water use", "es": "Consumo de agua estimado"}, lead=line)
        if len(requests) == 1:
            piece["meaning"] = {
                "en": "A secret phrase caused stress.", "es": "Una frase privada provocó estrés.",
            }
        return draft

    monkeypatch.setattr(adapter, "complete", complete)
    with caplog.at_level("INFO", logger="hup_api.writer.llm"):
        findings = write_findings(plan, [result], CATALOG)
    assert findings[0]["id"].startswith("f-l2-")
    assert requests[1]["retry_reason"] == "causal_language"
    assert requests[1]["retry_issue"]["field"] == "editorial.journalistic.meaning.en"
    assert requests[1]["retry_issue"]["term"] == "caused"
    assert "L2 review outcome=causal_language" in caplog.text
    assert "editorial.journalistic.meaning.en" in caplog.text
    assert "secret phrase" not in caplog.text and "secret phrase" not in str(requests[1])
