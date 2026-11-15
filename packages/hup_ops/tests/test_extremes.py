import numpy as np
import pytest
from scipy.stats import genextreme

from hup_ops.extremes import fit, level, period
from hup_ops.registry import REGISTRY, Context

CTX = Context({}, {}, 42, None, lambda *_: None)
# scipy's shape c is Hosking's k: c = -xi. A heavy tail like that of Mediterranean daily rain: xi = 0.2.
TRUE = {"c": -0.2, "loc": 40.0, "scale": 15.0}


def series(values, start=1998, t=str):
    return {"read": [{
        "variable": "pr_max_1day_observed", "unit": "mm", "data_class": "illustrative",
        "sources": [{"source": "synthetic", "url": "urn:hup:synthetic"}],
        "series": [{"t": t(start + i), "v": float(v)} for i, v in enumerate(values)],
    }]}


def sample(n, seed=3):
    return genextreme.rvs(size=n, random_state=np.random.default_rng(seed), **TRUE)


def test_level_and_period_are_the_gev_quantile_and_its_inverse():
    u, a, k = 40.0, 15.0, -0.2
    for years in (2, 10, 100):
        expected = genextreme.ppf(1 - 1 / years, k, loc=u, scale=a)
        assert level(u, a, k, years) == pytest.approx(expected)
        assert period(expected, u, a, k) == pytest.approx(years)
    assert period(1e6, 40.0, 15.0, 0.3) == np.inf  # above the upper end of a bounded fit


def test_l_moments_recover_a_known_distribution_with_a_long_record():
    u, a, k = (float(v[0]) for v in fit(sample(5000)))
    assert u == pytest.approx(40, abs=1) and a == pytest.approx(15, abs=1) and -k == pytest.approx(0.2, abs=0.05)
    result = REGISTRY["return_level"].fn({}, series(sample(400)), CTX)["level_10"]
    truth = genextreme.ppf(0.9, **TRUE)
    assert result["value"] == pytest.approx(truth, rel=0.1)
    assert result["interval"]["low"] <= truth <= result["interval"]["high"]
    assert result["unit"] == "mm" and result["evidence_level"] == "E0"
    assert result["diagnostics"]["n_years"] == 400


def test_same_seed_same_result_and_the_seed_only_moves_the_interval():
    data = series(sample(27))
    op = REGISTRY["return_level"].fn
    first, again = op({}, data, CTX), op({}, data, CTX)
    assert first == again
    other = op({}, data, Context({}, {}, 7, None, lambda *_: None))
    assert other["level_10"]["value"] == first["level_10"]["value"]
    assert other["level_10"]["interval"] != first["level_10"]["interval"]
    assert set(first) == {"level_2", "level_5", "level_10", "level_25"}


def test_too_few_years_constant_series_and_non_annual_series_are_absences():
    op = REGISTRY["return_level"].fn
    short = op({}, series(sample(19)), CTX)
    assert all(r["absence"]["reason"] == "insufficient_qa" for r in short.values())
    assert short["level_2"]["absence"]["detail"]["es"] == "Se necesitan al menos 20 años con valores distintos."
    flat = op({}, series([30.0] * 25), CTX)
    assert all("absence" in r for r in flat.values())
    monthly = op({}, series(sample(30), t=lambda i: f"2000-{i % 12 + 1:02d}"), CTX)
    assert all("absence" in r for r in monthly.values())


def test_a_return_period_longer_than_the_record_is_not_estimated():
    results = REGISTRY["return_level"].fn({}, series(sample(22)), CTX)
    assert "value" in results["level_10"]
    assert results["level_25"]["absence"]["reason"] == "not_computed"
    assert "22" in results["level_25"]["absence"]["detail"]["es"]


def test_an_episode_is_set_against_the_years_before_it():
    values = list(sample(27))  # 1998-2024
    op = REGISTRY["return_level"].fn
    values[-1] = float(np.median(values[:-1]))  # an ordinary 2024
    ordinary = op({"event_year": 2024}, series(values), CTX)
    event = ordinary["event"]
    assert event["unit"] == "years" and event["diagnostics"]["n_years"] == 26
    assert 1 < event["value"] < 4 and event["interval"]["low"] < event["value"] < event["interval"]["high"]
    # The levels are fitted on the same 26 years: the episode never enters its own reference.
    assert ordinary["level_10"]["value"] == op({}, series(values[:-1]), CTX)["level_10"]["value"]
    values[-1] = 10 * max(values[:-1])
    rare = op({"event_year": 2024}, series(values), CTX)["event"]
    assert rare["absence"]["reason"] == "not_computed"
    assert rare["diagnostics"]["event_value"] == values[-1]
    assert rare["diagnostics"]["record_level"] < values[-1]
    missing = op({"event_year": 2030}, series(values), CTX)["event"]
    assert missing["absence"]["detail"]["es"] == "2030 no tiene valor en la serie."


def test_an_episode_at_the_edge_of_the_record_gives_neither_a_figure_nor_rarer_than_the_record():
    """Review of A11: the point estimate alone said "rarer than the record" with a third of the bootstrap below it,
    and an unbounded interval left the result without one, which broke the writer."""
    op = REGISTRY["return_level"].fn
    history = list(sample(26))
    u, a, k = (float(v[0]) for v in fit(history))
    edge = op({"event_year": 2024}, series(history + [1.05 * float(level(u, a, k, 26))]), CTX)["event"]
    assert edge["absence"]["reason"] == "insufficient_qa" and "value" not in edge
    assert "no se puede afirmar" in edge["absence"]["detail"]["es"]
    bounded = list(genextreme.rvs(0.25, loc=40, scale=10, size=26, random_state=np.random.default_rng(1)))
    for result in op({"event_year": 2024}, series(bounded + [max(bounded)]), CTX).values():
        assert "absence" in result or "interval" in result  # a figure always comes with its interval


def daily(values: dict[str, float], status=None):
    entry = {"variable": "precipitation_daily", "unit": "mm/day", "data_class": "illustrative",
             "sources": [{"source": "synthetic", "url": "urn:hup:synthetic"}],
             "series": [{"t": t, "v": v} for t, v in values.items()]}
    if status:  # a read that came back empty
        entry = {**entry, "series": [], "absence": {"reason": status, "detail": {"en": "none"}}}
    return {"episode": [entry]}


def test_with_the_daily_series_of_an_episode_its_wettest_day_is_set_against_the_whole_years_before():
    """Review of A11: the year read up to the day of the episode gave the maximum from 1 January to that day, not
    the episode. With its daily series, the value is the wettest day of the episode."""
    op = REGISTRY["return_level"].fn
    history = list(sample(26))  # 1998-2023, whole years
    wettest = float(np.median(history))
    days = {"2024-10-28": 3.0, "2024-10-29": wettest, "2024-10-30": 1.0}
    got = op({"event_year": 2024}, {**series(history), **daily(days)}, CTX)
    event = got["event"]
    assert event["diagnostics"]["event_value"] == wettest and event["diagnostics"]["event_day"] == "2024-10-29"
    assert event["diagnostics"]["n_years"] == 26 and 1 < event["value"] < 4
    assert event["variable"] == "pr_max_1day_observed" and got["level_10"]["unit"] == "mm"
    assert got["level_10"]["value"] == op({}, series(history), CTX)["level_10"]["value"]
    # The annual series may hold its year too (an older plan): the daily series still gives the episode.
    with_year = op({"event_year": 2024}, {**series(history + [99 * wettest]), **daily(days)}, CTX)["event"]
    assert with_year["diagnostics"]["event_value"] == wettest
    without = op({"event_year": 2024}, series(history + [wettest]), CTX)["event"]  # as before, the value of the year
    assert without["diagnostics"]["event_value"] == wettest and "event_day" not in without["diagnostics"]
    empty = op({"event_year": 2024}, {**series(history), **daily({}, "no_coverage")}, CTX)
    assert empty["event"]["absence"]["detail"]["es"] == "La serie diaria del episodio no tiene valores en 2024."
    assert "value" in empty["level_10"]  # the levels do not need the episode
    alone = op({}, {**series(history), **daily(days)}, CTX)  # a daily series without an episode is not accepted
    assert all("absence" in result for result in alone.values())
