"""Population readers: GHSL (100 m tiles and 1 km, Mollweide) and SEDAC SSP (1/8 degree), on synthetic grids.

The test with the real files compares GHSL with the official figure of the INE: "Cifras oficiales de población de los
municipios españoles: Revisión del Padrón Municipal", 1 January 2025, table 2903 (Valencia/València),
https://www.ine.es/jaxiT3/Tabla.htm?t=2903, read on 2026-11-14 from servicios.ine.es/wstempus/js/ES/DATOS_TABLA/2903.
"""
import json

import numpy as np
import pytest
import rasterio
import shapely
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from hup_data import readers
from hup_data.readers import population

MOLLWEIDE = "ESRI:54009"
GHSL_KEY = "ghsl/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.tif"
SEDAC_DIR = "sedac-ssp/popdynamics-1-8th-pop-base-year-projection-ssp-2000-2100-rev01-proj-ssp2-netcdf"
GHSL_ORIGIN = (-30000.0, 4770000.0)  # Mollweide metres, about 0.35 degrees west of Greenwich at 39.6 N


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    return tmp_path


def write_grid(data, key, values, transform, crs, nodata, source, entries):
    path = data / "ready" / "grids" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1], count=1,
                       dtype=values.dtype, crs=crs, transform=transform, nodata=nodata) as out:
        out.write(values, 1)
    entries[key] = {"source": source, "path": path.relative_to(data).as_posix(), "version": "test",
                    "license": "test", "from": f"packages/{source}/{key.split('/')[1]}.zip"}
    (data / "ready" / "grids" / "index.json").write_text(json.dumps({"entries": entries}), encoding="utf-8")


@pytest.fixture
def ghsl(data):
    """A 20 x 20 grid of 1 km cells in Mollweide; cell (row, col) holds 100 * row + col people."""
    values = (100 * np.arange(20)[:, None] + np.arange(20)[None, :]).astype("float64")
    write_grid(data, GHSL_KEY, values, from_origin(*GHSL_ORIGIN, 1000, 1000), MOLLWEIDE, -200.0, "ghsl", {})
    return values


def to_wgs84(geometry):
    back = Transformer.from_crs(MOLLWEIDE, 4326, always_xy=True).transform
    return shapely.transform(shapely.segmentize(geometry, 50), lambda xy: np.column_stack(back(xy[:, 0], xy[:, 1])))


def mollweide_zone(x0, y0, x1, y1):
    """A zone given in Mollweide metres, as the planner would give it: in EPSG:4326, with its edges densified."""
    outline = to_wgs84(box(x0, y0, x1, y1))
    return {"id": "z", "name": "z", "bbox": list(outline.bounds), "geometry": mapping(outline)}


def cell_block(row0, row1, col0, col1):
    x, y = GHSL_ORIGIN
    return mollweide_zone(x + 1000 * col0, y - 1000 * row1, x + 1000 * col1, y - 1000 * row0)


def test_ghsl_sum_of_whole_cells_is_exact(ghsl):
    got = population.ghsl_population("population", cell_block(3, 7, 4, 9))
    assert got["absence"] is None and got["unit"] == "people"
    assert got["data"].t.tolist() == [2025]
    assert got["data"].v.iloc[0] == pytest.approx(ghsl[3:7, 4:9].sum(), rel=1e-6)
    assert got["support"] == {"resolution_m": 1000, "cells": pytest.approx(20, rel=1e-4)}
    assert got["coverage"]["valid_pct"] == 100 and got["sources"][0]["source"] == "ghsl"


def test_ghsl_part_of_a_cell_counts_its_share_and_support_says_the_cell_is_larger(ghsl):
    x, y = GHSL_ORIGIN
    quarter = mollweide_zone(x + 5000, y - 6000, x + 5500, y - 5500)  # a quarter of cell (5, 5)
    got = population.ghsl_population("population", quarter)
    assert got["data"].v.iloc[0] == pytest.approx(ghsl[5, 5] / 4, rel=1e-4)
    assert got["support"]["cells"] == pytest.approx(0.25, rel=1e-3)
    assert any("smaller than one grid cell" in limit["en"] for limit in got["limits"])


def test_ghsl_zone_is_reprojected_not_the_grid_a_triangle_is_half_a_square(ghsl, data):
    flat = np.full((20, 20), 10.0)
    write_grid(data, GHSL_KEY, flat, from_origin(*GHSL_ORIGIN, 1000, 1000), MOLLWEIDE, -200.0, "ghsl", {})
    x, y = GHSL_ORIGIN
    triangle = to_wgs84(shapely.Polygon([(x + 2000, y - 12000), (x + 12000, y - 12000), (x + 2000, y - 2000)]))
    got = population.ghsl_population("population", {"id": "t", "bbox": list(triangle.bounds),
                                                    "geometry": mapping(triangle)})
    assert got["data"].v.iloc[0] == pytest.approx(10.0 * 50, rel=1e-4)  # 50 km2 at 10 people per km2


def test_ghsl_nodata_is_left_out_and_reported(ghsl, data):
    values = ghsl.copy()
    values[3, 4] = -200.0
    write_grid(data, GHSL_KEY, values, from_origin(*GHSL_ORIGIN, 1000, 1000), MOLLWEIDE, -200.0, "ghsl", {})
    got = population.ghsl_population("population", cell_block(3, 5, 4, 6))
    assert got["data"].v.iloc[0] == pytest.approx(ghsl[3:5, 4:6].sum() - ghsl[3, 4], rel=1e-6)
    assert got["coverage"]["valid_pct"] == pytest.approx(75, abs=0.01)
    assert any("no population data" in limit["en"] for limit in got["limits"])


def test_ghsl_absences_carry_their_reason(ghsl, data):
    far = {"id": "far", "bbox": [100.0, 10.0, 100.1, 10.1]}
    assert population.ghsl_population("population", far)["absence"]["reason"] == "no_coverage"
    (data / "ready" / "grids" / "index.json").write_text('{"entries": {}}', encoding="utf-8")
    missing = population.ghsl_population("population", cell_block(0, 1, 0, 1))
    assert missing["absence"]["reason"] == "needs_package" and missing["sources"][0]["source"] == "ghsl"


def test_ghsl_answers_through_the_common_door(ghsl):
    got = readers.read_series("population", cell_block(0, 2, 0, 2), {"start": "2025-01-01", "end": "2025-12-31"})
    assert got["absence"] is None and got["data"].v.iloc[0] == pytest.approx(ghsl[0:2, 0:2].sum(), rel=1e-6)
    again = readers.read_series("population", cell_block(0, 2, 0, 2), {"start": "2025-01-01", "end": "2025-12-31"})
    assert again["origin"] == "derived"


# ---------------------------------------------------------------- GHSL at 100 m and 1 km together
TILE = "R5_C19"


def tile_key(epoch):
    name = f"GHS_POP_E{epoch}_GLOBE_R2023A_54009_100_V1_0_{TILE}"
    return f"ghsl/{name}/{name}.tif"


def concentrated(coarse, row0, row1, col0, col1):
    """A 100 m grid over km cells rows row0..row1, cols col0..col1, each km cell's people in its top-left 100 m cell."""
    fine = np.zeros(((row1 - row0) * 10, (col1 - col0) * 10))
    fine[::10, ::10] = coarse[row0:row1, col0:col1]
    return fine


@pytest.fixture
def both(data, ghsl):
    """The 1 km grid of `ghsl` plus 100 m tiles for 2020 and 2025 over km rows and cols 2..12, same people per km."""
    entries = json.loads((data / "ready" / "grids" / "index.json").read_text(encoding="utf-8"))["entries"]
    x, y = GHSL_ORIGIN
    for epoch, scale in ((2020, 0.5), (2025, 1.0)):
        write_grid(data, tile_key(epoch), scale * concentrated(ghsl, 2, 12, 2, 12),
                   from_origin(x + 2000, y - 2000, 100, 100), MOLLWEIDE, -200.0, "ghsl", entries)
    return entries


def drop(data, entries, *keys):
    index = data / "ready" / "grids" / "index.json"
    index.write_text(json.dumps({"entries": {k: v for k, v in entries.items() if k not in keys}}), encoding="utf-8")


def test_ghsl_uses_the_100m_tiles_when_they_cover_the_zone_and_the_sum_is_the_same(both, ghsl):
    got = population.ghsl_population("population", cell_block(3, 7, 4, 9))
    assert got["absence"] is None and got["data"].t.tolist() == [2020, 2025]
    assert got["data"].v.tolist() == pytest.approx([0.5 * ghsl[3:7, 4:9].sum(), ghsl[3:7, 4:9].sum()], rel=1e-9)
    assert got["support"] == {"resolution_m": 100, "cells": pytest.approx(2000, rel=1e-4)}
    source = got["sources"][0]
    assert source["source"] == "ghsl" and source["product"] == "GHS-POP 100 m"
    assert source["items"] == [tile_key(2020).rsplit("/", 1)[1][:-4], tile_key(2025).rsplit("/", 1)[1][:-4]]


def test_ghsl_100m_sees_where_the_people_are_and_1km_spreads_them(both, ghsl, data):
    x, y = GHSL_ORIGIN
    corner = mollweide_zone(x + 5000, y - 5100, x + 5100, y - 5000)  # the top-left 100 m cell of km cell (5, 5)
    fine = population.ghsl_population("population", corner)
    assert fine["support"]["resolution_m"] == 100 and fine["data"].v.iloc[-1] == pytest.approx(ghsl[5, 5], rel=1e-4)
    drop(data, both, tile_key(2020), tile_key(2025))
    coarse = population.ghsl_population("population", corner)
    assert coarse["support"]["resolution_m"] == 1000
    assert coarse["data"].v.iloc[-1] == pytest.approx(ghsl[5, 5] / 100, rel=1e-3)


def test_ghsl_two_grids_of_one_epoch_do_not_overwrite_each_other_and_1km_answers_outside_the_tile(both, ghsl):
    """Regression: with a 100 m tile and the 1 km grid of the same epoch, a zone outside the tile must use 1 km."""
    grids = population.ghsl_grids()
    assert sorted(grids) == [100, 1000] and sorted(grids[100]) == [2020, 2025] and sorted(grids[1000]) == [2025]
    got = population.ghsl_population("population", cell_block(10, 14, 10, 14))  # crosses the tile's edge
    assert got["absence"] is None and got["support"]["resolution_m"] == 1000
    assert got["data"].t.tolist() == [2025] and got["data"].v.iloc[0] == pytest.approx(ghsl[10:14, 10:14].sum(), 1e-6)
    assert got["sources"][0]["product"] == "GHS-POP 1 km"
    assert any("does not cover the whole zone" in limit["en"] and "R" in limit["en"] for limit in got["limits"])


def test_ghsl_only_tiles_and_a_zone_outside_them_needs_the_named_tile(both, data):
    drop(data, both, GHSL_KEY)
    got = population.ghsl_population("population", cell_block(10, 14, 10, 14))
    assert got["absence"]["reason"] == "needs_package"
    missing = population.tiles_for(population._project(population.zone_geometry(cell_block(10, 14, 10, 14)),
                                                       MOLLWEIDE))
    assert missing and all(tile in got["absence"]["detail"]["en"] for tile in missing)


@pytest.fixture
def sedac(data):
    """Two scenarios and two decades on a 1/8 degree grid over 0..2 E, 39..41 N; the base year 2000 too."""
    entries, transform = {}, from_origin(0.0, 41.0, 0.125, 0.125)
    grids = {}
    for scenario, year, scale in (("ssp2", 2050, 1), ("ssp2", 2100, 2), ("ssp5", 2050, 3)):
        values = (scale * (1000 + np.arange(256).reshape(16, 16))).astype("int32")
        write_grid(data, f"{SEDAC_DIR}/{scenario}_{year}.nc", values, transform, None, 2147483647, "sedac-ssp",
                   entries)
        grids[(scenario, year)] = values
    base = np.full((16, 16), 7, dtype="int32")
    write_grid(data, "sedac-ssp/popdynamics-byr-netcdf/2000total.nc", base, transform, None, 2147483647,
               "sedac-ssp", entries)
    return grids


def test_sedac_series_by_decade_and_scenario_sums_whole_cells(sedac):
    zone = {"id": "s", "bbox": [0.25, 40.25, 0.75, 40.5]}  # rows 4..5, columns 2..5: eight whole cells
    got = population.sedac_population("population_projected", zone, {"start": "2000-01-01", "end": "2100-12-31"})
    assert got["absence"] is None and set(got["data"].columns) == {"t", "v", "scenario"}
    table = got["data"].set_index(["scenario", "t"]).v
    for key, values in sedac.items():
        assert table[key] == pytest.approx(values[4:6, 2:6].sum(), rel=1e-6)
    assert table[("historical", 2000)] == pytest.approx(7 * 8, rel=1e-6)
    assert got["support"]["cells"] == pytest.approx(8, rel=1e-6) and 10000 < got["support"]["resolution_m"] < 14000


def test_sedac_period_and_coverage(sedac):
    zone = {"id": "s", "bbox": [0.25, 40.25, 0.75, 40.5]}
    only_2050 = population.sedac_population("population_projected", zone, {"start": "2041-01-01", "end": "2060-12-31"})
    assert sorted(only_2050["data"].scenario) == ["ssp2", "ssp5"] and set(only_2050["data"].t) == {2050}
    none = population.sedac_population("population_projected", zone, {"start": "2101-01-01", "end": "2110-12-31"})
    assert none["absence"]["reason"] == "no_coverage"
    far = population.sedac_population("population_projected", {"id": "f", "bbox": [10.0, 10.0, 10.1, 10.1]}, None)
    assert far["absence"]["reason"] == "no_coverage"


def test_sedac_half_a_cell_is_half_of_it(sedac):
    zone = {"id": "h", "bbox": [0.25, 40.25, 0.3125, 40.375]}  # the western half of cell (5, 2)
    got = population.sedac_population("population_projected", zone, {"start": "2050-01-01", "end": "2050-12-31"})
    ssp2 = got["data"].set_index("scenario").v["ssp2"]
    assert ssp2 == pytest.approx(sedac[("ssp2", 2050)][5, 2] / 2, rel=1e-3)
    assert got["support"]["cells"] == pytest.approx(0.5, rel=1e-3)


# ---------------------------------------------------------------- with the real files
INE_2025 = {"46017": ("Alzira", 48236), "46250": ("València", 840792)}  # table 2903, 1 January 2025


def municipality(code):
    from hup_data.readers import vectors

    found = vectors.read_layer("ine/seccionado_2026__SECC_CE_20260101", [-0.6, 39.0, -0.1, 39.8])
    if found["absence"] is not None:
        pytest.skip("The INE census sections are not prepared on this computer")
    outline = shapely.union_all(found["data"][found["data"].CUMUN == code].geometry.values)
    return {"id": f"ine-{code}", "bbox": list(outline.bounds), "geometry": mapping(outline)}


@pytest.mark.data
@pytest.mark.parametrize("code", sorted(INE_2025))
def test_real_ghsl_population_of_a_municipality_is_within_15_percent_of_the_ine(code):
    if not population.ghsl_grids():
        pytest.skip("GHSL is not prepared on this computer")
    got = population.ghsl_population("population", municipality(code))
    official = INE_2025[code][1]
    assert got["absence"] is None and abs(got["data"].v.iloc[-1] - official) / official < 0.15


@pytest.mark.data
@pytest.mark.parametrize("code", ["46017", "46220", "46250"])  # Alzira, Sagunt, València
def test_real_ghsl_100m_and_1km_agree_on_a_municipality_total(code):
    """The finest grid is chosen and, over a whole municipality, it moves the total by a few per cent at most."""
    grids = population.ghsl_grids()
    if 100 not in grids or 1000 not in grids:
        pytest.skip("GHSL at 100 m and 1 km are not both prepared on this computer")
    zone = municipality(code)
    got = population.ghsl_population("population", zone)
    coarse = population.ghsl_count(population.zone_geometry(zone), grids[1000][2025])["people"]
    fine = got["data"].set_index("t").v[2025]
    assert got["absence"] is None and got["support"]["resolution_m"] == 100
    assert got["sources"][0]["product"] == "GHS-POP 100 m" and abs(fine - coarse) / coarse < 0.05
