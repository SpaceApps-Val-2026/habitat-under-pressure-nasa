"""Package A2, climate cube: built from small synthetic files, and checked against the real files when they exist."""
import hashlib
import json
import random
import time

import numpy as np
import pytest
import xarray as xr

from hup_data import paths
from hup_data.prepare import climate
from hup_data.readers import climate as reader

LAT = np.arange(-59.875, 90, 0.25)
LON = np.arange(0.125, 360, 0.25)
SAGUNT = {"name": "Sagunt", "bbox": [-0.35, 39.60, -0.15, 39.75]}


def write_file(root, model, scenario, year, value):
    """A file shaped like the real ones: lon 0-360, fill value 1e20, one value everywhere and a marker near 0° E."""
    folder = root / "packages" / climate.SOURCE / model / scenario
    folder.mkdir(parents=True, exist_ok=True)
    grid = np.full((LAT.size, LON.size), value, dtype="float32")
    grid[:, LON > 359.5] = value + 1000  # just west of Greenwich: must land at about -0.25° E in the cube
    grid[LAT < 0, :] = np.nan
    ds = xr.Dataset({"tas_mean": (("lat", "lon"), grid, {"units": "degC"})}, coords={"lat": LAT, "lon": LON})
    path = folder / f"tas_{year}.nc"
    ds.to_netcdf(path, encoding={"tas_mean": {"_FillValue": 1e20}})
    relative = path.relative_to(root).as_posix()
    return {"source": climate.SOURCE, "item": relative, "scope": "global", "path": relative, "mb": 1, "version": "2.0",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "format": "netcdf", "license": "test",
            "ingest": {"date": "2026-11-12T00:00:00Z", "origin": "predownload", "url": "https://example.org"}}


@pytest.fixture
def small(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    lines = [write_file(tmp_path, model, scenario, year, value)
             for model, scenario, year, value in (("M1", "historical", 2000, 10), ("M1", "historical", 2001, 11),
                                                   ("M1", "ssp245", 2030, 13), ("M2", "ssp245", 2030, 15))]
    (tmp_path / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return tmp_path


def test_cube_is_built_once_and_says_what_it_holds(small):
    meta = climate.build()
    assert meta["rebuilt"] and meta["models"] == ["M1", "M2"] and meta["scenarios"] == ["historical", "ssp245"]
    assert meta["years_by_scenario"] == {"historical": [2000, 2001], "ssp245": [2030, 2030]}
    assert climate.build()["rebuilt"] is False  # same input files: nothing to do
    assert not (small / "ready" / "climate.zarr.building").exists()


def test_longitudes_are_rolled_and_missing_stays_missing(small):
    climate.build()
    got = reader.climate_series("tas_mean", SAGUNT)
    table = got["data"]
    assert got["absence"] is None and got["unit"] == "°C" and got["origin"] == "cache"
    # Sagunt is just west of Greenwich: it must read the marked cells, not the ones at 0.25° E.
    assert table.loc[(table.model == "M1") & (table.t == 2000), "v"].item() == pytest.approx(1010)
    assert table.loc[(table.model == "M2") & (table.t == 2030), "v"].item() == pytest.approx(1015)
    # M2 has no historical run and nobody has 2015: those rows do not exist; they are not zeros.
    assert len(table) == 4 and not ((table.model == "M2") & (table.scenario == "historical")).any()
    assert got["support"]["resolution_m"] == 27830 and got["support"]["cells"] < 1
    assert got["sources"][0]["source"] == climate.SOURCE and got["sources"][0]["url"].startswith("https://")


def test_a_zone_larger_than_a_cell_averages_the_cells_inside(small):
    climate.build()
    east_of_greenwich = {"name": "box", "bbox": [0.0, 40.0, 1.0, 41.0]}
    got = reader.climate_series("tas_mean", east_of_greenwich, {"start": "2030-01-01", "end": "2030-12-31"})
    assert set(got["data"].t) == {2030} and got["support"]["cells"] == pytest.approx(16)
    assert got["data"].v.tolist() == pytest.approx([13, 15])


def test_absences_have_a_reason(small, monkeypatch, tmp_path_factory):
    climate.build()
    south = reader.climate_series("tas_mean", {"name": "Antarctica", "bbox": [0, -80, 1, -79]})
    assert south["data"] is None and south["absence"]["reason"] == "no_coverage"
    sea = reader.climate_series("tas_mean", {"name": "no values", "bbox": [10, -30, 11, -29]})
    assert sea["absence"]["reason"] == "no_coverage" and sea["sources"]
    assert reader.climate_series("nope", SAGUNT)["absence"]["reason"] == "not_computed"
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path_factory.mktemp("empty")))
    assert reader.climate_series("tas_mean", SAGUNT)["absence"]["reason"] == "needs_package"


def test_a_new_file_rebuilds_the_cube(small):
    climate.build()
    line = write_file(small, "M2", "historical", 2000, 20)
    with (small / "manifest.jsonl").open("a", encoding="utf-8") as manifest:
        manifest.write(json.dumps(line) + "\n")
    assert climate.build()["rebuilt"] is True
    table = reader.climate_series("tas_mean", SAGUNT)["data"]
    assert table.loc[(table.model == "M2") & (table.scenario == "historical"), "v"].item() == pytest.approx(1020)


@pytest.mark.data
def test_real_cube_matches_the_original_files_and_is_fast():
    """With the real cube: one place, every model and scenario, under a second, equal to three files opened by hand."""
    meta = climate.read_meta()
    if meta is None:
        pytest.skip("the climate cube is not built: python -m hup_data.prepare climate")
    point = {"name": "Sagunt", "bbox": [-0.2701, 39.6799, -0.2699, 39.6801]}
    timings = []
    for _ in range(3):  # the best of three: a busy computer must not fail a test about the design
        start = time.perf_counter()
        got = reader.climate_series("tas_mean", point)
        timings.append(time.perf_counter() - start)
    elapsed = min(timings)
    assert got["absence"] is None and elapsed < 1.0, f"{elapsed:.2f} s"
    table = got["data"]
    assert set(table.model) == set(meta["models"]) and set(table.scenario) == set(meta["scenarios"])

    lines = [line for line in climate.inputs() if line["path"].rsplit("/", 1)[1].startswith("tas_")]
    for line in random.Random(20261113).sample(lines, 3):
        model, scenario, name = line["path"].split("/")[-3:]
        year = int(name[4:8])
        with xr.open_dataset(paths.data_dir() / line["path"]) as original:
            expected = float(original.tas_mean.sel(lat=39.68, lon=360 - 0.27, method="nearest"))
        value = table.loc[(table.model == model) & (table.scenario == scenario) & (table.t == year), "v"].item()
        assert value == pytest.approx(expected, abs=1e-4), line["path"]
