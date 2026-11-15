import pytest

from hup_ops import resources
from hup_ops.registry import Context

CTX = Context({}, {}, 42, None, lambda *_: None)
BASE = {"variable": "population", "unit": "people", "value": 10}
ARCHETYPE = {
    "id": "synthetic-water", "per": "people", "resource": "water", "unit": "L/day",
    "low": 2, "central": 3, "high": 4, "ref": "urn:hup:synthetic",
}


def test_consumption():
    result = resources.resource_estimate({"archetype": ARCHETYPE}, {"read": [BASE]}, CTX)["estimate"]
    assert result["value"] == 30
    assert result["table"]["rows"][0][2:4] == [20, 40]
    assert result["data_class"] == "approximated"


@pytest.mark.parametrize("field", ["low", "high", "ref"])
def test_incomplete_archetype(field):
    params = {"archetype": {**ARCHETYPE, field: None}}
    result = resources.resource_estimate(params, {"read": [BASE]}, CTX)["estimate"]
    assert result["absence"]["reason"] == "not_computed"
    assert "value" not in result


def test_index_reproducible_and_requires_all_components():
    params = {
        "components": {"water": {"threshold": 10, "weight": 1}, "energy": {"threshold": 10, "weight": 1}},
        "sample_power": 7,
    }
    water = {"variable": "water", "value": 20, "interval": {"low": 10, "high": 30}}
    energy = {"variable": "energy", "value": 40, "interval": {"low": 40, "high": 40}}
    failure = resources.stress_index(params, {"read": [water]}, CTX)["index"]
    assert "energy" in failure["absence"]["detail"]["en"]
    inputs = {"read": [water, energy]}
    result = resources.stress_index(params, inputs, CTX)
    assert result == resources.stress_index(params, inputs, CTX)
    assert result["index"]["value"] == pytest.approx(8 ** 0.5)
    rows = result["sensitivity"]["table"]["rows"]
    assert rows[1][1] == pytest.approx(1, abs=0.08)
    assert rows[0][1:] == [0.0, 0.0]


def _household_water():
    from hup_api import catalog

    return next(item for item in catalog.load()["archetypes"] if item["id"] == "household-water")


def test_resource_estimate_is_registered_and_in_the_built_catalog():
    from hup_api import catalog
    from hup_ops.registry import REGISTRY

    entry = next(item for item in catalog.load()["operations"] if item["id"] == "resource_estimate")
    assert REGISTRY["resource_estimate"].version == entry["version"]
    assert entry["output"]["data_class"] == "approximated"
    assert "stress_index" not in REGISTRY


def test_real_household_water_archetype_uses_the_national_mean_and_regional_range():
    # INE 2024, pp. 2 and 4; see catalog/archetypes.yaml.
    archetype = _household_water()
    people = {**BASE, "value": 1000}
    result = resources.resource_estimate({"archetype": archetype}, {"read": [people]}, CTX)["estimate"]
    assert (result["value"], *result["table"]["rows"][0][2:4]) == (128_000, 79_000, 186_000)
    assert result["table"]["rows"][0][4:6] == ["household-water", archetype["ref"]]
    assert result["data_class"] == "approximated" and result["unit"] == "L/day"
    assert any(archetype["ref"] in limit["es"] for limit in result["limits"])
    assert archetype["limits"] and all(limit in result["limits"] for limit in archetype["limits"])


def test_data_center_catalog_archetypes_expose_limits_for_estimates_and_absence():
    from hup_api import catalog

    archetypes = {item["id"]: item for item in catalog.load()["archetypes"]}
    # Guillem, «si a les recomanacions»: the range is the coefficients' at the 25 MW example size; 10 and 100 MW apart
    for resource, expected in (("water", (74_100, 35_850, 168_200)), ("energy", (127_200, 119_825, 145_075))):
        archetype = archetypes[f"data-center-{resource}"]
        result = resources.resource_estimate({"archetype": archetype}, {}, CTX)["estimate"]
        row = dict(zip(result["table"]["columns"], result["table"]["rows"][0], strict=True))
        assert (result["value"], row["low"], row["high"]) == expected
        assert (row["quantity_low"], row["quantity"], row["quantity_high"]) == (10, 25, 100)
        assert any("de 10 a 100 MW IT" in limit["es"] and "not multiplied" in limit["en"] for limit in result["limits"])
        assert result["data_class"] == "approximated"
        assert archetype["limits"] and all(limit in result["limits"] for limit in archetype["limits"])
    land = archetypes["data-center-land"]
    result = resources.resource_estimate({"archetype": land}, {}, CTX)["estimate"]
    assert result["absence"]["reason"] == "not_computed" and "value" not in result
    assert land["limits"] and all(limit in result["limits"] for limit in land["limits"])


@pytest.mark.parametrize("archetype", ["household-water", "no-such-archetype"])
def test_archetype_given_only_by_id_is_an_absence_not_an_estimate(archetype):
    result = resources.resource_estimate({"archetype": archetype}, {"read": [BASE]}, CTX)["estimate"]
    assert result["absence"]["reason"] == "not_computed"
    assert result["absence"]["detail"]["es"]
    assert "value" not in result


def _series(*points):
    return {"type": "series", "variable": "population", "unit": "people",
            "series": [{"t": t, "v": v} for t, v in points]}


def test_population_series_uses_its_last_year_and_says_so():
    population = _series(("2020-01-01", 1), ("2025-01-01", 10), ("2022-01-01", 5))
    result = resources.resource_estimate({"archetype": ARCHETYPE}, {"read": [population]}, CTX)["estimate"]
    assert result["value"] == 30
    assert dict(zip(result["table"]["columns"], result["table"]["rows"][0], strict=True))["quantity_year"] == "2025"
    assert any("2025" in limit["es"] and "2025" in limit["en"] for limit in result["limits"])


def test_population_series_with_a_null_last_year_uses_the_year_before():
    population = _series(("2024-01-01", 10), ("2025-01-01", None))
    result = resources.resource_estimate({"archetype": ARCHETYPE}, {"read": [population]}, CTX)["estimate"]
    assert result["value"] == 30
    assert result["table"]["rows"][0][-1] == "2024"


@pytest.mark.parametrize("population", [_series(), _series(("2025-01-01", None))])
def test_empty_or_null_population_series_is_an_absence(population):
    result = resources.resource_estimate({"archetype": ARCHETYPE}, {"read": [population]}, CTX)["estimate"]
    assert set(result["absence"]["detail"]) == {"en", "es"}
    assert "value" not in result


def test_engine_walkthrough_with_a_population_series_and_the_real_archetype():
    """Plan validated by the executor; read_series of population (synthetic reader) feeds resource_estimate."""
    import json
    from types import SimpleNamespace

    import pandas as pd

    from hup_api import catalog
    from hup_api.executor import runner
    from hup_api.library.plan_cache import normalize
    from hup_data import paths
    from hup_ops.registry import REGISTRY

    current = catalog.load()
    versions = {item["id"]: item["version"] for item in current["operations"]}
    zone = {"id": "test-zone", "name": "Test zone", "bbox": [0, 0, 1, 1]}
    plan = json.loads((paths.CONTRACTS_DIR / "examples" / "plan.json").read_text(encoding="utf-8"))
    plan.pop("comparison", None)
    plan.update(zone=zone, period={"start": "2020-01-01", "end": "2025-12-31"}, variables=["population"],
                catalog_version=current["version"])
    plan["steps"] = [
        {"id": "s1", "type": "ingest", "operation": "read_series", "version": versions["read_series"],
         "params": {"variable": "population"}, "depends_on": [], "sources": ["ine-population"]},
        {"id": "s2", "type": "compute", "operation": "resource_estimate", "version": versions["resource_estimate"],
         "params": {"archetype": _household_water()}, "depends_on": ["s1"]},
    ]
    plan = normalize(plan, current)
    assert runner.validate_plan(plan, current) == []

    def read(variable, zone, period):
        return {"data": pd.DataFrame({"t": ["2024-01-01", "2025-01-01"], "v": [900, 1000]}), "absence": None,
                "origin": "package", "sources": [{"source": "ine-population", "url": "https://www.ine.es/"}],
                "unit": "people", "support": {"resolution_m": 100}, "coverage": {"valid_pct": 100, "n_dates": 2},
                "input_fingerprint": "0" * 64}

    ctx = Context(zone, plan["period"], 1, SimpleNamespace(read_series=read), lambda *_: None)
    job = runner.Run("a-test-resources", plan, current, REGISTRY)
    outputs = {}
    for step in plan["steps"]:
        bodies = REGISTRY[step["operation"]].fn(step["params"], {k: outputs[k] for k in step["depends_on"]}, ctx)
        outputs[step["id"]] = job.complete(step, "0" * 64, bodies)
    estimate, = outputs["s2"]
    row = dict(zip(estimate["table"]["columns"], estimate["table"]["rows"][0], strict=True))
    assert (estimate["value"], row["low"], row["high"]) == (128_000, 79_000, 186_000)
    assert row["quantity_year"] == "2025" and estimate["data_class"] == "approximated"
