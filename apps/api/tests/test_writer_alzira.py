"""Regression fixtures from the corrected Alzira integration runs."""

import json
from pathlib import Path

import pytest

from hup_api.reviewer import review
from hup_api.writer import STYLES, compose, fill, write_findings
from hup_api.writer.llm import _check, _required_fields

FIXTURES = Path(__file__).parent / "fixtures" / "writer_alzira"


@pytest.mark.parametrize("design", ["state", "event"])
def test_archived_alzira_findings_render_in_both_languages(design, monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    path = FIXTURES / design
    a, results = (json.loads((path / f"{k}.json").read_text(encoding="utf8")) for k in ("analysis", "results"))
    catalog = json.loads(Path("catalog/catalog.json").read_text(encoding="utf8"))
    by_id = {r["id"]: r for r in results}
    out = review(results, write_findings(a["plan"], results, catalog), catalog)
    assert out["findings"]
    for language, month in (("en", "October 2024"), ("es", "octubre de 2024")):
        story = compose(a["plan"], out["results"], out["findings"], catalog)
        for style, blocks in story.items():
            if style == "editorial":
                continue
            linked = [b for b in blocks if b.get("finding")]
            assert linked
            for block in linked:
                text = fill(block["text"][language], [by_id[r] for r in block["results"]], language)
                assert month in text
                assert "{" not in text
                if design == "state":
                    assert "403 mm" in text and "105 mm" in text
    if design == "state":
        old = a["findings"][0]
        output = {"findings": [{k: old[k] for k in ("text", "results", "evidence_level")}
                               | {"story": dict.fromkeys(STYLES, old["text"])}]}
        assert _check(output, results, catalog, "test")[2] == "missing_report_fields"
    l2 = []
    for finding in out["findings"]:
        result, = (by_id[rid] for rid in finding["results"])
        slots = ", ".join(f"{{{result['id']}.{field}}}" for field in sorted(_required_fields(result)))
        text = {"en": f"Report values: {slots}.", "es": f"Valores del informe: {slots}."}
        l2.append({"text": text, "story": dict.fromkeys(STYLES, text),
                   "results": finding["results"], "evidence_level": finding["evidence_level"]})
    findings, stories, error = _check({"findings": l2}, results, catalog, "test")
    assert error is None
    for finding in findings:
        for text in [finding["text"], *stories[finding["id"]].values()]:
            en, es = (fill(text[language], results, language) for language in ("en", "es"))
            assert "October 2024" in en and "octubre de 2024" in es
            if design == "state":
                assert all("403 mm" in line and "105 mm" in line for line in (en, es))


def test_pretrend_explanation_survives_fallback_and_l2_review(monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    result = {
        "id": "r-s3-contrast", "type": "value", "variable": "lst", "value": 0.16, "unit": "°C",
        "data_class": "measured", "evidence_level": "E1", "fingerprint": "a" * 64,
        "interval": {"level": 0.95, "low": 0.004, "high": 0.32},
        "method": {"operation": "baci_contrast", "params": {"before": [2019, 2021], "after": [2023, 2025]}},
        "diagnostics": {"pre_trend_control": True},
        "sources": [{"source": "landsat-c2-l2", "url": "https://www.usgs.gov/landsat"}],
    }
    catalog = {"variables": [{"id": "lst", "name": {"en": "Land surface temperature"}}]}
    plan = {"design": "impact", "zone": {"name": "Alzira"}}
    fallback, = write_findings(plan, [result], catalog)
    slots = ", ".join(f"{{{result['id']}.{field}}}" for field in sorted(_required_fields(result)))
    text = {"en": f"Contrast: {slots}.", "es": f"Contraste: {slots}."}
    findings, stories, error = _check({"findings": [
        {"text": text, "story": dict.fromkeys(STYLES, text), "results": [result["id"]], "evidence_level": "E1"},
    ]}, [result], catalog, "test")
    assert error is None
    for pair in [fallback["text"], findings[0]["text"], *stories[findings[0]["id"]].values()]:
        assert "already changing before the event" in pair["en"]
        assert "ya cambiaba antes del hito" in pair["es"]
