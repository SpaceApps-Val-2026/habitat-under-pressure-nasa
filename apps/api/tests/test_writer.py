import json
from pathlib import Path

import pytest

from hup_api.contracts import errors
from hup_api.writer import compose, fill, write_findings

EXAMPLES = Path("docs/contratos/examples")


def example(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf8"))


def test_example_composition():
    analysis, results, catalog = map(example, ("analysis", "results", "catalog"))
    plan = analysis["plan"]
    findings = write_findings(plan, results, catalog)
    story = compose(plan, results, findings, catalog)
    styles = ("journalistic", "institutional", "scientific")
    assert findings and set(story) == {*styles, "editorial"}
    assert not errors("analysis", {**analysis, "findings": findings, "story": story})
    absent = {r["id"] for r in results if "absence" in r}
    assert absent
    for style in styles:
        blocks = story[style]
        assert any(absent.intersection(b.get("results", [])) for b in blocks if b["block"] == "LimitsBox")
        assert all(not absent.intersection(b.get("results", [])) for b in blocks if b["block"] != "LimitsBox")
    assert story == compose(plan, list(reversed(results)), findings, catalog)


def test_fill_locale_and_unknown():
    results = [{"id": "r-step-value", "value": 1234.5}]
    assert fill("{r-step-value.value}", results, "en") == "1,230"
    assert fill("{r-step-value.value}", results, "es") == "1.230"
    with pytest.raises(ValueError):
        fill("{r-step-value.other}", results, "es")


def test_real_trend_template_and_neutral():
    from hup_ops.registry import REGISTRY, Context

    result = REGISTRY["trend"].fn(
        {},
        {
            "read": [
                {
                    "variable": "air_temperature",
                    "unit": "degC",
                    "data_class": "reanalysis",
                    "sources": [{"source": "test", "url": "urn:hup:test"}],
                    "series": [{"t": str(year), "v": year} for year in range(1981, 2025)],
                }
            ]
        },
        Context({}, {}, 1, None, lambda *_: None),
    )["trend"]
    result["id"] = "r-s1-trend"
    findings = write_findings({}, [result], example("catalog"))
    assert findings[0]["data_class"] == "reanalysis"
    assert findings[0]["evidence_level"] == "E1"
    assert "{r-s1-trend.value}" in findings[0]["text"]["en"]
    result["direction_assertable"] = False
    neutral = write_findings({}, [result], example("catalog"))[0]
    assert neutral["evidence_level"] == "E0"
    assert "{" not in neutral["text"]["en"]

def test_written_placeholders_and_catalog_needs():
    import re

    analysis, results, catalog = map(example, ("analysis", "results", "catalog"))
    findings = write_findings(analysis["plan"], results, catalog)
    for finding in findings:
        cited = [r for r in results if r["id"] in finding["results"]]
        for language, text in finding["text"].items():
            assert not re.search(r"\d", re.sub(r"\{[^}]+\}|\b(?:19|20)\d\d\b", "", text))
            assert "{" not in fill(text, cited, language)
    story = compose(analysis["plan"], results, findings, catalog)
    definitions = {block["id"]: block for block in catalog["blocks"]}
    by_id = {r["id"]: r for r in results}
    for style, blocks in story.items():
        if style == "editorial":
            continue
        for block in blocks:
            definition = definitions[block["block"]]
            assert style in definition["styles"]
            needs = definition["needs"]
            assert len(block["results"]) >= needs.get("min_results", 1)
            for rid in block["results"]:
                result = by_id[rid]
                assert set(needs.get("fields", [])) <= result.keys()
                assert result["type"] in needs.get("result_types", [result["type"]])

def test_report_styles_use_supported_interval_placeholders():
    analysis, results, catalog = map(example, ("analysis", "results", "catalog"))
    story = compose(analysis["plan"], results, [], catalog)
    for style in ("institutional", "scientific"):
        key = next(item for item in story[style] if item["block"] == "KeyFigure")
        for language in ("en", "es"):
            assert ".interval}" in key["text"][language]
            assert "{" not in fill(key["text"][language], results, language)
    guarded = [{**result, "direction_assertable": False} for result in results]
    story = compose(analysis["plan"], guarded, [], catalog)
    assert all("text" not in block for block in story["scientific"] if block["block"] == "KeyFigure")


def test_fallback_report_carries_each_episode_finding_without_an_interval(monkeypatch):
    from hup_api.reviewer import review

    monkeypatch.setenv("HUP_OFFLINE", "1")
    plan = {"design": "event"}
    catalog = example("catalog")
    prototype = next(r for r in example("results") if r["type"] == "value" and "value" in r)
    results = [
        {
            **{k: v for k, v in prototype.items() if k not in {"interval", "direction_assertable", "diagnostics"}},
            "id": f"r-event-{index}",
            "variable": "precipitation",
            "unit": unit,
            "value": value,
            "evidence_level": "E0",
            "period": {"start": "1998-01-01", "end": "2024-10-31"},
            "support": {"resolution_m": 11_132},
            "method": {"operation": operation, "version": "1.0", "params": {"year": 2024, "months": [10]}},
        }
        for index, (operation, value, unit) in enumerate((
            ("rain_extreme", 85, "percentile"), ("same_date_anomaly", 51, "mm"),
        ))
    ]
    reviewed = review(results, write_findings(plan, results, catalog), catalog)
    assert len(reviewed["findings"]) == 2
    story = compose(plan, reviewed["results"], reviewed["findings"], catalog)
    for style, blocks in story.items():
        if style == "editorial":
            continue
        prose = [b for b in blocks if b.get("finding")]
        assert [b["results"] for b in prose] == [[r["id"]] for r in results]
        for language in ("en", "es"):
            for block in prose:
                rendered = fill(block["text"][language], reviewed["results"], language)
                assert ("October 2024" if language == "en" else "octubre de 2024") in rendered
                assert ("11.1 km" if language == "en" else "11,1 km") in rendered
                assert ("few hours" if language == "en" else "unas horas") in rendered


def test_multi_scenario_results_are_not_charted_as_one_univariate_series():
    catalog = example("catalog")
    catalog["variables"].append({"id": "pr_total", "name": {
        "en": "Projected precipitation, yearly total", "es": "Precipitación proyectada, total anual",
    }})
    prototype = {"type": "series", "variable": "pr_total", "evidence_level": "E0"}
    reference = {
        **prototype, "id": "r-annual-reference", "method": {"operation": "read_series"},
        "series": [{"t": "2000", "v": 10}],
    }
    projection = {
        **prototype, "id": "r-projection", "method": {"operation": "climate_delta"},
        "series": [
            {"t": "2030", "v": 12, "low": 10, "high": 13, "scenario": "ssp126"},
            {"t": "2030", "v": 14, "low": 13, "high": 16, "scenario": "ssp245"},
        ],
        "limits": [{"en": "Model spread."}],
    }
    uncertainty = {
        **prototype, "id": "r-uncertainty", "type": "table", "method": {"operation": "uncertainty_split"},
        "table": {"columns": ["horizon", "model_variance", "scenario_variance", "models", "scenarios"],
                  "rows": [[2030, 0.2, 0.5, ["a", "b"], ["ssp126", "ssp245"]]]},
    }
    results = [projection, reference, uncertainty]
    from hup_api.reviewer import review

    plan = {"design": "scenario"}
    for result in results:
        result.update(unit="°C", data_class="extrapolated", sources=[{"source": "test", "url": "urn:test"}])
    findings = review(results, write_findings(plan, results, catalog), catalog)["findings"]
    assert [f["results"] for f in findings] == [["r-projection"], ["r-uncertainty"]]
    assert "projected precipitation, yearly total" in findings[0]["text"]["en"]
    assert "precipitación proyectada, total anual" in findings[0]["text"]["es"]
    assert "{r-projection.series}" not in findings[0]["text"]["es"]
    assert "horizonte" in findings[0]["text"]["es"]
    story = compose(plan, results, findings, catalog)
    for style, blocks in story.items():
        if style == "editorial":
            continue
        chart = next(b for b in blocks if b["block"] == "TrendChart")
        assert chart["results"] == [reference["id"]]
        limits = next(b for b in blocks if b["block"] == "LimitsBox")
        assert limits["results"] == [projection["id"]]
        sources = next(b for b in blocks if b["block"] == "SourcesTable")
        assert set(sources["results"]) == {r["id"] for r in results}
        paragraphs = {b["results"][0]: fill(b["text"]["es"], results, "es") for b in blocks if b.get("finding")}
        assert "2030" in paragraphs["r-projection"] and "ssp245" in paragraphs["r-projection"]
        assert "14" in paragraphs["r-projection"] and "13 a 16" in paragraphs["r-projection"]
        assert "0,2" in paragraphs["r-uncertainty"] and "0,5" in paragraphs["r-uncertainty"]


def test_scenario_rendering_uses_named_columns_and_rejects_incomplete_rows():
    result = {
        "id": "r-split", "unit": "°C^2", "method": {"operation": "uncertainty_split"},
        "table": {"columns": ["scenario_variance", "horizon", "model_variance"], "rows": [[0.5, 2030, 0.2]]},
    }
    assert fill("{r-split.table}", [result], "en") == "2030: models 0.2, scenarios 0.5 °C^2"
    result["table"]["rows"] = [[0.5, 2030]]
    with pytest.raises(ValueError):
        fill("{r-split.table}", [result], "en")
    projection = {
        "id": "r-projection", "method": {"operation": "climate_delta"}, "series": [{"t": "2030"}],
    }
    with pytest.raises(ValueError):
        fill("{r-projection.series}", [projection], "en")


def return_level_results(series, params, days=None):
    """Results of the real return_level operation, with the ids and fields the executor gives them."""
    from hup_ops.registry import REGISTRY, Context

    read = {"variable": "pr_max_1day_observed", "unit": "mm", "data_class": "measured",
            "sources": [{"source": "imerg-daily", "url": "https://doi.org/10.5067/GPM/IMERGDF/DAY/07"}],
            "support": {"resolution_m": 11100}, "series": series}
    inputs = {"read": [read]}
    if days is not None:  # the daily series of the episode (return_level 1.2)
        inputs["days"] = [{**read, "variable": "precipitation_daily", "unit": "mm/day", "series": days}]
    bodies = REGISTRY["return_level"].fn(params, inputs, Context({}, {}, 7, None, lambda *_: None))
    return [{**body, "id": f"r-s2-{key}", "type": "value", "data_class": "measured"} for key, body in bodies.items()]


def test_return_levels_and_an_episode_rarer_than_the_record_read_as_findings():
    from hup_api.reviewer import review

    catalog = example("catalog")
    catalog["variables"].append({"id": "pr_max_1day_observed", "name": {
        "en": "Wettest day of the year, observed", "es": "Día más lluvioso del año, observado"}})
    plan = {"design": "event", "zone": {"name": "Paiporta"}}
    years = [{"t": str(1998 + i), "v": 40.0 + (i * 7) % 23 + i % 5} for i in range(26)]
    usual = return_level_results(years + [{"t": "2024", "v": 57.0}], {"event_year": 2024})
    rare = return_level_results(years + [{"t": "2024", "v": 400.0}], {"event_year": 2024})
    assert "value" in next(r for r in usual if r["id"] == "r-s2-event")
    assert next(r for r in rare if r["id"] == "r-s2-event")["absence"]["reason"] == "not_computed"

    reviewed = review(usual, write_findings(plan, usual, catalog), catalog)
    assert not reviewed["warnings"]
    assert [f["results"] for f in reviewed["findings"]] == [["r-s2-event"], *(
        [f"r-s2-level_{years}"] for years in (2, 5, 10, 25))]  # the episode first, then by period
    for language, period, episode in (("es", "una vez cada 10 años", "un año como 2024 se espera"),
                                      ("en", "once every 10 years", "a year like 2024 is expected")):
        text = [fill(f["text"][language], usual, language) for f in reviewed["findings"]]
        assert episode in text[0] and ("26 años anteriores" if language == "es" else "26 years before") in text[0]
        assert period in text[3] and ("intervalo del 95 %" if language == "es" else "95% interval") in text[3]
        assert all(("11,1 km" if language == "es" else "11.1 km") in sentence for sentence in text)

    reviewed = review(rare, write_findings(plan, rare, catalog), catalog)
    episode, = [f for f in reviewed["findings"] if f["results"] == ["r-s2-event"]]
    assert episode["review"]["status"] == "approved"
    assert fill(episode["text"]["es"], rare, "es").startswith(
        "Día más lluvioso del año, observado: 2024 es más raro de lo que permiten estimar 26 años de registro.")
    assert "rarer than 26 years of record" in fill(episode["text"]["en"], rare, "en")
    with pytest.raises(ValueError):  # an absence never fills a figure it does not have
        fill("{r-s2-event.value}", rare, "es")
    story = compose(plan, reviewed["results"], reviewed["findings"], catalog)
    for blocks in (b for b in story.values() if isinstance(b, list)):  # its reason stays in the limits
        assert [b["block"] for b in blocks if "r-s2-event" in b.get("results", [])] == ["LimitsBox"]


def test_an_episode_read_from_its_daily_series_says_it_is_its_wettest_day_that_is_compared():
    """Review of A11: the sentence says what is compared, the wettest day of the episode against the maxima of the
    whole years before, with that day as a slot and no literal figure."""
    from hup_api.reviewer import review

    catalog = example("catalog")
    catalog["variables"].append({"id": "pr_max_1day_observed", "name": {
        "en": "Wettest day of the year, observed", "es": "Día más lluvioso del año, observado"}})
    plan = {"design": "event", "zone": {"name": "Paiporta"}}
    years = [{"t": str(1998 + i), "v": 40.0 + (i * 7) % 23 + i % 5} for i in range(26)]
    days = [{"t": "2024-10-28", "v": 3.0}, {"t": "2024-10-29", "v": 57.16}]
    usual = return_level_results(years, {"event_year": 2024}, days)
    reviewed = review(usual, write_findings(plan, usual, catalog), catalog)
    assert not reviewed["warnings"]
    episode, = [f for f in reviewed["findings"] if f["results"] == ["r-s2-event"]]
    assert "{r-s2-event.event_value}" in episode["text"]["es"] and episode["review"]["status"] == "approved"
    es, en = (fill(episode["text"][language], usual, language) for language in ("es", "en"))
    assert es.startswith("Día más lluvioso del año, observado: un día como el más lluvioso del episodio (57,2 mm) es "
                         "el máximo de un año, de media, una vez cada ")
    assert "según los 26 años anteriores" in es and "a day like the wettest of the episode (57.2 mm)" in en
    rare = return_level_results(years, {"event_year": 2024}, [{"t": "2024-10-29", "v": 400.0}])
    reviewed = review(rare, write_findings(plan, rare, catalog), catalog)
    episode, = [f for f in reviewed["findings"] if f["results"] == ["r-s2-event"]]
    assert episode["review"]["status"] == "approved"
    assert fill(episode["text"]["es"], rare, "es").startswith(
        "Día más lluvioso del año, observado: el día más lluvioso del episodio (400 mm) es más raro de lo que "
        "permiten estimar 26 años de registro.")


def test_other_absences_of_return_level_are_still_not_findings():
    from hup_api.reviewer import review

    catalog = example("catalog")
    short = return_level_results([{"t": str(2000 + i), "v": float(i)} for i in range(5)], {"event_year": 2004})
    assert all(r["absence"]["reason"] == "insufficient_qa" for r in short)
    findings = write_findings({"design": "event"}, short, catalog)
    assert findings == []
    forged = [{"id": "f-x", "order": 1, "text": {"en": "{r-s2-event.record} years.", "es": "{r-s2-event.record} años."},
               "results": ["r-s2-event"], "data_class": "measured", "evidence_level": "E0",
               "review": {"status": "pending"}}]
    assert review(short, forged, catalog)["findings"] == []


def test_the_peak_of_a_yearly_series_says_the_year_and_a_daily_one_its_date():
    def series(times):
        return [{"id": "r-year", "unit": "people", "method": {"operation": "read_series", "version": "1.2"},
                 "series": [{"t": t, "v": v} for t, v in zip(times, (10, 20), strict=True)]}]

    assert fill("{r-year.series}", series(["2021-01-01", "2022-01-01"]), "es").endswith("(2022)")
    assert fill("{r-year.series}", series(["2022-01-01", "2022-01-02"]), "en").endswith("(2022-01-02)")
