"""The reference of the impact contrast as an outer ring (`reference_inner_km`), and `land_only` through read_series.

No network: synthetic zone and series; the ring geometry is the real one from hup_data.places."""
from types import SimpleNamespace

import pandas as pd
from shapely.geometry import shape

from hup_data import places
from hup_ops.impact import baci_contrast
from hup_ops.ingest import read_series
from hup_ops.registry import Context

ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [-0.42, 39.41, -0.40, 39.43],
        "geometry": {"type": "Polygon", "coordinates": [[[-0.42, 39.41], [-0.40, 39.41], [-0.40, 39.43],
                                                         [-0.42, 39.43], [-0.42, 39.41]]]}}
PERIOD = {"start": "2000-01-01", "end": "2009-12-31"}
SOURCE = {"source": "landsat-c2-l2", "url": "https://planetarycomputer.microsoft.com/"}
RING = {"en": "Reference: ring from 3 to 20 km from the edge of the zone.",
        "es": "Referencia: anillo de 3 a 20 km desde el borde de la zona."}


def monthly(shift=0.0):
    rows = [{"t": f"{year}-{month:02d}-01", "v": float(month) + (shift if year >= 2005 else 0)}
            for year in range(2000, 2010) for month in range(1, 13)]
    return {"data": pd.DataFrame(rows), "absence": None, "origin": "cache", "sources": [SOURCE], "unit": "°C",
            "support": {"resolution_m": 30}, "coverage": {"valid_pct": 100.0, "n_dates": len(rows)}}


def context(answer=monthly):
    asked = []

    def read(variable, zone, period, **options):
        asked.append({"zone": zone, "options": options})
        return answer()

    data = SimpleNamespace(read_series=read, reference_zone=places.reference, ring_zones=places.rings)
    return Context(ZONE, PERIOD, 1, data, lambda *_: None), asked


def test_without_the_new_parameters_the_reference_is_the_disc_as_before():
    ctx, asked = context()
    result = read_series({"variable": "lst", "area": "reference"}, {}, ctx)["series"]
    assert len(asked) == 1 and asked[0]["options"] == {}
    disc = places.reference(ZONE, 3)
    assert asked[0]["zone"]["id"] == disc["id"] and asked[0]["zone"]["geometry"] == disc["geometry"]
    assert result["zone"] == "test-zone-reference-3km"
    assert result["method"]["params"] == {"variable": "lst", "area": "reference", "reference_km": 3}
    assert "limits" not in result


def test_outer_reference_reads_only_the_annulus_and_says_so():
    ctx, asked = context()
    params = {"variable": "lst", "area": "reference", "reference_inner_km": 3, "reference_km": 20}
    result = read_series(params, {}, ctx)["series"]
    assert len(asked) == 1  # the 0-3 km disc is not read
    ring = shape(asked[0]["zone"]["geometry"])
    expected = places.rings(ZONE, [3, 20])[1]
    assert asked[0]["zone"]["id"] == expected["id"]
    assert ring.symmetric_difference(shape(expected["geometry"])).area < 1e-12
    assert ring.intersection(shape(places.reference(ZONE, 3)["geometry"])).area < 1e-10
    assert result["zone"] == "test-zone-reference-3-20km"
    assert result["method"]["params"]["area"] == "reference" and result["limits"] == [RING]


def test_a_bad_inner_edge_is_an_absence_not_an_error():
    ctx, asked = context()
    params = {"variable": "lst", "area": "reference", "reference_inner_km": 20, "reference_km": 3}
    result = read_series(params, {}, ctx)["series"]
    assert asked == [] and result["absence"]["reason"] == "not_computed"


def test_baci_takes_the_outer_ring_as_its_reference():
    site_ctx, _ = context(lambda: monthly(shift=4))
    ref_ctx, _ = context()
    site = read_series({"variable": "lst"}, {}, site_ctx)["series"]
    ref = read_series({"variable": "lst", "area": "reference", "reference_inner_km": 3, "reference_km": 20},
                      {}, ref_ctx)["series"]
    params = {"variable": "lst", "before": [2000, 2004], "after": [2005, 2009],
              "resamples": 50}
    contrast = baci_contrast(params, {"s1": [ref], "s2": [site]}, site_ctx)["contrast"]
    assert "absence" not in contrast and contrast["value"] == 4
    assert RING in contrast["limits"]


def test_land_only_reaches_the_reader_only_when_asked_and_its_note_is_a_limit():
    def masked():
        return {**monthly(), "land_mask": {"applied": True, "detail": {"en": "Water out.", "es": "Sin agua."}}}

    ctx, asked = context(masked)
    plain = read_series({"variable": "lst"}, {}, ctx)["series"]
    assert asked[-1]["options"] == {} and "limits" not in plain  # absent: called exactly as before
    off = read_series({"variable": "lst", "land_only": False}, {}, ctx)["series"]
    assert asked[-1]["options"] == {} and "limits" not in off
    on = read_series({"variable": "lst", "land_only": True}, {}, ctx)["series"]
    assert asked[-1]["options"] == {"land_only": True}
    assert on["limits"] == [{"en": "Water out.", "es": "Sin agua."}]


def test_a_masked_read_that_is_an_absence_still_says_there_was_no_water_tile():
    no_tile = {"applied": False, "detail": {"en": "No JRC tile here.", "es": "Sin tesela JRC aquí."}}

    def empty():
        return {"data": None, "absence": {"reason": "insufficient_qa", "detail": {"en": "None.", "es": "Ninguna."}},
                "origin": "cache", "sources": [SOURCE], "support": {}, "coverage": {}, "unit": None,
                "land_mask": no_tile}

    ctx, _ = context(empty)
    params = {"variable": "lst", "area": "reference", "reference_inner_km": 3, "reference_km": 20, "land_only": True}
    ref = read_series(params, {}, ctx)["series"]
    assert ref["absence"]["reason"] == "insufficient_qa"
    assert RING in ref["limits"] and no_tile["detail"] in ref["limits"]
