"""The ingest operation: it reads only through ctx.data and returns a result that fits the contract."""
import json

import pandas as pd
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from hup_data import paths
from hup_ops.ingest import read_series
from hup_ops.registry import REGISTRY, Context

ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [0, 0, 1, 1]}
PERIOD = {"start": "2000-01-01", "end": "2002-12-31"}
SOURCE = {"source": "nasa-power", "url": "https://power.larc.nasa.gov/api/temporal/monthly/point"}


class Readers:
    """Stands in for hup_data.readers: it answers what the test gives it and records what was asked."""

    def __init__(self, answer):
        self.answer, self.asked = answer, []

    def read_series(self, variable, zone, period):
        self.asked.append((variable, zone["id"], period))
        return self.answer


def context(answer):
    data = Readers(answer)
    return Context(zone=ZONE, period=PERIOD, seed=1, data=data, progress=lambda fraction, text: None), data


def valid(body):
    """The executor adds these four fields; with them the result must fit result.schema.json."""
    schemas = [json.loads(file.read_text(encoding="utf-8")) for file in paths.CONTRACTS_DIR.glob("*.schema.json")]
    registry = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas)
    schema = next(schema for schema in schemas if schema["$id"].endswith("result.schema.json"))
    full = {**body, "id": "r-s1-series", "analysis": "a1", "step": "s1", "fingerprint": "0" * 64}
    errors = list(Draft202012Validator(schema, registry=registry, format_checker=FormatChecker()).iter_errors(full))
    assert not errors, errors[0].message
    return body


def monthly(years, missing=()):
    rows = [{"t": f"{year}-{month:02d}-01", "v": float(month)} for year in years for month in range(1, 13)
            if (year, month) not in missing]
    return {"data": pd.DataFrame(rows), "absence": None, "origin": "cache", "sources": [SOURCE], "unit": "°C",
            "support": {"resolution_m": 55000, "cells": 0.1}, "coverage": {"valid_pct": 100.0, "n_dates": len(rows)}}


def test_it_is_registered_and_reads_only_through_the_context():
    assert REGISTRY["read_series"].version == "1.2"
    ctx, data = context(monthly([2000]))
    result = valid(read_series({"variable": "air_temperature"}, {}, ctx)["series"])
    assert data.asked == [("air_temperature", "test-zone", PERIOD)]
    assert result["type"] == "series" and result["unit"] == "°C" and result["data_class"] == "measured"
    assert len(result["series"]) == 12 and result["series"][0] == {"t": "2000-01-01", "v": 1.0}
    assert result["evidence_level"] == "E0" and result["zone"] == "test-zone" and result["sources"] == [SOURCE]


def test_years_are_aggregated_and_short_years_are_dropped_not_padded():
    ctx, _ = context(monthly([2000, 2001, 2002], missing={(2001, 12)}))
    params = {"variable": "air_temperature", "aggregate": "year", "statistic": "mean", "min_count": 12}
    result = valid(read_series(params, {}, ctx)["series"])
    assert result["series"] == [{"t": "2000", "v": 6.5}, {"t": "2002", "v": 6.5}]  # 2001 lacks a month
    total = read_series({**params, "statistic": "sum", "min_count": 1}, {}, ctx)["series"]["series"]
    assert [row["v"] for row in total] == [78.0, 66.0, 78.0]

    rain = monthly([2000])
    rain["unit"] = "mm/month"
    ctx, _ = context(rain)
    summed = read_series({"variable": "precipitation", "aggregate": "year", "statistic": "sum"}, {}, ctx)["series"]
    averaged = read_series({"variable": "precipitation", "aggregate": "year"}, {}, ctx)["series"]
    assert summed["unit"] == "mm/year" and averaged["unit"] == "mm/month"  # a sum of months is a yearly total

    nothing = valid(read_series({**params, "min_count": 13}, {}, ctx)["series"])
    assert nothing["absence"]["reason"] == "insufficient_qa" and "series" not in nothing


def test_yearly_season_uses_only_requested_months_and_counts_only_those_values():
    ctx, data = context(monthly([2000, 2001], missing={(2001, 7)}))
    period = {**PERIOD, "months": [6, 7]}
    params = {"variable": "precipitation", "aggregate": "year", "statistic": "sum", "min_count": 2,
              "read_period": period}
    result = valid(read_series(params, {}, ctx)["series"])
    assert data.asked[-1][2] == period
    assert result["series"] == [{"t": "2000", "v": 13.0}]
    assert result["coverage"]["n_dates"] == 1
    empty = valid(read_series({**params, "read_period": {**period, "months": [7]}, "min_count": 2}, {}, ctx)["series"])
    assert empty["absence"]["reason"] == "insufficient_qa"


def test_model_series_keep_model_and_scenario_for_executor_classification():
    rows = [{"t": year, "v": 20.0 + i, "model": model, "scenario": scenario}
            for i, (model, scenario) in enumerate([("M1", "historical"), ("M1", "ssp245"), ("M2", "ssp245")])
            for year in (2000, 2001)]
    answer = {"data": pd.DataFrame(rows), "absence": None, "origin": "cache", "unit": "°C", "support": {},
              "coverage": {}, "sources": [{"source": "nex-gddp-cmip6", "url": "https://www.nccs.nasa.gov/"}]}
    ctx, _ = context(answer)
    result = valid(read_series({"variable": "tas_mean", "aggregate": "year"}, {}, ctx)["series"])
    assert result["data_class"] == "measured" and len(result["series"]) == 6
    assert {"t": "2001", "v": 22.0, "model": "M2", "scenario": "ssp245"} in result["series"]


def test_the_limits_of_the_reader_travel_with_a_read_without_grid():
    limit = {"en": "Modelled split inside each section.", "es": "Reparto modelado dentro de cada sección."}
    ctx, _ = context({**monthly([2000]), "limits": [limit]})
    result = valid(read_series({"variable": "air_temperature"}, {}, ctx)["series"])
    assert result["limits"] == [limit]
    yearly = valid(read_series({"variable": "air_temperature", "aggregate": "year"}, {}, ctx)["series"])
    assert yearly["limits"] == [limit]


def test_tier1_only_is_forwarded_only_when_the_plan_sets_it_and_its_limit_reaches_the_result():
    """Absent, the reader's default applies (Landsat: Tier 1 only) and its limit, in en and es, reaches the result."""
    from hup_data.readers import landsat

    limit = landsat.tier1_limit(3)
    asked = []

    class Landsat:
        def read_series(self, variable, zone, period, **options):
            asked.append(options)
            return {**monthly([2000]), "limits": [] if options.get("tier1_only") is False else [limit]}

    ctx = Context(zone=ZONE, period=PERIOD, seed=1, data=Landsat(), progress=lambda fraction, text: None)
    plain = valid(read_series({"variable": "lst"}, {}, ctx)["series"])
    assert asked[-1] == {} and plain["limits"] == [limit]
    assert "3 Tier 2" in plain["limits"][0]["en"] and "3 escena(s) T2" in plain["limits"][0]["es"]
    every = valid(read_series({"variable": "lst", "tier1_only": False}, {}, ctx)["series"])
    assert asked[-1] == {"tier1_only": False} and "limits" not in every
    valid(read_series({"variable": "lst", "tier1_only": True}, {}, ctx)["series"])
    assert asked[-1] == {"tier1_only": True}


def test_an_absence_travels_with_its_reason_and_its_source():
    absence = {"reason": "needs_package", "detail": {"en": "Not downloaded.", "es": "No descargado."}}
    ctx, _ = context({"data": None, "absence": absence, "origin": "cache", "sources": [SOURCE], "unit": None,
                      "support": {}, "coverage": {}})
    result = valid(read_series({"variable": "air_temperature"}, {}, ctx)["series"])
    assert result["absence"] == absence and "series" not in result and "unit" not in result

def test_impact_plan_with_reference_first():
    from types import SimpleNamespace

    from hup_api import catalog
    from hup_api.executor import runner
    from hup_api.library.plan_cache import normalize

    def read(variable, zone, period):
        answer = monthly(range(2000, 2010))
        if zone["id"] == ZONE["id"]:
            answer["data"].loc[answer["data"]["t"] >= "2005", "v"] += 4
        return answer

    reader = SimpleNamespace(
        read_series=read,
        reference_zone=lambda zone, km: {**zone, "id": "reference"},
    )
    plan = json.loads((paths.CONTRACTS_DIR / "examples" / "plan.json").read_text(encoding="utf-8"))
    current = catalog.load()
    plan.update(zone=ZONE, period={"start": "2000-01-01", "end": "2009-12-31"},
                variables=["air_temperature"], catalog_version=current["version"])
    reference, site, contrast = plan["steps"][0], plan["steps"][1], plan["steps"][3]
    for step, area in ((reference, "reference"), (site, "zone")):
        step.update(params={"variable": "air_temperature", "area": area, "aggregate": "year"},
                    sources=["nasa-power"])
    contrast.update(params={"variable": "air_temperature", "before": [2000, 2004], "after": [2005, 2009]},
                    depends_on=[reference["id"], site["id"]], sources=["nasa-power"])
    plan["steps"] = [reference, site, contrast]
    # The example plan pins the example catalog's versions; a real plan takes them from the current catalog.
    versions = {operation["id"]: operation["version"] for operation in current["operations"]}
    for step in plan["steps"]:
        step["version"] = versions[step["operation"]]
    plan = normalize(plan, current)
    assert runner.validate_plan(plan, current) == []
    job = runner.Run("a-test-impact", plan, current, REGISTRY)
    ctx = Context(ZONE, plan["period"], 1, reader, lambda *_: None)
    outputs = {}
    for step in plan["steps"]:
        inputs = {key: outputs[key] for key in step["depends_on"]}
        bodies = REGISTRY[step["operation"]].fn(step["params"], inputs, ctx)
        outputs[step["id"]] = job.complete(step, "0" * 64, bodies)
    results = {item["id"]: item for group in outputs.values() for item in group}
    ref = results[f"r-{reference['id']}-series"]
    assert ref["method"]["params"]["area"] == "reference"
    assert ref["zone"] == "test-zone-reference-3km"
    assert results[f"r-{contrast['id']}-contrast"]["value"] == 4

def test_ring_metadata_selects_bands_independently_of_order():
    from types import SimpleNamespace

    def read(variable, zone, period):
        answer = monthly(range(2000, 2010))
        answer["data"].loc[answer["data"]["t"] >= "2005", "v"] += zone.get("shift", 0)
        return answer

    reader = SimpleNamespace(
        read_series=read,
        reference_zone=lambda zone, km: zone,
        ring_zones=lambda zone, edges: [{**zone, "shift": edge} for edge in edges],
    )
    ctx = Context(ZONE, PERIOD, 1, reader, lambda *_: None)
    params = {"variable": "air_temperature", "aggregate": "year"}
    rings = read_series({**params, "area": "ring", "ring_km": [2, 1]}, {}, ctx)
    reference = read_series({**params, "area": "reference"}, {}, ctx)["series"]
    assert valid(rings["ring-1"])["zone"] == "test-zone-ring-1-2km"
    result = REGISTRY["distance_decay"].fn(
        {"before": [2000, 2004], "after": [2005, 2009]},
        {"s1": [rings["ring-1"], rings["ring-0"]], "s2": [reference]}, ctx,
    )["decay"]
    assert [row[:3] for row in result["table"]["rows"]] == [[0, 1000, 1], [1000, 2000, 2]]


def scenes(rows):
    """Satellite-like answer: (date, value) pairs, unevenly spread over the months."""
    return {"data": pd.DataFrame([{"t": t, "v": v} for t, v in rows]), "absence": None, "origin": "cache",
            "sources": [{"source": "landsat-c2-l2", "url": "https://www.usgs.gov/landsat-missions"}], "unit": "°C",
            "support": {"resolution_m": 30, "revisit_days": 8}, "coverage": {"valid_pct": 100.0, "n_dates": len(rows)}}


def test_month_balanced_weighs_every_month_the_same():
    # Three June scenes at 10 and one July scene at 20: the mean of the scenes says 12.5, each month once says 15.
    ctx, _ = context(scenes([("2000-05-01", 100.0), ("2000-06-01", 10.0), ("2000-06-09", 10.0),
                             ("2000-06-17", 10.0), ("2000-07-03", 20.0)]))
    params = {"variable": "lst", "aggregate": "year", "statistic": "month_balanced", "min_count": 1}
    seasonal = {**params, "read_period": {**PERIOD, "months": [6, 7]}}
    assert valid(read_series(seasonal, {}, ctx)["series"])["series"] == [{"t": "2000", "v": 15.0}]
    # A year without July is not pulled to June's value: July enters at its usual value plus the year's anomaly.
    ctx, _ = context(scenes([("2000-06-01", 10.0), ("2000-07-01", 20.0), ("2001-06-01", 12.0)]))
    values = {row["t"]: row["v"] for row in read_series(params, {}, ctx)["series"]["series"]}
    assert values == {"2000": 15.0, "2001": 16.5}
    short = read_series({**params, "min_count": 2}, {}, ctx)["series"]["series"]
    assert [row["t"] for row in short] == ["2000"]


def test_summer_follows_the_hemisphere_and_counts_in_the_year_it_ends():
    from hup_ops.seasons import season_months, season_year

    assert season_months("summer", ZONE) == [6, 7, 8, 9]
    south = {**ZONE, "bbox": [-71.0, -34.0, -70.0, -33.0]}
    assert season_months("summer", south) == [12, 1, 2, 3]
    assert season_year("2020-12-15", [12, 1, 2, 3]) == 2021 and season_year("2021-03-15", [12, 1, 2, 3]) == 2021
    assert season_year("2020-06-15", [6, 7, 8, 9]) == 2020

    ctx, data = context(scenes([("2000-07-01", 30.0)]))
    read_series({"variable": "lst", "aggregate": "year", "season": "summer"}, {}, ctx)
    assert data.asked[-1][2] == {**PERIOD, "months": [6, 7, 8, 9]}
    answer = scenes([("1999-12-10", 20.0), ("2000-01-10", 22.0), ("2000-05-10", 100.0),
                     ("2000-12-10", 24.0), ("2001-02-10", 26.0), ("2001-05-10", 100.0)])
    ctx = Context(zone=south, period=PERIOD, seed=1, data=Readers(answer), progress=lambda fraction, text: None)
    result = read_series({"variable": "lst", "aggregate": "year", "season": "summer", "statistic": "median"}, {}, ctx)
    assert ctx.data.asked[-1][2]["start"] == "1999-12-01"  # the first southern summer starts the December before
    assert result["series"]["series"] == [{"t": "2000", "v": 21.0}, {"t": "2001", "v": 25.0}]


def test_percentile_and_base_period_are_forwarded_only_when_given_and_the_threshold_reaches_the_result():
    """Absent, the call is today's; given, they reach the door, and the reader's threshold becomes the declared
    diagnostics of read_series. Test values only."""
    base = {"start": "2000-06-01", "end": "2001-09-30"}
    asked = []

    class Thresholded:
        def read_series(self, variable, zone, period, **options):
            asked.append(options)
            extra = {"threshold": {"value_c": 1.5, "kind": "base_percentile", "percentile": options["percentile"],
                                   "base_period": options["base_period"], "base_years": [2000, 2001],
                                   "n_values": 4}} if options else {}
            return {**monthly([2000]), **extra}

    ctx = Context(zone=ZONE, period=PERIOD, seed=1, data=Thresholded(), progress=lambda fraction, text: None)
    plain = valid(read_series({"variable": "fake"}, {}, ctx)["series"])
    assert asked[-1] == {} and "diagnostics" not in plain
    given = valid(read_series({"variable": "fake", "percentile": 50, "base_period": base}, {}, ctx)["series"])
    assert asked[-1] == {"percentile": 50, "base_period": base}
    assert given["diagnostics"] == {"threshold_c": 1.5, "threshold_percentile": 50, "threshold_base_start":
                                    base["start"], "threshold_base_end": base["end"], "threshold_n_values": 4}
    catalog = json.loads((paths.CATALOG_DIR / "catalog.json").read_text(encoding="utf-8"))
    declared = next(o for o in catalog["operations"] if o["id"] == "read_series")["diagnostics"]
    assert set(given["diagnostics"]) <= set(declared)
