import json
from pathlib import Path

import pytest
import yaml

from hup_ops.registry import REGISTRY, Context

ROOT = Path(__file__).resolve().parents[2]
GOLD = Path(__file__).parent
EXPECTED = yaml.safe_load((GOLD / "expected.yaml").read_text(encoding="utf-8"))
CONTEXT = Context({}, {}, 42, None, lambda *_: None)


def series(variable, unit, values, source, url):
    return {
        "variable": variable, "unit": unit, "data_class": "measured",
        "sources": [{"source": source, "url": url}],
        "series": [{"t": str(date), "v": value} for date, value in values],
    }


def test_scan_gold():
    rows = json.loads((GOLD / "fixtures/scan.json").read_text(encoding="utf-8"))
    entries = [
        series(row["var"].lower().replace("_", "-"), row["unit"],
               enumerate(row["series"], row["start"]), "nasa-power", "https://power.larc.nasa.gov/")
        for row in rows
    ]
    output = REGISTRY["scan_trends"].fn({}, {"read": entries}, CONTEXT)
    catalog = yaml.safe_load((ROOT / "catalog/operations.yaml").read_text(encoding="utf-8"))
    scan = next(operation for operation in catalog if operation["id"] == "scan_trends")
    assert all(set(result["diagnostics"]) <= set(scan["diagnostics"]) for result in output.values())
    for key, expected in EXPECTED["scan"].items():
        if key in output["scan"]["diagnostics"]:
            actual = output["scan"]["diagnostics"][key]
        else:
            sample = output["trend-0"]
            actual = sample["value"] * 10 if key == "slope_per_decade" else sample["statistic"]["p_adjusted"]
        assert actual == pytest.approx(expected["value"], abs=expected["tolerance"])


@pytest.mark.parametrize("variable", ["lst", "ndvi"])
@pytest.mark.xfail("baci_contrast" not in REGISTRY, strict=True, reason="A6")
def test_baci_gold(variable):
    scenes = json.loads((GOLD / "fixtures/landsat.json").read_text(encoding="utf-8"))
    expected = EXPECTED["baci"][variable]
    entries = [
        series(variable, expected["unit"], [(date, areas[zone][variable]) for date, areas in scenes.items()],
               "landsat-c2-l2", "https://www.usgs.gov/landsat-missions/landsat-collection-2")
        for zone in ("1", "9")
    ]
    result = REGISTRY["baci_contrast"].fn(
        {"before": [2013, 2021], "after": [2023, 2025]}, {"site": [entries[0]], "reference": [entries[1]]}, CONTEXT,
    )["contrast"]
    assert expected["low"] <= result["value"] <= expected["high"]


def test_catalog_registry_versions():
    entries = yaml.safe_load((ROOT / "catalog/operations.yaml").read_text(encoding="utf-8"))
    scientific = [entry for entry in entries if entry["type"] in {"ingest", "compute"}]
    expected = {entry["id"]: entry["version"] for entry in scientific}
    assert len(entries) == len({entry["id"] for entry in entries})
    assert {key: value.version for key, value in REGISTRY.items()} == expected
