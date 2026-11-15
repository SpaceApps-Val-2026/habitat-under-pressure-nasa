import numpy as np
import pytest

from hup_ops import association
from hup_ops.registry import REGISTRY, Context

CTX = Context({}, {}, 42, None, lambda *_: None)
PARAMS = {"lags": [0, 1], "resamples": 199}


def sample(linked):
    rng = np.random.default_rng(CTX.seed)
    trend = np.arange(100) * 10
    x, y, regional = rng.normal(size=(3, len(trend)))
    if linked:
        y[1:] = x[:-1]

    def series(variable, values):
        return {"variable": variable, "series": [{"t": str(year), "v": float(v)}
                                                for year, v in enumerate(values, 1900)]}

    return {"from": [series("x", x + trend)], "to": [series("y", y + trend)],
            "regional": [series("region", regional + trend)]}


def test_association_removes_common_trend_and_recovers_lag():
    op = REGISTRY["lagged_association"].fn
    independent = op(PARAMS, sample(False), CTX)
    assert all(not result["direction_assertable"] for key, result in independent.items() if key != "association")
    linked = op(PARAMS, sample(True), CTX)
    assert linked["lag-1"]["value"] > 0.9
    assert linked["lag-1"]["statistic"]["p_adjusted"] < 0.05
    assert linked["lag-0"]["direction_assertable"] is False


def test_chain_requires_reference():
    chain = {"id": "test", "links": [{"from": "x", "to": "y", "sign": "+", "lag_years": [1, 1], "ref": None}]}
    op = REGISTRY["evaluate_chain"].fn
    result = op({"chain": chain}, sample(True), CTX)["chain"]
    assert result["table"]["rows"][0][2] == "not_testable"
    assert result["evidence_level"] != "E5"
    chain["links"][0]["ref"] = "urn:hup:synthetic"
    result = op({"chain": chain, "resamples": 199}, sample(True), CTX)["chain"]
    assert result["table"]["rows"][0][2] == "met"
    assert result["evidence_level"] == "E5"


def test_association_resampling_is_reproducible_and_minimum_is_reported():
    op = REGISTRY["lagged_association"].fn
    entries = sample(True)
    assert op(PARAMS, entries, CTX) == op(PARAMS, entries, CTX)
    result = op({**PARAMS, "min_years": 101}, entries, CTX)["association"]
    assert result["absence"]["reason"] == "insufficient_qa"
    assert result["method"]["params"]["min_years"] == 101


def test_chain_uses_the_outer_ring_and_passes_minimum_to_its_link(monkeypatch):
    entries = sample(True)
    local = [dict(entries[key][0], method={"params": {"area": "zone"}}, support={"resolution_m": 100})
             for key in ("from", "to")]
    controls = [{**entries["regional"][0], "variable": "y", "support": {"resolution_m": 100},
                 "method": {"params": {"area": "ring", "ring_from_km": low, "ring_to_km": high}}}
                for low, high in ((0, 3), (3, 20))]
    chain = {"id": "test", "links": [{"from": "x", "to": "y", "sign": "+", "lag_years": [1, 1],
                                    "ref": "urn:hup:synthetic"}]}
    params = {"chain": chain, "regional": "y", "regional_ring": [3, 20], "min_years": 19}

    def evaluate(settings, inputs, ctx):
        assert settings["min_years"] == 19
        assert inputs["regional"] == [controls[1]]
        return {"association": {"table": {"rows": [[1, 0.98, 0.9, 1.0, 0.001, "tested"]]}}}

    monkeypatch.setattr(association, "lagged_association", evaluate)
    result = association.evaluate_chain(params, {"site": local, "rings": controls}, CTX)["chain"]
    assert result["table"]["rows"][0][2] == "met" and result["support"] == {"resolution_m": 100}
    result = association.evaluate_chain({**params, "min_years": 101}, {"site": local, "rings": controls}, CTX)["chain"]
    row, = result["table"]["rows"]
    assert row[2] == "not_testable" and "Insufficient paired years" in row[3] and "insuficientes" in row[-1]


RING_LIMIT = ("The control does not exclude other construction works inside the ring.",
              "El control no excluye otras obras dentro del anillo.")
CITATION_LIMIT = ("The published reference supports the sign of the relation by comparing sites within the same "
                  "summer month. On its own it does not support comparing years at the same place.",
                  "La referencia publicada sostiene el signo de la relación comparando sitios dentro de un mismo "
                  "mes de verano. Por sí sola no sostiene la comparación entre años en un mismo lugar.")
PRADES_GIL_REF = "Prades-Gil et al. 2024, doi:10.1016/j.ecmx.2024.100706, pp. 7-8"


def has(limits, pair):
    return any(pair[0] in limit["en"] and pair[1] in limit["es"] for limit in limits)


def greenness(ref="urn:hup:synthetic"):
    return {"id": "greenness_heat", "links": [{"from": "x", "to": "y", "sign": "-", "lag_years": [0, 0], "ref": ref}]}


def test_chain_ring_limit_says_the_control_does_not_exclude_other_works():
    entries = sample(True)
    local = [dict(entries[key][0], method={"params": {"area": "zone"}}, support={"resolution_m": 100})
             for key in ("from", "to")]
    ring = {**entries["regional"][0], "variable": "y", "support": {"resolution_m": 100},
            "method": {"params": {"area": "ring", "ring_from_km": 3, "ring_to_km": 20}}}
    params = {"chain": greenness(), "regional": "y", "regional_ring": [3, 20], "min_years": 101}
    result = REGISTRY["evaluate_chain"].fn(params, {"site": local, "rings": [ring]}, CTX)["chain"]
    assert result["table"]["rows"][0][2] == "not_testable"
    assert has(result["limits"], RING_LIMIT)
    assert result["method"]["version"] == REGISTRY["evaluate_chain"].version == "1.4"


def test_prades_gil_citation_limit_follows_the_link_even_when_not_testable():
    op = REGISTRY["evaluate_chain"].fn
    vegetation_heat = {"from": "ndvi", "to": "lst", "sign": "-", "lag_years": [0, 0], "ref": PRADES_GIL_REF}
    for chain_id, reference in (("greenness_heat", PRADES_GIL_REF),
                                ("sealing_heat", "https://doi.org/10.1016/j.ecmx.2024.100706")):
        chain = {"id": chain_id, "links": [{**vegetation_heat, "ref": reference}]}
        result = op({"chain": chain}, {}, CTX)["chain"]
        assert result["table"]["rows"][0][2] == "not_testable"
        assert has(result["limits"], CITATION_LIMIT)

    for link in ({**vegetation_heat, "ref": None},
                 {**vegetation_heat, "ref": "https://doi.org/10.1016/j.ecmx.2024.1007068"},
                 {**vegetation_heat, "to": "air_temperature"},
                 {**vegetation_heat, "sign": "+"}):
        chain = {"id": "greenness_heat", "links": [link]}
        result = op({"chain": chain}, {}, CTX)["chain"]
        assert not has(result["limits"], CITATION_LIMIT)


def test_greenness_heat_low_power_limit_says_no_lag():
    entries = sample(False)
    short = {role: [dict(entry, series=entry["series"][:12])] for role, [entry] in entries.items()}
    n = len(short["from"][0]["series"])
    params = {"chain": greenness(), "min_years": n - 2, "resamples": 199}
    result = REGISTRY["evaluate_chain"].fn(params, short, CTX)["chain"]
    assert result["table"]["rows"][0][2] in {"met", "not_met"}
    assert has(result["limits"], (f"With {n} years and no lag, the test reports a spurious relation",
                                  f"Con {n} años y solo el mismo año (sin desfase), la prueba da una relación falsa"))
    assert not any("lag of" in limit["en"] or "desfase de" in limit["es"] or "matched summers" in limit["en"]
                   for limit in result["limits"])


def summer_inputs(gaps=(), local_gaps=(), constant=False):
    years = range(2013, 2026)
    signal = [1, 7, 4, 2, 9, 5, 3, 8, 6, 0, 10, 12, 11]
    control = [8, 1, 4, 6, 10, 2, 3, 11, 9, 7, 5, 0, 12]

    def entry(variable, values, missing, **params):
        return {
            "variable": variable, "period": {"months": [6, 7, 8, 9]},
            "method": {"params": {"aggregate": "year", "require_complete_months": True, **params}},
            "series": [{"t": str(year), "v": 1.0 if constant else float(value)}
                       for year, value in zip(years, values, strict=True) if year not in missing],
            "support": {"resolution_m": 100},
        }

    return {
        "from": [entry("ndvi", signal, local_gaps, area="zone")],
        "to": [entry("lst", [-value for value in signal], (), area="zone")],
        "regional": [entry("lst", control, gaps, area="ring", ring_from_km=3, ring_to_km=20)],
    }


def chain_params(**params):
    chain = {"id": "greenness_heat", "links": [{"from": "ndvi", "to": "lst", "sign": "-", "lag_years": [0, 0],
                                               "ref": "urn:hup:synthetic"}]}
    return {"chain": chain, "regional_ring": [3, 20], "regional": "lst", "min_years": 10, **params}


def test_chain_distinguishes_the_missing_regional_year_from_no_residual_variation():
    broken = association.evaluate_chain(chain_params(), summer_inputs(gaps=(2020,), constant=True), CTX)["chain"]
    _, _, status, en, _, es = broken["table"]["rows"][0]
    assert status == "not_testable"
    assert "enough paired summers" in en and "The regional control (LST) lacks data for 2020." in en
    assert "veranos emparejados" in es and "Faltan datos de 2020 en la serie del control regional (LST)." in es
    assert {"en": en, "es": es} in broken["limits"]
    complete = association.evaluate_chain(chain_params(), summer_inputs(constant=True), CTX)["chain"]
    _, _, status, en, _, es = complete["table"]["rows"][0]
    assert status == "not_testable"
    assert "enough consecutive years" in en and "residual variation" in en
    assert "suficientes años consecutivos" in es and "variación residual" in es


@pytest.mark.parametrize("gaps", [(2020,), (2020, 2021)])
def test_complete_summer_association_allows_gaps_in_the_regional_control(gaps):
    inputs = summer_inputs(gaps=gaps)
    params = {"min_years": 10, "allow_regional_gaps": True}
    tested = association.lagged_association(params, inputs, CTX)
    assert tested["lag-0"]["value"] == pytest.approx(-1)
    n = 13 - len(gaps)
    assert tested["lag-0"]["statistic"]["n"] == n
    gap_limit = next(limit for limit in tested["association"]["limits"] if "complete summers" in limit["en"])
    assert f"{n} of 13" in gap_limit["en"] and f"{n} de 13" in gap_limit["es"]
    assert all(str(year) in gap_limit["en"] and str(year) in gap_limit["es"] for year in gaps)
    chain = association.evaluate_chain(chain_params(), inputs, CTX)["chain"]
    assert chain["table"]["rows"][0][2] == "met" and chain["evidence_level"] == "E5"
    assert gap_limit in chain["limits"]
    assert "absence" in association.lagged_association({"min_years": 10}, inputs, CTX)["association"]


@pytest.mark.parametrize(("gaps", "local_gaps", "options", "reason"), [
    ((2020, 2021, 2022), (), {}, "at most two missing summers"),
    ((2016, 2020), (), {}, "one gap"),
    ((2015,), (), {}, "at least three consecutive years"),
    ((), (2020,), {}, "complete local series"),
    ((2017, 2018, 2019, 2020), (), {}, "9 are available"),
    ((2020,), (), {"block_years": 2}, "default test settings"),
])
def test_chain_declines_gaps_outside_its_validated_scope(gaps, local_gaps, options, reason):
    inputs = summer_inputs(gaps=gaps, local_gaps=local_gaps)
    result = association.evaluate_chain(chain_params(**options), inputs, CTX)["chain"]
    row, = result["table"]["rows"]
    assert row[2] == "not_testable" and reason in row[3] and row[5]
    assert result["evidence_level"] == "E0"


def test_gap_rule_excludes_lag_one_other_links_and_previous_annual_aggregates():
    inputs = summer_inputs(gaps=(2020,))
    sampled = association.lagged_association({"min_years": 10, "allow_regional_gaps": True, "lags": [1]}, inputs, CTX)
    assert "absence" in sampled["association"]
    params = chain_params()
    params["chain"]["id"] = "sealing_heat"
    result = association.evaluate_chain(params, inputs, CTX)["chain"]
    assert result["table"]["rows"][0][2] == "not_testable"
    del inputs["regional"][0]["method"]["params"]["require_complete_months"]
    result = association.evaluate_chain(chain_params(), inputs, CTX)["chain"]
    row, = result["table"]["rows"]
    assert row[2] == "not_testable" and "every summer month" in row[3]


def test_resampled_blocks_stay_in_their_segment():
    segments = [np.arange(7), np.arange(7, 11)]
    indices = np.array(list(association._bootstrap_indices(segments, 3, 99, np.random.default_rng(1))))
    assert indices.shape == (99, 11)
    assert (indices[:, :7] < 7).all() and (indices[:, 7:] >= 7).all()
    for start in (0, 3, 7):
        assert (np.diff(indices[:, start:start + 3], axis=1) == 1).all()
