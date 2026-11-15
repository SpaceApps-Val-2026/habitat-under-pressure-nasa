from hup_ops.registry import REGISTRY, Context

CTX = Context({}, {}, 1, None, lambda *_: None)


def test_aligned_cells():
    entries = {
        "cells": [
            {
                "table": {
                    "columns": ["population", "detected", "reference", "area_km2"],
                    "rows": [[10, True, True, 1], [20, True, False, 2], [30, False, True, 3], [40, False, False, 4]],
                }
            }
        ]
    }
    exposed = REGISTRY["exposure"].fn({}, entries, CTX)["exposure"]
    assert exposed["value"] == 30
    overlap = REGISTRY["validate_against"].fn({}, entries, CTX)["overlap"]
    assert overlap["value"] == 100 / 6
    assert overlap["table"]["rows"] == [[3, 4, 1, 6]]
    entries["cells"][0]["table"]["rows"][0][1] = None
    assert "absence" in REGISTRY["exposure"].fn({}, entries, CTX)["exposure"]

def test_reference_outside_coverage_preserves_reason():
    absence = {"reason": "no_coverage"}
    result = REGISTRY["validate_against"].fn({}, {"mask": [{"absence": absence}]}, CTX)["overlap"]
    assert result["absence"] == absence
