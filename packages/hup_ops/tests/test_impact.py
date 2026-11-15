import numpy as np
import pytest

from hup_ops.registry import REGISTRY, Context

CTX = Context({}, {}, 42, None, lambda *_: None)
WINDOWS = {"before": [2000, 2009], "after": [2010, 2019]}


def series(values):
    return {
        "variable": "ndvi", "unit": "1", "data_class": "illustrative",
        "sources": [{"source": "synthetic", "url": "urn:hup:synthetic"}],
        "series": [{"t": str(year), "v": float(v)} for year, v in enumerate(values, 2000)],
    }


@pytest.mark.parametrize("shift", [0, 4])
def test_baci_step(shift):
    noise = np.random.default_rng(CTX.seed).normal(scale=0.1, size=20)
    values = noise + np.repeat([0, shift], 10)
    inputs = {"site": [series(values)], "reference": [series(noise)]}
    result = REGISTRY["baci_contrast"].fn(WINDOWS, inputs, CTX)["contrast"]
    assert result["direction_assertable"] is bool(shift)
    if shift:
        assert result["value"] == pytest.approx(shift)
    else:
        assert result["interval"]["low"] <= 0 <= result["interval"]["high"]


def test_placebo_step():
    rng = np.random.default_rng(CTX.seed)
    controls = [series(rng.normal(scale=0.1, size=20)) for _ in range(40)]
    case = series(np.repeat([0, 4], 10))
    inputs = {"site": [case], "reference": [series(np.zeros(20))], "controls": controls}
    result = REGISTRY["placebo_test"].fn(
        {**WINDOWS, "control_criteria": "Synthetic controls with no step"}, inputs, CTX,
    )["placebo"]
    assert result["value"] > 95
    assert result["diagnostics"]["n_controls"] == 40
    params = {**WINDOWS, "control_criteria": "Synthetic controls with no step"}
    assert result == REGISTRY["placebo_test"].fn(params, inputs, CTX)["placebo"]
    missing = REGISTRY["placebo_test"].fn({**params, "min_controls": 41}, inputs, CTX)["placebo"]
    assert missing["absence"]["reason"] == "insufficient_qa"

def test_placebo_with_temporal_false_events_only():
    noise = np.random.default_rng(CTX.seed).normal(scale=0.1, size=20)
    inputs = {"site": [series(noise + np.repeat([0, 4], [16, 4]))], "reference": [series(np.zeros(20))]}
    events = [{"before": [y, y + 2], "after": [y + 3, y + 5]} for y in range(2000, 2008)]
    params = {"before": [2013, 2015], "after": [2016, 2018], "control_criteria": "Temporal only",
              "min_controls": 0, "false_events": events}
    fn = REGISTRY["placebo_test"].fn
    assert REGISTRY["placebo_test"].version == "1.1"
    result = fn(params, {**inputs, "controls": []}, CTX)["placebo"]
    assert result["value"] == 100
    assert result["evidence_level"] == "E2"
    assert result["diagnostics"] == {"n_controls": 0, "control_criteria": "Temporal only", "n_false_events": 8}
    assert {"en": "Placebo from 8 temporal false events only, with no spatial controls.",
            "es": "Placebo solo con 8 hitos falsos temporales, sin controles espaciales."} in result["limits"]
    empty = fn({**params, "false_events": []}, inputs, CTX)["placebo"]
    assert empty["absence"]["reason"] == "insufficient_qa"
    assert "value" not in empty and set(empty["absence"]["detail"]) == {"en", "es"}


def test_placebo_generates_false_events_with_the_shape_of_the_case():
    def from_year(first):
        return {**series(np.zeros(2026 - first)), "series": [
            {"t": f"{year}-07-01", "v": 0.0} for year in range(first, 2026)]}

    params = {"before": [2019, 2021], "after": [2023, 2025], "control_criteria": "Temporal only",
              "min_controls": 0}
    fn = REGISTRY["placebo_test"].fn
    result = fn(params, {"site": [from_year(2013)], "reference": [from_year(2013)]}, CTX)["placebo"]
    assert result["method"]["params"]["false_events"] == [
        {"before": [2013, 2015], "after": [2017, 2019]},
        {"before": [2014, 2016], "after": [2018, 2020]},
        {"before": [2015, 2017], "after": [2019, 2021]},
    ]
    assert result["diagnostics"]["n_false_events"] == 3
    passed = [{"before": [2019, 2019], "after": [2020, 2020]}]
    kept = fn({**params, "false_events": passed}, {"site": [from_year(2013)], "reference": [from_year(2013)]}, CTX)
    assert kept["placebo"]["method"]["params"]["false_events"] == passed
    late = fn(params, {"site": [from_year(2019)], "reference": [from_year(2019)]}, CTX)["placebo"]
    assert late["absence"]["reason"] == "insufficient_qa"
    assert late["method"]["params"]["false_events"] == []


def test_break_year():
    entry = series(np.repeat([0, 4], 10))
    result = REGISTRY["break_year"].fn({}, {"read": [entry]}, CTX)["break"]
    assert result["value"] == 2010
    assert result["interval"]["low"] == result["interval"]["high"] == 2010
    assert result == REGISTRY["break_year"].fn({}, {"read": [entry]}, CTX)["break"]
    missing = REGISTRY["break_year"].fn({"min_dates": 11}, {"read": [entry]}, CTX)["break"]
    assert missing["absence"]["reason"] == "insufficient_qa"


def test_distance_decay():
    reference = series(np.zeros(20))
    rings = [series(np.repeat([0, shift], 10)) for shift in (4, 2)]
    inputs = {"rings": rings, "reference": [reference]}
    result = REGISTRY["distance_decay"].fn(
        {**WINDOWS, "bands_m": [[0, 250], [250, 500]]}, inputs, CTX,
    )
    assert [row[2] for row in result["decay"]["table"]["rows"]] == [4, 2]

def test_baci_bootstrap_uses_only_unique_shared_dates():
    site = series(np.repeat([0, 4], 10))
    reference = series(np.zeros(20))
    inputs = {"site": [site], "reference": [reference]}
    fn = REGISTRY["baci_contrast"].fn
    original = fn(WINDOWS, inputs, CTX)
    assert original["contrast"]["diagnostics"]["n_dates"] == 20
    assert original == fn(WINDOWS, inputs, CTX)
    site["series"].append({"t": "2005-07-01", "v": 100})
    reference["series"].append({"t": "2015-07-01", "v": -100})
    assert original == fn(WINDOWS, inputs, CTX)


@pytest.mark.parametrize("area, en, es", [("reference", "Reference", "Referencia"),
                                          ("site", "Analysis area", "Zona de análisis")])
def test_baci_passes_on_the_absence_of_an_input_in_both_languages(area, en, es):
    missing = {**series(np.zeros(20)), "absence": {"reason": "insufficient_qa", "detail": {
        "en": "No scene was clear enough over this zone in this period.",
        "es": "Ninguna escena estaba lo bastante despejada sobre esta zona en este periodo."}}}
    inputs = {"site": [series(np.zeros(20))], "reference": [series(np.zeros(20))]}
    inputs[area] = [missing]
    absence = REGISTRY["baci_contrast"].fn(WINDOWS, inputs, CTX)["contrast"]["absence"]
    assert absence["reason"] == "insufficient_qa"
    assert absence["detail"] == {"en": f"{en}: No scene was clear enough over this zone in this period.",
                                 "es": f"{es}: Ninguna escena estaba lo bastante despejada sobre esta zona en "
                                       "este periodo."}


def test_baci_without_enough_paired_dates_says_so_in_both_languages():
    inputs = {"site": [series(np.zeros(2))], "reference": [series(np.zeros(2))]}
    detail = REGISTRY["baci_contrast"].fn(WINDOWS, inputs, CTX)["contrast"]["absence"]["detail"]
    assert set(detail) == {"en", "es"}
