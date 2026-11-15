import json
from pathlib import Path

import pytest

from hup_api.contracts import errors
from hup_api.writer import SLOT, compose, fill, finding_text, write_findings

FIXTURES = Path(__file__).parent / "fixtures" / "writer"


def cases():
    for folder in sorted(FIXTURES.iterdir()):
        yield [json.loads((folder / f"{name}.json").read_text(encoding="utf8")) for name in ("analysis", "results")]


@pytest.mark.parametrize(("analysis", "results"), list(cases()))
def test_real_report_precision_context_and_formats(analysis, results):
    catalog = json.loads(Path("catalog/catalog.json").read_text(encoding="utf8"))
    plan = analysis["plan"]
    findings = write_findings(plan, results, catalog)
    story = compose(plan, results, findings, catalog)
    assert not errors("analysis", {**analysis, "findings": findings, "story": story})
    finding = findings[0]
    assert SLOT.findall(finding["text"]["en"]) == SLOT.findall(finding["text"]["es"])
    rendered = fill(finding["text"]["es"], results, "es")
    result = next(r for r in results if r["id"] == finding["results"][0])
    expected = {"ndvi": "0,29", "lst": "0,7", "air_temperature": "0,26"}
    assert expected[result["variable"]] in rendered
    assert "intervalo del 95 %" in rendered
    if plan["design"] == "impact":
        assert "2013" in rendered and "2025" in rendered
        assert "coincide en tiempo y lugar" in rendered.lower()
    else:
        assert "por década" in rendered and "aumenta" in rendered
    key = [
        next(block for block in story[style] if block["block"] == "KeyFigure")
        for style in ("journalistic", "institutional", "scientific")
    ]
    strings = [fill(block["text"]["es"], results, "es") for block in key]
    assert len(set(strings)) == 3
    if any(r.get("limits") or "absence" in r for r in results):
        assert all(any(b["block"] == "LimitsBox" for b in blocks)
                   for style, blocks in story.items() if style != "editorial")

def test_units_warn_in_both_languages():
    result = {"id": "r-x", "unit": "mm/month"}
    assert fill("{r-x.unit}", [result], "es") == "mm al mes"
    assert fill("{r-x.unit}", [result], "en") == "mm per month"
    result["unit"] = "custom"
    with pytest.warns(UserWarning, match="Untranslated"):
        assert fill("{r-x.unit}", [result], "en") == "custom"


def test_crossing_interval_reports_no_direction():
    result = {"id": "r-x", "variable": "ndvi", "value": 0.29, "unit": "1",
              "data_class": "measured", "direction_assertable": False,
              "interval": {"low": -0.1, "high": 0.6, "level": 0.95},
              "method": {"operation": "baci_contrast"}, "sources": [{"source": "test"}]}
    finding = write_findings({}, [result], {})[0]
    assert "dirección" in finding["text"]["es"]
    assert ".value}" not in finding["text"]["en"]


@pytest.mark.parametrize("level", ["E0", "E1"])
def test_annual_air_temperature_wording_retains_the_period(level):
    analysis, results = next((a, r) for a, r in cases() if a["plan"]["design"] == "trend")
    trend = next(result for result in results if result["method"]["operation"] == "trend")
    text = finding_text(trend, {}, level, analysis["plan"])
    for language, incorrect in (("en", "monthly"), ("es", "mensual")):
        sentence = fill(text[language], results, language)
        assert incorrect not in sentence
        assert trend["period"]["start"][:4] in sentence and trend["period"]["end"][:4] in sentence
    if level == "E0":
        assert "shows a trend" not in text["en"]


def test_trend_report_badge_uses_the_reviewed_finding_and_keeps_the_input_series():
    from hup_api.reviewer import review

    analysis, results = next((a, r) for a, r in cases() if a["plan"]["design"] == "trend")
    catalog = json.loads(Path("catalog/catalog.json").read_text(encoding="utf8"))
    plan = analysis["plan"]
    checked = review(results, write_findings(plan, results, catalog), catalog)
    trend, = [f for f in checked["findings"] if f["review"]["status"] == "approved"]
    assert trend["evidence_level"] == "E1"
    series, = [r["id"] for r in checked["results"] if r["type"] == "series"]
    story = compose(plan, checked["results"], checked["findings"], catalog)
    for style, blocks in story.items():
        if style == "editorial":
            continue
        levels = [block["results"] for block in blocks if block["block"] == "EvidenceBadge"]
        assert levels == [trend["results"]]
        assert any(series in block["results"] for block in blocks if block["block"] == "TrendChart")
        assert any(series in block["results"] for block in blocks if block["block"] == "SourcesTable")


def test_projection_wording_shows_the_windows_in_the_result():
    result = {
        "id": "r-s3-delta", "method": {"operation": "climate_delta", "params": {
            "windows": {"2040": [2035, 2044], "2030": [2025, 2034], "2050": [2045, 2054]},
        }}, "unit": "mm", "variable": "pr_total",
        "series": [{"t": str(year), "v": 520, "low": 470, "high": 560, "scenario": "ssp245"}
                   for year in (2030, 2040, 2050)],
    }
    text = finding_text(result, {"en": "rainfall", "es": "la lluvia"}, "E0")
    for language, ranges in (("en", ("2025 to 2034", "2035 to 2044", "2045 to 2054")),
                             ("es", ("2025 a 2034", "2035 a 2044", "2045 a 2054"))):
        sentence = fill(text[language], [result], language)
        positions = [sentence.index(bounds) for bounds in ranges]
        assert positions == sorted(positions)
