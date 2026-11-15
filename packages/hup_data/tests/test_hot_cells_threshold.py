"""population_in_hot_cells through the common door `readers.read_series`: the catalogue's `threshold` (percentile and
base period) reaches `read_with_threshold` and its `threshold` comes back; without them, or with only one, an absence
with its reason. No network: the threshold computation is a double, test values only."""
import json

import pandas as pd
import pytest
import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from hup_data import paths, readers
from hup_data.readers import hot_cells

ZONE = {"id": "z", "name": "Z", "bbox": [0.0, 40.0, 0.02, 40.02]}
PERIOD = {"start": "2021-06-01", "end": "2025-08-31", "months": [6, 7, 8]}
BASE = {"start": "2010-01-01", "end": "2011-12-31"}


@pytest.fixture
def computed(monkeypatch, tmp_path):
    """read_with_threshold replaced by a double that records its call and answers with a threshold."""
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    calls = []

    def double(zone, period, threshold_c=None, *, percentile=None, base_period=None, layer=None, base_layer=None):
        calls.append((zone["id"], period, threshold_c, percentile, base_period))
        return {"data": pd.DataFrame({"t": ["2021-01-01"], "v": [1.0]}), "absence": None, "origin": "package",
                "sources": [{"source": "fake", "url": "urn:fake"}], "support": {}, "coverage": {}, "unit": "people",
                "limits": [], "input_fingerprint": None,
                "threshold": {"value_c": 1.5, "kind": "base_percentile", "percentile": percentile,
                              "base_period": base_period, "base_years": ["2010"], "n_values": 4}}

    monkeypatch.setattr(hot_cells, "read_with_threshold", double)
    return calls


def test_percentile_and_base_period_reach_the_computation_and_the_threshold_comes_back(computed):
    got = readers.read_series("population_in_hot_cells", ZONE, PERIOD, percentile=50, base_period=BASE)
    assert computed == [("z", PERIOD, None, 50, BASE)]
    assert got["absence"] is None and got["threshold"]["percentile"] == 50 and got["threshold"]["base_period"] == BASE
    again = readers.read_series("population_in_hot_cells", ZONE, PERIOD, percentile=50, base_period=BASE)
    assert again["origin"] == "derived" and again["threshold"] == got["threshold"] and len(computed) == 1


def test_without_them_the_absence_says_what_is_missing(computed):
    got = readers.read_series("population_in_hot_cells", ZONE, PERIOD)
    assert computed == [] and got["data"] is None and got["absence"]["reason"] == "not_computed"
    assert got["absence"]["detail"]["en"] == hot_cells.NEEDS[0]


@pytest.mark.parametrize("given", [{"percentile": 50}, {"base_period": BASE}])
def test_with_only_one_of_them_it_is_an_absence(monkeypatch, tmp_path, given):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    got = readers.read_series("population_in_hot_cells", ZONE, PERIOD, **given)
    assert got["data"] is None and got["absence"]["reason"] == "not_computed"
    assert got["absence"]["detail"]["en"] == hot_cells.BOTH[0]


def test_the_entry_declares_a_threshold_and_the_catalogue_validates():
    entry = next(v for v in yaml.safe_load((paths.CATALOG_DIR / "variables.yaml").read_text(encoding="utf-8"))
                 if v["id"] == "population_in_hot_cells")
    assert set(entry["threshold"]) == {"percentile", "base_period", "ref"} and entry["threshold"]["ref"]
    schemas = [json.loads(p.read_text(encoding="utf-8")) for p in paths.CONTRACTS_DIR.glob("*.schema.json")]
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)
    schema = json.loads((paths.CONTRACTS_DIR / "catalog.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
    built = json.loads((paths.CATALOG_DIR / "catalog.json").read_text(encoding="utf-8"))
    assert next(v for v in built["variables"] if v["id"] == "population_in_hot_cells")["threshold"] == \
        entry["threshold"]
    assert list(validator.iter_errors(built)) == []
    broken = {**built, "variables": [{**v, "threshold": {**v["threshold"], "percentile": 100}} if "threshold" in v
                                     else v for v in built["variables"]]}
    assert list(validator.iter_errors(broken)) != []
