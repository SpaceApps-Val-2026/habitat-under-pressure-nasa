import pytest

from hup_ops.registry import REGISTRY, Context

CTX = Context({}, {}, 42, None, lambda *_: None)


def series(kind, shift=0):
    return {
        "data_class": kind,
        "variable": "ndvi",
        "unit": "1",
        "series": [{"t": str(y), "v": shift if y >= 2010 else 0} for y in range(2000, 2040)],
    }


def test_reanalysis_trend():
    result = REGISTRY["trend"].fn(
        {}, {"read": [{"data_class": "reanalysis", "series": [{"t": str(y), "v": y} for y in range(2000, 2040)]}]}, CTX
    )["trend"]
    assert result["data_class"] == "reanalysis"
    assert result["value"] == 1


@pytest.mark.parametrize("ref", ["measured", "reanalysis", "illustrative"])
def test_contrast_inherits_least_direct_class(ref):
    inputs = {"site": [series("measured", 1)], "reference": [series(ref)]}
    result = REGISTRY["baci_contrast"].fn({"before": [2000, 2009], "after": [2010, 2019]}, inputs, CTX)
    assert result["contrast"]["data_class"] == ref
    assert result["contrast"]["value"] == 1
