"""A11: COGs the browser reads by ranges, and zone clips of gridded sources. Synthetic files only, no network."""
import json

import h5py
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from hup_data import coverage, layers, store

ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [-0.35, 39.60, -0.15, 39.75]}
PERIOD = {"start": "2000-01-01", "end": "2001-12-31", "months": [1, 2]}
LON = np.arange(-179.95, 180, 0.1)
LAT = np.arange(-89.95, 90, 0.1)


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    return tmp_path


def write_imerg(data, year, month, rate):
    """Shaped like the real files: group Grid, axes (time, lon, lat), mm/hour, a fill value east of Greenwich.

    The rate grows towards the north-east inside the zone, to prove the clip is not flipped or transposed.
    """
    folder = data / "packages" / "imerg-monthly"
    folder.mkdir(parents=True, exist_ok=True)
    lon, lat = LON.astype("float32"), LAT.astype("float32")
    grid = np.full((1, lon.size, lat.size), rate, dtype="float32")
    grid[0] += (np.arange(lon.size)[:, None] % 10) * 0.001 + (np.arange(lat.size)[None, :] % 10) * 0.01
    grid[0, lon > 0, :] = -9999.9
    path = folder / f"imerg_{year}{month:02d}.HDF5"
    with h5py.File(path, "w") as file:
        group = file.create_group("Grid")
        group["lon"], group["lat"] = lon, lat
        group.create_dataset("precipitation", data=grid).attrs["_FillValue"] = np.array([-9999.9], dtype="float32")
    relative = path.relative_to(data).as_posix()
    last = {1: 31, 2: 29 if year % 4 == 0 else 28}[month]
    return {"source": "imerg-monthly", "item": relative, "scope": "global", "path": relative, "mb": 1,
            "sha256": "0" * 64, "format": "hdf5", "license": "test",
            "period": {"start": f"{year}-{month:02d}-01", "end": f"{year}-{month:02d}-{last}"},
            "ingest": {"date": "2026-11-12T00:00:00Z", "origin": "predownload", "url": "https://example.org"}}


def imerg_package(data, months):
    lines = [write_imerg(data, year, month, rate) for year, month, rate in months]
    (data / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    coverage.rebuild({"imerg-monthly": ["precipitation"]})


def test_write_cog_gives_blocks_overviews_nodata_and_projection(tmp_path):
    values = np.random.default_rng(1).random((1300, 900)).astype("float32")
    values[:5, :5] = -9999.0
    path = tmp_path / "values.tif"
    layers.write_cog(values, path, nodata=-9999.0, kind="values", transform=from_origin(0, 1, 0.001, 0.001))
    assert layers.check_cog(path) == []
    with rasterio.open(path) as cog:
        assert cog.nodata == -9999.0 and cog.crs.to_epsg() == 4326 and cog.overviews(1)
        assert cog.tags(ns="IMAGE_STRUCTURE")["OVERVIEW_RESAMPLING"] == "AVERAGE"
        assert np.array_equal(cog.read(1), values)

    classes = (np.arange(600 * 600).reshape(600, 600) % 5).astype("uint8")
    layers.write_cog(classes, tmp_path / "classes.tif", nodata=255, kind="classes",
                     transform=from_origin(0, 1, 0.001, 0.001))
    assert layers.check_cog(tmp_path / "classes.tif") == []
    with rasterio.open(tmp_path / "classes.tif") as cog:
        assert cog.tags(ns="IMAGE_STRUCTURE")["OVERVIEW_RESAMPLING"] == "NEAREST"
    with pytest.raises(ValueError):
        layers.write_cog(classes, tmp_path / "x.tif", nodata=255, kind="colours", transform=from_origin(0, 1, 1, 1))


def test_a_plain_tiff_fails_the_check(tmp_path):
    path = tmp_path / "plain.tif"
    with rasterio.open(path, "w", driver="GTiff", width=600, height=600, count=1, dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(0, 1, 0.001, 0.001)) as plain:
        plain.write(np.zeros((1, 600, 600), dtype="uint8"))
    assert "layout is not COG" in layers.check_cog(path) and "no nodata value" in layers.check_cog(path)


def test_zone_clip_per_year_shares_one_range_and_is_reused(data):
    imerg_package(data, [(2000, 1, 0.1), (2000, 2, 0.2), (2001, 1, 0.3), (2001, 2, 0.4)])
    got = layers.zone_layers("precipitation", ZONE, PERIOD)
    assert got["absence"] is None and got["origin"] == "package" and got["unit"] == "mm/month"
    assert got["scale"] == "water" and got["data_class"] == "measured" and got["format"] == "cog"
    assert [frame["t"] for frame in got["frames"]] == ["2000", "2001"]
    # Cells that touch the bbox: columns centred at -0.35, -0.25, -0.15; rows at 39.65 and 39.75 (the cell centred
    # at 39.55 ends exactly at 39.60, so it only borders the zone).
    assert got["shape"] == [2, 3]
    assert got["bounds"] == pytest.approx([-0.4, 39.6, -0.1, 39.8], abs=1e-4)
    assert got["sources"][0]["source"] == "imerg-monthly" and got["sources"][0]["n_items"] == 4
    assert got["support"]["resolution_m"] == 11100 and got["support"]["cells"] == 6

    lows, highs = [], []
    for frame in got["frames"]:
        path = store.derived_path(got["fingerprint"], frame["file"])
        assert layers.check_cog(path) == []
        with rasterio.open(path) as cog:
            pixels = cog.read(1)
            assert cog.nodata == layers.NODATA and tuple(cog.bounds) == pytest.approx(got["bounds"], abs=1e-4)
        assert (pixels != layers.NODATA).all() and frame["coverage"]["valid_pct"] == 100.0
        # North-east is the largest value: row 0 is north, the last column is east.
        assert pixels[0, -1] == pixels.max() and pixels[-1, 0] == pixels.min()
        lows.append(pixels.min())
        highs.append(pixels.max())
    assert got["range"] == pytest.approx([min(lows), max(highs)], rel=1e-6)
    # 2000: January 0.1 mm/h * 744 h and February 0.2 * 696 h, plus the gradient; the mean of the two months.
    assert min(lows) > (0.1 * 744 + 0.2 * 696) / 2

    again = layers.zone_layers("precipitation", ZONE, PERIOD)
    assert again["origin"] == "derived" and again["range"] == got["range"]
    assert again["fingerprint"] == got["fingerprint"]
    other = layers.zone_layers("precipitation", ZONE, {**PERIOD, "end": "2000-12-31"})
    assert other["fingerprint"] != got["fingerprint"] and len(other["frames"]) == 1


def test_outline_masks_cells_and_a_missing_month_is_an_absent_year(data):
    imerg_package(data, [(2000, 1, 0.1), (2000, 2, 0.2), (2001, 1, 0.3)])
    triangle = {"type": "Polygon", "coordinates": [[[-0.34, 39.61], [-0.34, 39.62], [-0.33, 39.61], [-0.34, 39.61]]]}
    got = layers.zone_layers("precipitation", {**ZONE, "id": "tri", "geometry": triangle}, PERIOD)
    assert got["support"]["cells"] == 1 and any("outline" in limit["en"] for limit in got["limits"])
    first, second = got["frames"]
    assert first["coverage"]["valid_pct"] == 100.0  # of the cells inside the outline
    assert "file" not in second and second["absence"]["reason"] == "insufficient_qa"
    with rasterio.open(store.derived_path(got["fingerprint"], first["file"])) as cog:
        assert (cog.read(1) != layers.NODATA).sum() == 1


def test_absences_have_reasons_and_write_nothing(data):
    imerg_package(data, [(2000, 1, 0.1)])
    east = layers.zone_layers("precipitation", {"name": "east", "bbox": [5.0, 40.0, 5.5, 40.5]}, PERIOD)
    assert east["absence"]["reason"] == "insufficient_qa" and east["frames"] == []
    before = layers.zone_layers("precipitation", ZONE, {"start": "1990-01-01", "end": "1990-12-31"})
    assert before["absence"]["reason"] == "needs_package" and before["sources"][0]["source"] == "imerg-monthly"
    unknown = layers.zone_layers("air_temperature", ZONE, PERIOD)  # a point series: no grid to draw
    assert unknown["absence"]["reason"] == "not_computed" and unknown["sources"]
    early = layers.zone_layers("ndvi", ZONE, PERIOD)  # Landsat 8 starts in 2013
    assert early["absence"]["reason"] == "no_coverage" and early["sources"][0]["source"] == "landsat-c2-l2"
    assert not (data / "derived").exists()


def test_layer_result_is_a_contract_body(data):
    imerg_package(data, [(2000, 1, 0.1), (2000, 2, 0.2)])
    meta = layers.zone_layers("precipitation", ZONE, PERIOD)
    body = layers.layer_result(meta, meta["frames"][0], "layers/precipitation-2000.tif")
    assert body["layer"] == {"path": "layers/precipitation-2000.tif", "format": "cog", "range": meta["range"],
                             "scale": "water"}
    assert body["period"] == {"start": "2000-01-01", "end": "2000-12-31", "months": [1, 2]}
    absent = layers.layer_result(meta, {"t": "2001", "absence": {"reason": "no_coverage"}}, "unused")
    assert "layer" not in absent and absent["absence"]["reason"] == "no_coverage"


def test_climate_cube_median_of_models_with_historical_where_the_scenario_has_no_year(data):
    import hashlib

    import xarray as xr

    from hup_data.prepare import climate

    lat, lon = np.arange(-59.875, 90, 0.25), np.arange(0.125, 360, 0.25)

    def write_file(root, model, scenario, year, value):
        """Shaped like the real files: lon 0-360, fill value 1e20, one value everywhere."""
        folder = root / "packages" / climate.SOURCE / model / scenario
        folder.mkdir(parents=True, exist_ok=True)
        grid = np.full((lat.size, lon.size), value, dtype="float32")
        ds = xr.Dataset({"tas_mean": (("lat", "lon"), grid, {"units": "degC"})}, coords={"lat": lat, "lon": lon})
        path = folder / f"tas_{year}.nc"
        ds.to_netcdf(path, encoding={"tas_mean": {"_FillValue": 1e20}})
        relative = path.relative_to(root).as_posix()
        return {"source": climate.SOURCE, "item": relative, "scope": "global", "path": relative, "mb": 1,
                "version": "2.0", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "format": "netcdf",
                "license": "test",
                "ingest": {"date": "2026-11-12T00:00:00Z", "origin": "predownload", "url": "https://example.org"}}

    lines = [write_file(data, model, scenario, year, value)
             for model, scenario, year, value in (("M1", "historical", 2000, 10), ("M1", "ssp245", 2030, 13),
                                                   ("M2", "ssp245", 2030, 15), ("M3", "ssp245", 2030, 20))]
    (data / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    climate.build()
    box = {"name": "box", "bbox": [0.1, 40.1, 0.4, 40.4]}
    assert "tas_mean" in layers.variables()
    got = layers.zone_layers("tas_mean", box, {"start": "2000-01-01", "end": "2030-12-31"}, scenario="ssp245")
    assert got["absence"] is None and got["unit"] == "°C" and got["data_class"] == "extrapolated"
    assert got["scale"] == "climate" and got["sources"][0]["source"] == climate.SOURCE
    frames = {frame["t"]: frame for frame in got["frames"]}
    assert frames["2000"]["scenario"] == "historical" and frames["2030"]["scenario"] == "ssp245"
    assert frames["2030"]["coverage"]["n_dates"] == 3
    with rasterio.open(store.derived_path(got["fingerprint"], frames["2030"]["file"])) as cog:
        assert np.allclose(cog.read(1), 15)  # median of 13, 15 and 20
    assert got["range"] == [10.0, 15.0]
    years_without_any_model = [frame for frame in got["frames"] if frame.get("absence")]
    assert years_without_any_model and all(f["absence"]["reason"] == "needs_package" for f in years_without_any_model)
