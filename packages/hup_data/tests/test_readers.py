"""Package A3: readers behind one door. Network answers are recorded and files are synthetic: no network here."""
import hashlib
import json

import h5py
import numpy as np
import pytest
import requests

from hup_data import coverage, paths, readers
from hup_data.readers import imerg, nasa_power

ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [-0.35, 39.60, -0.15, 39.75]}
PERIOD = {"start": "2000-01-01", "end": "2001-12-31"}


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    return tmp_path


def manifest_lines(data):
    file = data / "manifest.jsonl"
    return [json.loads(text) for text in file.read_text(encoding="utf-8").splitlines()] if file.is_file() else []


# ---------------------------------------------------------------- NASA POWER
def power_answer(parameter, lat, lon, last_year):
    """Shaped like the API: YYYYMM keys, a thirteenth month with the annual value, -999 for no data."""
    values = {}
    for year in range(1981, last_year + 1):
        for month in range(1, 13):
            values[f"{year}{month:02d}"] = 10.0 + month if (year, month) != (2000, 3) else -999.0
        values[f"{year}13"] = 99.0
    return values, f"https://power.example/api?parameters={parameter}&latitude={lat}&longitude={lon}"


def test_power_is_asked_once_cached_and_recorded(data, monkeypatch):
    calls = []
    monkeypatch.setattr(nasa_power, "_fetch", lambda *args: calls.append(args) or power_answer(*args))
    first = readers.read_series("air_temperature", ZONE, PERIOD)
    assert first["absence"] is None and first["origin"] == "remote" and first["unit"] == "°C"
    table = first["data"]
    assert len(table) == 23 and "2000-03-01" not in set(table.t)  # the month without data is absent, not zero
    assert table.loc[table.t == "2000-07-01", "v"].item() == 17.0 and 99.0 not in set(table.v)
    assert first["support"]["resolution_m"] == 55000 and first["support"]["cells"] < 1
    assert calls == [("T2M", 39.67, -0.25, calls[0][3])]

    line = manifest_lines(data)[-1]
    assert line["source"] == "nasa-power" and line["ingest"]["origin"] == "on_demand" and line["format"] == "csv"
    assert line["sha256"] == hashlib.sha256((data / line["path"]).read_bytes()).hexdigest()

    again = readers.read_series("air_temperature", ZONE, PERIOD)
    assert again["origin"] == "derived" and again["data"].equals(table) and len(calls) == 1
    other_period = readers.read_series("air_temperature", ZONE, {"start": "1990-01-01", "end": "1990-12-31"})
    assert other_period["origin"] == "cache" and len(other_period["data"]) == 12 and len(calls) == 1
    assert len(manifest_lines(data)) == 1  # one file, one line, however many times it is read


def test_power_daily_maximum_is_the_mean_of_daily_maxima(data, monkeypatch):
    # Monthly T2M_MAX is the hottest hour of the month; T2M_MAX_AVG is the mean of the daily maxima, which is what
    # the catalogue promises (T4 of A8, decided by Guillem on 14 November 2026).
    calls = []
    monkeypatch.setattr(nasa_power, "_fetch", lambda *args: calls.append(args) or power_answer(*args))
    read = readers.read_series("air_temperature_max", ZONE, PERIOD)
    assert read["absence"] is None and read["unit"] == "°C"
    assert [call[0] for call in calls] == ["T2M_MAX_AVG"]
    assert (data / "cache" / "nasa-power" / "+39.67_-0.25" / "T2M_MAX_AVG.csv").is_file()


def test_power_offline_unreachable_and_before_its_record(data, monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    offline = readers.read_series("air_temperature", ZONE, PERIOD)
    assert offline["data"] is None and offline["absence"]["reason"] == "source_unreachable" and offline["sources"]
    monkeypatch.delenv("HUP_OFFLINE")

    def down(*args):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(nasa_power, "_fetch", down)
    assert readers.read_series("air_temperature", ZONE, PERIOD)["absence"]["reason"] == "source_unreachable"
    assert manifest_lines(data) == []  # a failed request leaves no file and no line

    monkeypatch.setattr(nasa_power, "_fetch", power_answer)
    early = readers.read_series("air_temperature", ZONE, {"start": "1950-01-01", "end": "1960-12-31"})
    assert early["absence"]["reason"] == "no_coverage"
    # An absence is not saved as a result: asking again reads again.
    assert readers.read_series("air_temperature", ZONE, PERIOD)["origin"] in ("cache", "remote")


SUMMER, SOUTH_SUMMER = [6, 7, 8, 9], [12, 1, 2, 3]  # hup_ops.seasons: north, and south (crosses the new year)


@pytest.mark.parametrize("months", [SUMMER, SOUTH_SUMMER])
def test_power_returns_only_the_months_asked_for(data, monkeypatch, months):
    monkeypatch.setattr(nasa_power, "_fetch", power_answer)
    got = readers.read_series("air_temperature", ZONE, {**PERIOD, "months": months})
    assert got["absence"] is None and {int(t[5:7]) for t in got["data"].t} == set(months)
    assert len(got["data"]) == 2 * len(months) - (1 if 3 in months else 0)  # March 2000 has no data
    assert readers.read_series("air_temperature", ZONE, PERIOD)["data"].shape[0] == 23  # no filter: whole period


@pytest.mark.parametrize(("months", "pct"), [(None, 95.8), (SUMMER, 100.0), (SOUTH_SUMMER, 87.5)])
def test_power_coverage_counts_the_months_asked_for_not_the_whole_record(data, monkeypatch, months, pct):
    # 2000-2001 is 24 months and March 2000 has no data: 23 of 24; summer is 8 of 8; the southern summer 7 of 8.
    monkeypatch.setattr(nasa_power, "_fetch", power_answer)
    period = {**PERIOD, "months": months} if months else PERIOD
    assert readers.read_series("air_temperature", ZONE, period)["coverage"]["valid_pct"] == pct


# ---------------------------------------------------------------- IMERG
def write_imerg(data, year, month, rate):
    """A file shaped like the real ones: group Grid, axes (time, lon, lat), rate in mm/hour, a fill value."""
    folder = data / "packages" / "imerg-monthly"
    folder.mkdir(parents=True, exist_ok=True)
    lon, lat = np.arange(-179.95, 180, 0.1, dtype="float32"), np.arange(-89.95, 90, 0.1, dtype="float32")
    grid = np.full((1, lon.size, lat.size), rate, dtype="float32")
    grid[0, lon > 0, :] = -9999.9  # east of Greenwich: no data, to prove the window is read on the right axis
    grid[0, :, lat < 0] = rate * 100  # the south: a different value, to catch swapped axes
    path = folder / f"imerg_{year}{month:02d}.HDF5"
    with h5py.File(path, "w") as file:
        group = file.create_group("Grid")
        group["lon"], group["lat"] = lon, lat
        dataset = group.create_dataset("precipitation", data=grid)
        dataset.attrs["_FillValue"] = np.array([-9999.9], dtype="float32")
    relative = path.relative_to(data).as_posix()
    last = {1: 31, 2: 29 if year % 4 == 0 else 28}.get(month, 30)
    return {"source": "imerg-monthly", "item": relative, "scope": "global", "path": relative, "mb": 1,
            "sha256": "0" * 64, "format": "hdf5", "license": "test",
            "period": {"start": f"{year}-{month:02d}-01", "end": f"{year}-{month:02d}-{last}"},
            "ingest": {"date": "2026-11-12T00:00:00Z", "origin": "predownload", "url": "https://example.org"}}


def test_imerg_reads_the_right_axes_and_returns_month_totals(data):
    lines = [write_imerg(data, 2000, 1, 0.1), write_imerg(data, 2000, 2, 0.2), write_imerg(data, 2005, 1, 0.5)]
    (data / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    coverage.rebuild({"imerg-monthly": ["precipitation"]})

    got = imerg.read("precipitation", ZONE, PERIOD)
    assert got["absence"] is None and got["origin"] == "package" and got["unit"] == "mm/month"
    assert got["data"].t.tolist() == ["2000-01-01", "2000-02-01"]  # 2005 is outside the period
    assert got["data"].v.tolist() == pytest.approx([0.1 * 24 * 31, 0.2 * 24 * 29])  # mm/hour → mm in the month
    assert got["sources"][0]["n_items"] == 2 and got["coverage"] == {"valid_pct": 100.0, "n_dates": 2}

    east = {"name": "east of Greenwich", "bbox": [5.0, 40.0, 5.5, 40.5]}
    assert imerg.read("precipitation", east, PERIOD)["absence"]["reason"] == "insufficient_qa"
    nothing = imerg.read("precipitation", ZONE, {"start": "1990-01-01", "end": "1990-12-31"})
    assert nothing["absence"]["reason"] == "needs_package" and nothing["sources"][0]["source"] == "imerg-monthly"


@pytest.mark.parametrize("months", [SUMMER, SOUTH_SUMMER])
def test_imerg_returns_only_the_months_asked_for(data, months):
    lines = [write_imerg(data, y, m, 0.1) for y in (2000, 2001) for m in range(1, 13)]
    (data / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    coverage.rebuild({"imerg-monthly": ["precipitation"]})
    got = readers.read_series("precipitation", ZONE, {**PERIOD, "months": months})
    assert got["absence"] is None and {int(t[5:7]) for t in got["data"].t} == set(months)
    assert len(got["data"]) == 2 * len(months) and got["sources"][0]["n_items"] == 2 * len(months)
    assert got["coverage"]["valid_pct"] == 100.0
    assert len(readers.read_series("precipitation", ZONE, PERIOD)["data"]) == 24  # no filter: every month


# ---------------------------------------------------------------- the common door
def test_unknown_variable_is_an_absence_with_a_reason(data):
    got = readers.read_series("no_such_variable", ZONE, PERIOD)
    assert got["data"] is None and got["absence"]["reason"] == "not_computed" and got["sources"]


def test_every_reader_variable_is_in_the_catalog_with_its_unit():
    """A reader that the catalog does not know cannot be planned; a unit that differs would mislabel a figure."""
    import yaml
    catalog = {v["id"]: v for v in yaml.safe_load((paths.CATALOG_DIR / "variables.yaml").read_text(encoding="utf-8"))}
    assert set(readers.variables()) <= set(catalog)
    assert catalog["air_temperature"]["unit"] == "°C" and catalog["precipitation"]["unit"] == "mm/month"


@pytest.mark.data
def test_real_imerg_series_for_a_known_point():
    """With the real files: about three seconds for the whole record, plausible monthly totals."""
    coverage.rebuild()
    got = imerg.read("precipitation", ZONE, {"start": "1998-01-01", "end": "2025-12-31"})
    if got["absence"] is not None:
        pytest.skip("IMERG is not downloaded on this computer")
    table = got["data"]
    assert len(table) >= 300 and table.v.min() >= 0 and table.v.max() < 1000
    assert 250 < table.groupby(table.t.str[:4]).v.sum().median() < 900  # a Mediterranean coast, in mm per year


@pytest.mark.data
def test_real_imerg_summer_at_sagunt_is_only_june_to_september():
    coverage.rebuild()
    sagunt = {"id": "sagunt", "name": "Sagunt", "bbox": [-0.35, 39.60, -0.15, 39.75]}
    got = readers.read_series("precipitation", sagunt, {"start": "2015-01-01", "end": "2020-12-31", "months": SUMMER})
    if got["absence"] is not None:
        pytest.skip("IMERG is not downloaded on this computer")
    months = {int(t[5:7]) for t in got["data"].t}
    assert months == set(SUMMER) and len(got["data"]) == 24


# ---------------------------------------------------------------- Landsat
class Scene:
    """What the catalogue returns for one scene, with local files instead of signed URLs."""

    def __init__(self, folder, name, date, platform, values, cloud=5.0):
        import rasterio
        from rasterio.transform import from_origin

        # A Collection 2 Tier 1 id: since decision 1 of A10 the reader keeps only T1 scenes by default.
        self.id, self.properties = f"{name}_02_T1", {"platform": platform, "eo:cloud_cover": cloud}
        self.datetime = __import__("datetime").datetime.fromisoformat(date)
        self.assets = {}
        # A 40 x 40 grid of 30 m pixels in UTM 30N whose corner sits just north-west of the test zone.
        transform = from_origin(726000, 4406000, 30, 30)
        for band, value in values.items():
            path = folder / f"{name}_{band}.tif"
            grid = np.full((1200, 1200), value, dtype="uint16")
            with rasterio.open(path, "w", driver="GTiff", height=1200, width=1200, count=1, dtype="uint16",
                               crs="EPSG:32630", transform=transform) as out:
                out.write(grid, 1)
            self.assets[band] = type("Asset", (), {"href": str(path)})()


CLEAR, CLOUD = 1 << 6 | 1, 1 << 3 | 1  # quality values: clear land, and cloud


def digital(reflectance):
    return int(round((reflectance + 0.2) / 2.75e-5))


def test_landsat_masks_scales_dedupes_and_caches(data, monkeypatch, tmp_path):
    from hup_data.readers import landsat

    small = {"id": "small-zone", "name": "Small zone", "bbox": [-0.34, 39.72, -0.30, 39.75]}
    bands = {"red": digital(0.10), "nir08": digital(0.30), "lwir11": int(round((30 + 273.15 - 149.0) / 0.00341802))}
    scenes = {
        2020: [Scene(tmp_path, "clear-a", "2020-07-01", "landsat-8", {**bands, "qa_pixel": CLEAR}),
               Scene(tmp_path, "clear-a-neighbour", "2020-07-01", "landsat-8", {**bands, "qa_pixel": CLEAR}),
               Scene(tmp_path, "cloudy", "2020-07-17", "landsat-8", {**bands, "qa_pixel": CLOUD}),
               Scene(tmp_path, "clear-b", "2020-08-02", "landsat-9", {**bands, "qa_pixel": CLEAR})],
        2021: [],
    }
    searched = []
    monkeypatch.setattr(landsat, "_search", lambda zone, year, months: searched.append(year) or scenes[year])
    period = {"start": "2020-06-01", "end": "2021-09-30", "months": [6, 7, 8, 9]}

    green = readers.read_series("ndvi", small, period)
    assert green["absence"] is None and green["origin"] == "remote" and green["unit"] == "1"
    assert green["data"].t.tolist() == ["2020-07-01", "2020-08-02"]  # one per date and satellite; the cloudy one is out
    assert green["data"].v.tolist() == pytest.approx([0.5, 0.5], abs=1e-3)  # (0.30 - 0.10) / (0.30 + 0.10)
    assert green["coverage"]["n_dates"] == 2 and green["sources"][0]["n_items"] == 2

    heat = readers.read_series("lst", small, period)  # same scenes, already reduced: nothing is searched again
    assert heat["origin"] == "cache" and heat["unit"] == "°C" and sorted(searched) == [2020, 2021]
    assert heat["data"].v.tolist() == pytest.approx([30.0, 30.0], abs=0.01)
    assert heat["support"]["resolution_m"] == 100

    lines = [line for line in manifest_lines(data) if line["source"] == "landsat-c2-l2"]
    assert [line["item"] for line in lines] == ["small-zone_2020_m06-07-08-09", "small-zone_2021_m06-07-08-09"]
    assert lines[0]["variables"] == ["ndvi", "lst"] and lines[0]["tier"] == "ready"

    monkeypatch.setenv("HUP_OFFLINE", "1")
    longer = readers.read_series("ndvi", small, {**period, "start": "2019-06-01"})
    assert longer["absence"]["reason"] == "source_unreachable"  # a missing year is said, not skipped
    assert readers.read_series("ndvi", small, {"start": "2000-01-01", "end": "2005-12-31"})["absence"]["reason"] \
        == "no_coverage"


def test_landsat_lists_missing_years_concurrently_but_skips_cached_year(data, monkeypatch):
    from threading import Barrier

    from hup_data.readers import landsat

    zone = {"id": "small-zone", "name": "Small zone", "bbox": [-0.34, 39.72, -0.30, 39.75]}
    land_cache = landsat._year_file(zone, 2020, None, None)
    land_cache.parent.mkdir(parents=True)
    rows = ["2020-01-01,landsat-8,winter_T1,0.2,20,100,0", "2020-07-01,landsat-8,summer_T1,0.5,30,100,0"]
    land_cache.write_text("\n".join([",".join(landsat.COLUMNS), *rows]) + "\n", encoding="utf-8")
    barrier = Barrier(2, timeout=3)
    searched = []

    def search(zone, year, months):
        assert year != 2020
        assert months == [7]
        searched.append(year)
        barrier.wait()
        return []

    monkeypatch.setattr(landsat, "_search", search)
    got = landsat.read("ndvi", zone, {"start": "2020-01-01", "end": "2022-12-31", "months": [7]})
    assert got["absence"] is None and got["origin"] == "remote"
    assert got["data"].t.tolist() == ["2020-07-01"] and sorted(searched) == [2021, 2022]


def test_landsat_ndvi_and_lst_read_at_once_reduce_each_scene_once(data, monkeypatch, tmp_path):
    """The NDVI and LST steps of a plan run in parallel and need the same year file: one reads, the other waits."""
    from threading import Barrier, Thread

    from hup_data.readers import landsat

    zone = {"id": "small-zone", "name": "Small zone", "bbox": [-0.34, 39.72, -0.30, 39.75]}
    bands = {"red": digital(0.10), "nir08": digital(0.30), "lwir11": int(round((30 + 273.15 - 149.0) / 0.00341802)),
             "qa_pixel": CLEAR}
    scenes = [Scene(tmp_path, "clear-a", "2020-07-01", "landsat-8", bands),
              Scene(tmp_path, "clear-b", "2020-08-02", "landsat-9", bands)]
    barrier = Barrier(2, timeout=5)  # both steps have listed the scenes before either reduces them

    def search(zone, year, months):
        barrier.wait()
        return scenes

    reduced, scene = [], landsat._scene
    monkeypatch.setattr(landsat, "_search", search)
    monkeypatch.setattr(landsat, "_scene", lambda item, *args: reduced.append(item.id) or scene(item, *args))
    period = {"start": "2020-06-01", "end": "2020-09-30", "months": [6, 7, 8, 9]}
    got = {}
    threads = [Thread(target=lambda v=v: got.__setitem__(v, landsat.read(v, zone, period))) for v in ("ndvi", "lst")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(reduced) == sorted(item.id for item in scenes)  # once each, not once per variable
    assert sorted(result["origin"] for result in got.values()) == ["cache", "remote"]
    assert got["ndvi"]["data"].v.tolist() == pytest.approx([0.5, 0.5], abs=1e-3)
    assert got["lst"]["data"].v.tolist() == pytest.approx([30.0, 30.0], abs=0.01)
