from copy import deepcopy

import pytest

from hup_api.reviewer import review
from hup_api.writer import write_findings

RESULT = {
    "id": "r-s1-change",
    "type": "value",
    "variable": "ndvi",
    "unit": "NDVI",
    "value": 2,
    "data_class": "measured",
    "evidence_level": "E2",
    "method": {"operation": "baci_contrast", "params": {}},
    "sources": [{"source": "example", "url": "urn:hup:example"}],
}


@pytest.mark.parametrize("text", ["This caused {r-s1-change.value}.", "The result is 27."])
def test_invalid_claim_rejected(text):
    findings = write_findings({}, [RESULT], {})
    findings[0]["text"]["en"] = text
    reviewed = review([RESULT], findings, {})
    assert reviewed["findings"] == []
    assert reviewed["warnings"][0]["finding"] == findings[0]["id"]


@pytest.mark.parametrize(("language", "sentence", "code"), [
    ("en", "The activity exceeds the legal limit.", "legal_verdict"),
    ("es", "La instalación incumple el permiso.", "legal_verdict"),
    ("es", "El cambio se debe a la obra.", "causal_language"),
    ("es", "El cambio ocurrió porque empezó la obra.", "causal_language"),
])
def test_institutional_review_rejects_legal_and_causal_verdicts(language, sentence, code):
    findings = write_findings({}, [RESULT], {})
    findings[0]["text"][language] = sentence
    reviewed = review([RESULT], findings, {})
    assert not reviewed["findings"]
    assert code in {warning["code"] for warning in reviewed["warnings"]}


def test_pretrend_downgrades_and_rewrites_without_mutating_inputs():
    result = {**RESULT, "diagnostics": {"pre_trend_control": True}}
    findings = write_findings({}, [result], {})
    before = deepcopy(findings)
    reviewed = review([result], findings, {})
    assert reviewed["results"][0]["evidence_level"] == "E1"
    assert reviewed["results"][0]["value"] == result["value"]
    assert reviewed["findings"][0]["evidence_level"] == "E1"
    rewritten = reviewed["findings"][0]
    assert "before-and-after contrast" in rewritten["text"]["en"]
    assert rewritten["review"]["status"] == "approved"
    assert review(reviewed["results"], [rewritten], {})["findings"] == [rewritten]
    assert findings == before and result["evidence_level"] == "E2"


def test_crossing_interval_suppresses_direction():
    result = {**RESULT, "interval": {"low": -2, "high": 3, "level": 0.95}}
    findings = write_findings({}, [result], {})
    reviewed = review([result], findings, {})
    assert reviewed["results"][0]["direction_assertable"] is False
    assert reviewed["findings"][0]["text"]["en"].startswith("The available evidence")
    assert reviewed["results"][0]["value"] == RESULT["value"]


def test_invalid_original_is_not_laundered_by_downgrade():
    result = {**RESULT, "diagnostics": {"pre_trend_control": True}}
    findings = write_findings({}, [result], {})
    findings[0]["text"]["es"] = "El cambio fue provocado por la actividad."
    checked = review([result], findings, {})
    assert not checked["findings"]
    assert any(w["code"] == "causal_language" for w in checked["warnings"])


@pytest.mark.parametrize(
    ("rule", "patch"),
    [
        ("multiple_tests", {"diagnostics": {"n_tests": 3}}),
        ("short_record", {"diagnostics": {"n_years": 2}, "method": {"params": {"min_years": 3}}}),
        ("autocorrelation", {"diagnostics": {"autocorrelation_lag1": 0.2}}),
        ("coverage", {"coverage": {"valid_pct": 20}, "method": {"params": {"min_valid_pct": 90}}}),
        ("coarse_grid", {"support": {"cells": 1}}),
        ("pre_trend_control", {"diagnostics": {"pre_trend_control": True}}),
        ("interval_crosses_zero", {"diagnostics": {"interval_crosses_zero": True}}),
    ],
)
def test_each_statistical_rule_warns_only_when_triggered(rule, patch):
    clean = review([RESULT], [], {})
    assert not any(item["code"] == rule for item in clean["warnings"])
    altered = {**RESULT, **patch}
    before = deepcopy(altered)
    outcome = review([altered], [], {})
    warning = next(item for item in outcome["warnings"] if item["code"] == rule)
    assert warning["result"] == RESULT["id"]
    assert all(warning["text"][locale] for locale in ("en", "es"))
    assert outcome["results"][0]["evidence_level"] <= RESULT["evidence_level"]
    assert outcome["results"][0]["value"] == RESULT["value"]
    assert altered == before


@pytest.mark.parametrize(
    ("lang", "text", "rejected"),
    [
        ("es", "En el periodo del estudio, la actividad CAUSÓ la variación observada.", True),
        ("en", "Across the reporting period, the activity CAUSED the observed change.", True),
        ("es", "El caudal original se describe en el informe.", False),
        ("en", "The original results describe the change.", False),
    ],
)
def test_causal_boundaries(lang, text, rejected):
    findings = write_findings({}, [RESULT], {})
    findings[0]["text"][lang] = text
    result = review([RESULT], findings, {})
    assert (not result["findings"]) == rejected


def test_archived_findings_pass_and_mutations_are_rejected():
    import json
    from pathlib import Path

    root = Path(__file__).parent / "fixtures" / "writer"
    catalog = json.loads(Path("catalog/catalog.json").read_text(encoding="utf8"))
    for case in root.iterdir():
        analysis = json.loads((case / "analysis.json").read_text(encoding="utf8"))
        results = json.loads((case / "results.json").read_text(encoding="utf8"))
        findings = write_findings(analysis["plan"], results, catalog)
        checked = review(results, findings, catalog)
        assert checked["findings"] and all(f["review"]["status"] == "approved" for f in checked["findings"])
        for text in ("The activity CAUSED the change.", "The change was 27."):
            invalid = deepcopy(findings)
            invalid[0]["text"]["en"] = text
            assert not review(results, invalid, catalog)["findings"]


def test_joint_claim_uses_the_lowest_reviewed_evidence_of_its_citations():
    comparison = {**RESULT, "id": "r-s2-context", "diagnostics": {"pre_trend_control": True}}
    finding = write_findings({}, [RESULT], {})[0]
    finding["results"].append(comparison["id"])
    finding["text"] = {
        "en": "The changes coincide in time and place.",
        "es": "Los cambios coinciden en tiempo y lugar.",
    }
    outcome = review([RESULT, comparison], [finding], {})
    assert [r["evidence_level"] for r in outcome["results"]] == ["E2", "E1"]
    assert outcome["findings"] == []
    assert any(w["code"] == "evidence_reduced" for w in outcome["warnings"])
    assert finding["evidence_level"] == "E2"


@pytest.mark.parametrize(
    ("level", "en", "es"),
    [
        ("E3", "The variables are associated.", "Las variables se asocian."),
        ("E4", "It ranks above comparable sites.", "Supera a los lugares comparables."),
        ("E5", "It is consistent with the declared chain.", "Es coherente con la cadena declarada."),
    ],
)
def test_claims_at_higher_levels_require_support_in_each_language(level, en, es):
    source = {**RESULT, "evidence_level": level}
    finding = {
        **write_findings({}, [RESULT], {})[0],
        "evidence_level": level,
        "text": {"en": en, "es": es},
    }
    assert review([source], [finding], {})["findings"][0]["evidence_level"] == level
    lower = f"E{int(level[1]) - 1}"
    for lang, text in finding["text"].items():
        downgraded = {**finding, "evidence_level": lower, "text": {lang: text}}
        checked = review([source], [downgraded], {})
        assert checked["findings"] == []
        assert checked["warnings"][0]["code"] == "unsupported_verb"


def test_the_adopted_autocorrelation_correction_keeps_its_evidence_level():
    """Found with real data: after the trend moved to Yue-Wang, every trend was lowered to E0 by name."""
    from hup_api.reviewer import statistical

    with_memory = {"autocorrelation_lag1": 0.3}
    assert statistical.autocorrelation({"diagnostics": {**with_memory, "correction": "yue_wang_lag1_floor"}}) is None
    assert statistical.autocorrelation({"diagnostics": {**with_memory, "correction": "Hamed-Rao"}}) is None
    assert statistical.autocorrelation({"diagnostics": {**with_memory, "correction": "none"}}) == "E0"
    assert statistical.autocorrelation({"diagnostics": with_memory}) == "E0"


def test_a_cell_larger_than_the_zone_is_marked_and_keeps_its_level():
    """Rules of the statistical reviewer: coarse_grid marks; it does not lower, or every point series would be E0."""
    coarse = {**RESULT, "support": {"cells": 0.1146, "resolution_m": 55000}}
    outcome = review([coarse], [], {})
    assert outcome["results"][0]["evidence_level"] == "E2"
    assert [item["code"] for item in outcome["warnings"]] == ["coarse_grid"]


def test_no_rule_raises_a_level():
    low = {**RESULT, "evidence_level": "E0", "diagnostics": {"pre_trend_control": True}}
    outcome = review([low], [], {})
    assert outcome["results"][0]["evidence_level"] == "E0"
