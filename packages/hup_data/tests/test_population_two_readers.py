"""The two readers of the GHSL grid give the same people for the same polygon.

`readers/exposure.py::_population` (cell by cell, used by `read_exposure`) and `readers/population.py::cells` with
`total` (vectorised, used by `masks.population_in_mask`) both share each cell by the part of its area the polygon
covers. They differ only with missing values: the first gives up, the second leaves the cell out and says how much.
Neither function is changed here; see .dev/notes/2026-11-14-pruebas-reales-agua-poblacion.md.
"""
import numpy as np
import pytest
import rasterio
import shapely
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from hup_data import paths
from hup_data.readers import exposure, population

ORIGIN = (-30000.0, 4770000.0)  # Mollweide metres, near València
NODATA = -200.0


def grid(values):
    memory = MemoryFile()
    with memory.open(driver="GTiff", height=values.shape[0], width=values.shape[1], count=1, dtype="float64",
                     crs="ESRI:54009", transform=from_origin(*ORIGIN, 1000, 1000), nodata=NODATA) as out:
        out.write(values, 1)
    return memory


def both(raster, polygon):
    counts, _ = exposure._population(raster, polygon)
    found = population.cells(raster, polygon)
    other = population.total(raster.read(1, window=found["window"]).astype("float64"), raster.nodata, found["share"])
    return (None if counts is None else sum(counts)), other


def test_same_sum_on_whole_and_partial_cells():
    values = (100 * np.arange(8)[:, None] + np.arange(8)[None, :]).astype("float64")
    x, y = ORIGIN
    polygon = box(x + 500, y - 3500, x + 3000, y - 1000)  # rows 1-2 whole and half of row 3; half of col 0, cols 1-2
    share = np.outer([1, 1, 0.5], [0.5, 1, 1])
    expected = float((values[1:4, 0:3] * share).sum())
    with grid(values) as memory, memory.open() as raster:
        old, new = both(raster, polygon)
    assert old == pytest.approx(expected, rel=1e-9) and new["people"] == pytest.approx(expected, rel=1e-9)
    assert new["missing"] == 0 and new["cells"] == pytest.approx(share.sum())


def test_with_no_data_inside_they_differ_on_purpose():
    """exposure turns the whole figure into an absence; population leaves the cell out and reports its share."""
    values = np.full((8, 8), 10.0)
    values[2, 1] = NODATA
    x, y = ORIGIN
    with grid(values) as memory, memory.open() as raster:
        old, new = both(raster, box(x, y - 4000, x + 4000, y))
    assert old is None
    assert new["people"] == pytest.approx(150.0) and new["missing"] == pytest.approx(1.0)


# ---------------------------------------------------------------- with the real files
@pytest.mark.data
@pytest.mark.parametrize("code", ["46094", "46250"])  # Catarroja and València: flooded on 29 October 2024
def test_real_emsr773_flood_gives_the_same_people_with_both_readers(code):
    """The flood polygon that `read_exposure` builds from its catalogue defaults (EMSR773, flooded area)."""
    import yaml
    from pyproj import CRS

    from hup_data.readers import vectors

    operation = next(op for op in yaml.safe_load((paths.CATALOG_DIR / "operations.yaml").read_text(encoding="utf-8"))
                     if op.get("id") == "read_exposure")
    defaults = operation["params"]["properties"]
    spec = next(s for s in defaults["flood_layers"]["default"] if s["source"] == "copernicus-ems-emsr773")
    year = defaults["grid"]["default"]["year"]
    entries = population.ghsl_grids().get(1000, {}).get(year, [])
    sections = vectors.read_layer("ine/seccionado_2026__SECC_CE_20260101", [-0.6, 39.2, -0.2, 39.6])
    if len(entries) != 1 or sections["absence"] is not None:
        pytest.skip("GHSL or the INE census sections are not prepared on this computer")
    outline = shapely.union_all(sections["data"][sections["data"].CUMUN == code].geometry.values)
    zone = {"id": code, "bbox": list(outline.bounds), "geometry": mapping(outline)}
    with rasterio.open(paths.data_dir() / entries[0]["path"]) as raster:
        crs = CRS.from_user_input(raster.crs)
        found = exposure._mask(spec, exposure._project(shapely.make_valid(outline), crs), zone["bbox"], crs)
        if found is None:
            pytest.skip("EMSR773 is not prepared on this computer")
        old, new = both(raster, found[0])
    assert old > 0 and new["missing"] == 0
    assert new["people"] == pytest.approx(old, rel=1e-9)
