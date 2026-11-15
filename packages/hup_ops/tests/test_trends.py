import numpy as np
import pytest

from hup_ops.registry import REGISTRY, Context


def series(values):
    return {
        "variable": "temperature", "unit": "degC", "data_class": "illustrative",
        "sources": [{"source": "synthetic", "url": "urn:hup:synthetic"}],
        "series": [{"t": str(1981 + i), "v": float(v)} for i, v in enumerate(values)],
    }


def test_noise_and_insufficient_years():
    context = Context({}, {}, 42, None, lambda *_: None)
    trend = REGISTRY["trend"].fn
    values = np.random.default_rng(context.seed).normal(size=44)
    result = trend({}, {"read": [series(values)]}, context)["trend"]
    assert result["statistic"]["p"] > 0.05
    assert result["interval"]["low"] <= 0 <= result["interval"]["high"]
    assert result["direction_assertable"] is False
    assert "value" not in result
    short = trend({}, {"read": [series(values[:8])]}, context)["trend"]
    assert short["absence"]["reason"] == "insufficient_qa"
    assert short["absence"]["detail"]["es"] == "Se necesita una serie continua de años completos."


def test_reference_scan():
    import json
    from pathlib import Path

    path = Path("exploracion/viabilidad/2026-10-31-sondeo-datos-reales/resultados/escaneo.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for row in data["resultados"]:
        entry = series(row["serie"])
        for year, point in enumerate(entry["series"], row["desde"]):
            point["t"] = str(year)
        entries.append(entry)
    context = Context({}, {}, 42, None, lambda *_: None)
    output = REGISTRY["scan_trends"].fn({}, {"read": entries}, context)
    diagnostics = output["scan"]["diagnostics"]
    assert diagnostics["n_tests"] == data["n_pruebas"]
    # The probe tested with Hamed-Rao; A5 uses C2s. Expected counts come from the C2s re-run of the same series.
    rerun = Path("exploracion/viabilidad/2026-11-13-intervalo-sen/escaneo_c2s.json")
    expected = json.loads(rerun.read_text(encoding="utf-8"))["totales"]["c2s"]
    assert diagnostics["n_significant_raw"] == expected["sin_corregir"]
    assert diagnostics["n_significant_adjusted"] == expected["tras_bh"]
    sample = next(i for i, row in enumerate(data["resultados"]) if row["var"] == "T2M")
    actual = output[f"trend-{sample}"]
    reference = data["resultados"][sample]
    # Sen's slope does not depend on the correction, so the probe's value still holds.
    assert abs(actual["value"] * 10 - reference["sen_decada"]) <= 0.0001
    assert actual["statistic"]["p_adjusted"] < 0.05


# The plain Yue-Wang variance can be negative on short noisy series; pymannkendall then warns. C2s never uses it.
@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_c2s_never_shrinks_and_matches_yue_wang():
    import pymannkendall as mk

    from hup_ops._mk import c2s

    rng = np.random.default_rng(7)
    matched = 0
    for _ in range(200):
        values = 0.02 * np.arange(20) + rng.normal(size=20)
        p, _, var_s = c2s(values)
        classic = mk.original_test(values).var_s
        corrected = mk.yue_wang_modification_test(values, lag=1)
        assert var_s >= classic
        if corrected.var_s >= classic:
            assert p == pytest.approx(corrected.p, abs=1e-12)
            matched += 1
    assert matched > 0


def test_nine_years_and_limits():
    context = Context({}, {}, 42, None, lambda *_: None)
    trend = REGISTRY["trend"].fn
    values = np.random.default_rng(3).normal(size=9)
    result = trend({}, {"read": [series(values)]}, context)["trend"]
    assert "statistic" in result
    assert result["diagnostics"]["correction"] == "yue_wang_lag1_floor"
    assert any("15 years" in limit["en"] for limit in result["limits"])
    # The memory limit is declared for every tested series: nine years cannot show how much memory there is.
    assert any("memory" in limit["en"] for limit in result["limits"])
    memory = np.zeros(44)
    noise = np.random.default_rng(11).normal(size=44)
    for i in range(1, 44):
        memory[i] = 0.8 * memory[i - 1] + noise[i]
    long = trend({}, {"read": [series(memory)]}, context)["trend"]
    assert long["diagnostics"]["autocorrelation_lag1"] >= 0.3
    assert any("memory" in limit["en"] and "memoria" in limit["es"] for limit in long["limits"])
    assert not any("15 years" in limit["en"] for limit in long["limits"])

def test_anomaly_and_final_incomplete_year():
    context = Context({}, {"start": "1981-01-01", "end": "2024-06-30"}, 42, None, lambda *_: None)
    entries = {"read": [series(np.arange(44))]}
    result = REGISTRY["trend"].fn({}, entries, context)["trend"]
    assert result["diagnostics"]["n_years"] == 43
    assert result["value"] == 1
    assert result["limits"]
    params = {"baseline": {"start": "1981-01-01", "end": "1982-12-31"}}
    anomaly = REGISTRY["anomaly"].fn(params, entries, context)["anomaly"]
    assert [point["v"] for point in anomaly["series"][:3]] == [-0.5, 0.5, 1.5]

def test_scan_reports_only_adjusted_probabilities():
    ctx = Context({}, {}, 42, None, lambda *_: None)
    entries = [series(np.arange(44)), series(np.arange(44)[::-1])]
    results = REGISTRY["scan_trends"].fn({}, {"read": entries}, ctx)
    assert results["scan"]["diagnostics"]["n_tests"] == len(entries)
    for result in (results["trend-0"], results["trend-1"]):
        assert "p" not in result["statistic"]
        assert 0 <= result["statistic"]["p_adjusted"] <= 1

def test_scan_direction_is_a_python_boolean():
    ctx = Context({}, {}, 42, None, lambda *_: None)
    outputs = REGISTRY["scan_trends"].fn({}, {"read": [series(np.arange(44))]}, ctx)
    assert outputs["trend-0"]["direction_assertable"] is True


def test_a_southern_summer_ends_in_march():
    trend = REGISTRY["trend"].fn
    entry = series(np.arange(12, dtype=float))
    entry["period"] = {"start": "1981-01-01", "end": "1992-03-31", "months": [12, 1, 2, 3]}
    context = Context({}, entry["period"], 42, None, lambda *_: None)
    result = trend({}, {"read": [entry]}, context)["trend"]
    assert result["diagnostics"]["n_years"] == 12  # the summer of 1992 ended on 31 March: it is complete
    entry["period"]["end"] = "1992-02-15"
    result = trend({}, {"read": [entry]}, context)["trend"]
    assert result["diagnostics"]["n_years"] == 11 and result["limits"]
