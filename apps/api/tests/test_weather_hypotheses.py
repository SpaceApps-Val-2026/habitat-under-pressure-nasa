"""The provisional weather pairs share a cell, a family and a fixed publication rule."""

import time

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from scipy.stats import theilslopes

from hup_api import contracts, main
from hup_api.assistant.weather import relationship_question
from hup_api.catalog import load
from hup_api.writer import compose, compose_fallback, write_findings
from hup_data import coverage, readers
from hup_ops.association import lagged_association
from hup_ops.ingest import read_series
from hup_ops.registry import Context
from hup_ops.weather_hypotheses import detrend, power_cell

ZONE = {"id": "test", "name": "Test", "bbox": [-.45, 39.4, -.42, 39.46]}
PERIOD = {"start": "1998-01-01", "end": "2024-12-31"}


def test_sen_detrending_uses_the_fitted_slope():
    time = np.arange(27)
    data = np.random.default_rng(808).normal(size=27) + time * 2.8
    slope = theilslopes(data, time).slope
    np.testing.assert_allclose(detrend(data), data - slope * time)


@pytest.mark.parametrize(("text", "wanted"), [
    ("¿Hay relación entre la lluvia y el calor en Paiporta?", True),
    ("Quina relació hi ha entre la pluja i la temperatura a Requena?", True),
    ("Does rainfall correlate with temperature in Sydney?", True),
    ("What changes together in Sydney?", True),
    ("¿Llueve menos en Requena que antes?", False),
    ("¿Cómo afecta el calor al consumo de electricidad?", False),
])
def test_scope_for_relations(text, wanted):
    assert relationship_question(text) is wanted


class Monthly:
    def __init__(self, relationship=True, gaps=False):
        rng = np.random.default_rng(913)
        self.rain = rng.normal(size=27)
        self.warm = (-self.rain if relationship else rng.normal(size=27))
        self.annual = (self.rain if relationship else rng.normal(size=27))
        self.gaps = gaps

    def read_series(self, variable, zone, period):
        assert zone["bbox"] == [-.9375, 39.25, -.3125, 39.75]
        assert zone["geometry"] is None
        source = "imerg-monthly" if variable == "precipitation" else "nasa-power"
        values = {"precipitation": self.rain, "air_temperature": self.annual,
                  "air_temperature_max": self.warm}[variable]
        dates = pd.date_range("1998-01-01", "2024-12-31", freq="MS")
        table = pd.DataFrame({"t": dates.strftime("%Y-%m-%d"), "v": np.repeat(values + np.arange(27) * .3, 12)})
        if self.gaps:
            table = table[table.t != "2004-06-01"]
        return {"data": table, "unit": "mm/month" if source == "imerg-monthly" else "°C",
                "sources": [{"source": source, "url": f"https://example.com/{source}"}],
                "support": {"resolution_m": 11100 if source == "imerg-monthly" else 55000, "cells": 1},
                "coverage": {"valid_pct": 100}, "absence": None}


@pytest.mark.parametrize(("relationship", "gaps", "status", "level"), [
    (True, False, "compatible", "E2"),
    (False, False, "inconclusive", "E0"),
    (True, True, "not_testable", "E0"),
])
def test_complete_family_on_the_power_cell(monkeypatch, relationship, gaps, status, level):
    monkeypatch.setenv("HUP_MILESTONE_HYPOTHESES", "1")
    catalog = load()
    operation = next(item for item in catalog["operations"] if item["id"] == "lagged_association")
    pairs = operation["params"]["properties"]["weather_hypotheses"]["const"]
    params = {"weather_hypotheses": pairs}
    current = Context(zone=ZONE, period={"start": "2024-01-01", "end": "2024-12-31"},
                      seed=31, data=Monthly(relationship, gaps), progress=lambda *_: None)
    assert power_cell(ZONE) == [-.9375, 39.25, -.3125, 39.75]
    inputs = {}
    for pair in pairs:
        season = pair["season"]
        for role, statistic in (("precipitation", "sum"), ("temperature", "mean")):
            read = {"variable": pair[role], "aggregate": "year", "statistic": statistic,
                    "require_complete_months": True, "hypothesis_power_cell": True, "read_period": PERIOD}
            if season:
                read["season"] = season
            inputs[f"{pair['id']}-{role}"] = [read_series(read, {}, current)["series"]]
    result = lagged_association(params, inputs, current)["hypotheses"]
    result.update(id="hypotheses", step="weather-hypotheses", analysis="test-analysis")
    assert not contracts.errors("result", result)
    assert result["evidence_level"] == level
    assert result["data_class"] == "reanalysis"
    assert [row[-1] for row in result["table"]["rows"]] == [status, status]
    assert [row[1:4] for row in result["table"]["rows"]] == [[1998, 2024, 26 if gaps else 27]] * 2
    assert {entry["source"] for entry in result["sources"]} == {"imerg-monthly", "nasa-power"}
    if status == "compatible":
        plan = {"design": "scan", "zone": ZONE, "period": PERIOD, "answer": {"decided": []}}
        assert write_findings(plan, [result], catalog) == []
        for story in (compose_fallback(plan, [result], catalog), compose(plan, [result], [], catalog)):
            assert "Hipótesis" not in str(story["editorial"])
            for style in ("journalistic", "institutional", "scientific"):
                block, = story[style]
                assert block["block"] == "Hypotheses"
                assert "27 años emparejados" in block["text"]["es"]
                assert block["text"]["es"].count("; no la demuestra.") == 2
    monkeypatch.delenv("HUP_MILESTONE_HYPOTHESES")
    with pytest.raises(ValueError, match="disabled"):
        lagged_association(params, inputs, current)


def test_catalogue_requires_the_predeclared_family():
    op = next(item for item in load()["operations"] if item["id"] == "lagged_association")
    schema = op["params"]
    pairs = schema["properties"]["weather_hypotheses"]["const"]
    assert Draft202012Validator(schema).is_valid({"weather_hypotheses": pairs})
    assert not Draft202012Validator(schema).is_valid({"weather_hypotheses": pairs[:1]})


def test_relationship_question_publishes_the_hypotheses_after_the_findings(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_MILESTONE_HYPOTHESES", "1")
    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    monkeypatch.setenv("HUP_QUESTION_CACHE", "off")
    monkeypatch.setattr(coverage, "query", lambda variable, *_: [
        {"source": "imerg-monthly", "origin": "package", "period": PERIOD},
    ] if variable == "precipitation" else [])
    monthly = Monthly()
    called = []

    def read(variable, zone, period):
        called.append(zone["bbox"])
        prepared = {**zone, "bbox": power_cell(zone), "geometry": None}
        return {**monthly.read_series(variable, prepared, period), "origin": "package"}

    monkeypatch.setattr(readers, "read_series", read)
    client = TestClient(main.app)
    response = client.post("/api/plans", json={
        "text": "¿Hay relación entre la lluvia y la temperatura en este punto?", "language": "es", "place": ZONE,
    })
    assert response.status_code == 200, response.text
    plan = response.json()["plan"]
    response = client.post("/api/analyses", json={"plan": plan})
    assert response.status_code == 202, response.text
    url = f"/api/analyses/{response.json()['analysis_id']}"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        saved = client.get(url).json()
        if saved["status"] != "running":
            break
        time.sleep(.05)
    assert saved["status"] == "finished"
    assert set(plan["variables"]) == {"precipitation", "air_temperature"}
    assert called.count(power_cell(ZONE)) == 4
    report = saved["story"]["journalistic"]
    hypothesis, = [block for block in report if block["block"] == "Hypotheses"]
    assert "E2" in hypothesis["text"]["es"]
    assert "Hipótesis" not in str(saved["story"]["editorial"])
    assert not any(set(finding["results"]) & set(hypothesis["results"]) for finding in saved["findings"])
