import pandas as pd
import pytest

from hup_ops.registry import REGISTRY, Context

CTX = Context({}, {}, 42, None, lambda *_: None)
PARAMS = {"reference": [2000, 2000], "windows": {"2030": [2030, 2030]}}


def test_delta_and_variance():
    rows = [
        (year, 100 + model * 20 + (2 * model + 4 * scenario if year == 2030 else 0), str(model), f"ssp{scenario + 1}26")
        for model in range(2)
        for scenario in range(2)
        for year in (2000, 2030)
    ]
    cube = {"unit": "degC", "data": pd.DataFrame(rows, columns=["t", "v", "model", "scenario"])}
    observed = {"unit": "degC", "series": [{"t": "2000", "v": 10}]}
    inputs = {"read": [cube, observed]}
    delta = REGISTRY["climate_delta"].fn(PARAMS, inputs, CTX)["delta"]
    assert [p["v"] for p in delta["series"]] == [11, 15]
    assert delta["diagnostics"]["n_models"] == 2
    split = REGISTRY["uncertainty_split"].fn({}, {"delta": [delta]}, CTX)["uncertainty"]
    assert split["table"]["rows"][0][:3] == [2030, 1, 4]
    cube["data"] = cube["data"][cube["data"]["model"] == "0"]
    delta = REGISTRY["climate_delta"].fn(PARAMS, inputs, CTX)["delta"]
    split = REGISTRY["uncertainty_split"].fn({}, {"delta": [delta]}, CTX)["uncertainty"]
    assert split["absence"]["reason"] == "not_computed"


def test_delta_bounds_are_the_lowest_and_highest_model():
    # Guillem, 14 Nov (T1 of A8): the fan goes from the lowest to the highest model, not between quantiles
    changes = {"a": 1, "b": 2, "c": 3, "d": 4, "e": 10}
    cube = {"series": [
        row for model, change in changes.items()
        for row in ({"t": "2000", "v": 100, "model": model, "scenario": "historical"},
                    {"t": "2030", "v": 100 + change, "model": model, "scenario": "ssp245"})
    ]}
    observed = {"series": [{"t": "2000", "v": 10}]}
    for params in (PARAMS, {**PARAMS, "confidence": 0.5}):  # the removed parameter, if a plan still passes it
        result = REGISTRY["climate_delta"].fn(params, {"read": [cube, observed]}, CTX)["delta"]
        point = result["series"][0]
        assert (point["low"], point["v"], point["high"]) == (11, 13, 20)
    assert "confidence" not in REGISTRY["climate_delta"].fn(PARAMS, {"read": [cube, observed]}, CTX)["delta"][
        "method"]["params"]


def test_project_trend():
    entry = {"series": [{"t": str(year), "v": 2 * year} for year in range(2000, 2020)]}
    operation = REGISTRY["project_trend"].fn
    params = {"variable_kind": "non_climate", "horizons": [2030]}
    point = operation(params, {"read": [entry]}, CTX)["projection"]["series"][0]
    assert point["low"] == point["v"] == point["high"] == 2 * 2030
    assert "absence" in operation({}, {"read": [entry]}, CTX)["projection"]


def test_delta_accepts_ingested_series_with_separate_history():
    def entry(rows):
        return {"variable": "test", "unit": "degC", "series": rows}

    observed = entry([{"t": "2000", "v": 10}])
    historical = entry([{"t": "2000", "v": 100, "model": "m", "scenario": "historical"}])
    future = entry([{"t": "2030", "v": 103, "model": "m", "scenario": "ssp245"}])
    result = REGISTRY["climate_delta"].fn(PARAMS, {"read": [future, observed, historical]}, CTX)["delta"]
    assert result["series"][0]["v"] == 13


def test_single_model_declares_name_and_uncertainty_is_absent():
    entry = {
        "unit": "degC",
        "series": [
            {"t": "2000", "v": 100, "model": "synthetic", "scenario": "historical"},
            {"t": "2030", "v": 103, "model": "synthetic", "scenario": "ssp245"},
        ],
    }
    observed = {"unit": "degC", "series": [{"t": "2000", "v": 10}]}
    delta = REGISTRY["climate_delta"].fn(PARAMS, {"read": [entry, observed]}, CTX)["delta"]
    assert delta["diagnostics"] == {"n_models": 1, "models": "synthetic"}
    result = REGISTRY["uncertainty_split"].fn({}, {"delta": [delta]}, CTX)["uncertainty"]
    assert result["absence"]["reason"] == "not_computed"


def test_partial_reference_reports_coverage_separately_from_compatibility():
    cube = {
        "variable": "pr_total", "unit": "mm",
        "series": [
            {"t": "2000", "v": 100, "model": "model", "scenario": "historical"},
            {"t": "2001", "v": 101, "model": "model", "scenario": "historical"},
            {"t": "2030", "v": 102, "model": "model", "scenario": "ssp245"},
        ],
    }
    reference = {
        "variable": "precipitation", "unit": "mm/year", "series": [{"t": "2001", "v": 10}],
        "method": {"params": {"aggregate": "year", "statistic": "sum"}},
    }
    params = {**PARAMS, "reference": [2000, 2001], "reference_variable": "precipitation"}
    result = REGISTRY["climate_delta"].fn(params, {"read": [cube, reference]}, CTX)["delta"]
    assert result["absence"]["reason"] == "insufficient_qa"
    assert "coverage" in result["absence"]["detail"]["en"]
    assert "cobertura" in result["absence"]["detail"]["es"]
    shortened = REGISTRY["climate_delta"].fn(
        {**params, "reference_min_years": 1}, {"read": [cube, reference]}, CTX,
    )["delta"]
    assert shortened["series"][0]["v"] == 11
    assert shortened["table"]["rows"][0][3] == 1
    assert any("2001" in limit["en"] and "2000" in limit["en"] for limit in shortened["limits"])


def test_default_accepts_seventeen_of_twenty_reference_years_and_says_so():
    # Guillem, 14 Nov: an observed record that starts in 1998 (IMERG) shares 1998-2014 with the models
    cube = {"series": [
        {"t": str(year), "v": 100, "model": "m", "scenario": "historical"} for year in range(1995, 2015)
    ] + [{"t": "2030", "v": 103, "model": "m", "scenario": "ssp245"}]}
    observed = {"series": [{"t": str(year), "v": 10} for year in range(1998, 2015)]}
    params = {"windows": {"2030": [2030, 2030]}}
    result = REGISTRY["climate_delta"].fn(params, {"read": [cube, observed]}, CTX)["delta"]
    assert result["series"][0]["v"] == 13
    assert any("1998" in limit["en"] and "1995" in limit["en"] for limit in result["limits"])
    strict = REGISTRY["climate_delta"].fn(
        {**params, "reference_min_years": None}, {"read": [cube, observed]}, CTX,
    )["delta"]
    assert strict["absence"]["reason"] == "insufficient_qa"
    sixteen = {"series": [{"t": str(year), "v": 10} for year in range(1999, 2015)]}
    assert "absence" in REGISTRY["climate_delta"].fn(params, {"read": [cube, sixteen]}, CTX)["delta"]


def test_short_reference_does_not_bridge_internal_gaps():
    observed = {"series": [{"t": "2000", "v": 1}, {"t": "2002", "v": 2}]}
    cube = {"series": [
        {"t": str(year), "v": year - 1999, "model": "m", "scenario": "historical"}
        for year in range(2000, 2003)
    ] + [{"t": "2030", "v": 12, "model": "m", "scenario": "ssp245"}]}
    result = REGISTRY["climate_delta"].fn(
        {**PARAMS, "reference": [2000, 2002], "reference_min_years": 2}, {"read": [cube, observed]}, CTX,
    )["delta"]
    assert result["absence"]["reason"] == "insufficient_qa"


def test_scenario_from_two_ingest_reads_with_explicit_observed_reference():
    from types import SimpleNamespace

    def read(variable, zone, period):
        observed_period = {"start": "2000-01-01", "end": "2000-12-31"}
        if variable == "observed_temperature":
            assert period == observed_period
            rows = [{"t": 2000, "v": 10.0}]
        else:
            rows = [
                {"t": 2000, "v": 40.0, "model": "model", "scenario": "historical"},
                {"t": 2030, "v": 42.0, "model": "model", "scenario": "ssp245"},
            ]
        return {
            "data": pd.DataFrame(rows),
            "absence": None,
            "sources": [{"source": "test", "url": "urn:test"}],
            "support": {},
            "coverage": {},
            "unit": "degC",
        }

    ctx = Context(
        {"id": "somewhere"},
        {"start": "2000-01-01", "end": "2030-12-31"},
        CTX.seed,
        SimpleNamespace(read_series=read),
        CTX.progress,
    )
    ingest = REGISTRY["read_series"].fn
    observed = ingest(
        {"variable": "observed_temperature", "read_period": {"start": "2000-01-01", "end": "2000-12-31"}}, {}, ctx
    )["series"]
    model = ingest({"variable": "modeled_temperature"}, {}, ctx)["series"]
    delta = REGISTRY["climate_delta"].fn
    params = {**PARAMS, "reference_variable": "observed_temperature"}
    assert "absence" in delta(params, {"s2": [model]}, ctx)["delta"]
    for inputs in ({"s1": [observed], "s2": [model]}, {"s2": [model], "s1": [observed]}):
        result = delta(params, inputs, ctx)["delta"]
        assert result["variable"] == "modeled_temperature"
        assert result["series"][0]["v"] == 12
        assert "absence" in delta(PARAMS, inputs, ctx)["delta"]


def test_population_projection_accepts_scenario_without_model():
    # SEDAC SSP (population_projected) has scenario per decade but no model column.
    cube = {
        "variable": "population_projected", "unit": "people",
        "series": [
            {"t": "2000", "v": 100, "scenario": "historical"},
            {"t": "2030", "v": 110, "scenario": "ssp245"},
            {"t": "2030", "v": 120, "scenario": "ssp126"},
        ],
    }
    observed = {"variable": "population", "unit": "people", "series": [{"t": "2000", "v": 95}]}
    params = {**PARAMS, "reference_variable": "population"}
    result = REGISTRY["climate_delta"].fn(params, {"read": [cube, observed]}, CTX)["delta"]
    assert "absence" not in result
    assert result["diagnostics"] == {"n_models": 1, "models": "projection"}
    by_scenario = {row["scenario"]: row["v"] for row in result["series"]}
    assert by_scenario["ssp126"] == 115
    assert by_scenario["ssp245"] == 105
    assert all(row["low"] == row["v"] == row["high"] for row in result["series"])


@pytest.mark.parametrize(
    ("projection", "reference", "model_unit", "observed_unit", "model_grid", "observed_grid"),
    [
        ("tas_mean", "air_temperature", "°C", "°C", 27_830, 55_000),
        ("pr_total", "precipitation", "mm", "mm/year", 27_830, 11_132),
    ],
)
def test_climate_metadata_and_comparable_annual_readings(
    projection, reference, model_unit, observed_unit, model_grid, observed_grid,
):
    cube = {
        "variable": projection, "unit": model_unit, "data_class": "extrapolated",
        "support": {"resolution_m": model_grid},
        "series": [
            {"t": "2000", "v": 40 + shift, "model": model, "scenario": "historical"}
            for model, shift in (("first", 0), ("second", 2))
        ] + [
            {"t": "2030", "v": 42 + model_shift + weather_shift, "model": model, "scenario": scenario}
            for model, model_shift in (("first", 0), ("second", 4))
            for scenario, weather_shift in (("ssp126", 0), ("ssp245", 4))
        ],
    }
    observed = {
        "variable": reference, "unit": observed_unit, "data_class": "reanalysis",
        "support": {"resolution_m": observed_grid},
        "method": {"operation": "read_series", "params": {"aggregate": "year", "statistic": "sum"}},
        "series": [{"t": "2000", "v": 10}],
    }
    params = {**PARAMS, "reference_variable": reference}
    delta = REGISTRY["climate_delta"].fn
    result = delta(params, {"read": [observed, cube]}, CTX)["delta"]
    assert [p["v"] for p in result["series"]] == [13, 17]
    assert result["support"]["resolution_m"] == max(model_grid, observed_grid)
    assert result["data_class"] == "extrapolated"
    split = REGISTRY["uncertainty_split"].fn({}, {"delta": [result]}, CTX)["uncertainty"]
    assert split["table"]["rows"][0][:3] == [2030, 1, 4]
    assert split["support"] == result["support"]
    assert split["data_class"] == result["data_class"]
    missing = delta(PARAMS, {"read": [cube, observed]}, CTX)["delta"]
    assert "absence" in missing
    split = REGISTRY["uncertainty_split"].fn({}, {"delta": [missing]}, CTX)["uncertainty"]
    assert split["absence"] == missing["absence"]
    observed["unit"] = "mm/month"
    assert "absence" in delta(params, {"read": [cube, observed]}, CTX)["delta"]


def test_rainfall_delta_declares_the_absolute_millimetre_limit():
    # Guillem, 14 Nov: T2 (relative rainfall change) is dropped for the demo; the limit says so instead
    def run(variable, unit, observed_unit, reference):
        cube = {"variable": variable, "unit": unit, "series": [
            {"t": "2000", "v": 100, "model": "m", "scenario": "historical"},
            {"t": "2030", "v": 103, "model": "m", "scenario": "ssp245"},
        ]}
        observed = {
            "variable": reference, "unit": observed_unit, "series": [{"t": "2000", "v": 10}],
            "method": {"params": {"aggregate": "year", "statistic": "sum"}},
        }
        params = {**PARAMS, "reference_variable": reference}
        return REGISTRY["climate_delta"].fn(params, {"read": [cube, observed]}, CTX)["delta"]["limits"]

    def mentions(limits):
        return any("millimetres" in limit["en"] and "milímetros" in limit["es"] for limit in limits)

    assert mentions(run("pr_total", "mm", "mm/year", "precipitation"))
    assert not mentions(run("tas_mean", "°C", "°C", "air_temperature"))
