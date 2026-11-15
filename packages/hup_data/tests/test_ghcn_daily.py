"""GHCN-Daily rain gauges through the NCEI service. The service is simulated: no network here."""
import hashlib
import json
import os
import time

import pytest
import requests

from hup_data import readers
from hup_data.readers import ghcn_daily as ghcn

VALENCIA = {"id": "valencia", "name": "València", "bbox": [-0.43, 39.43, -0.33, 39.51]}  # centre 39.47 N 0.38 W
AIRPORT = {"id": "manises", "name": "Manises", "bbox": [-0.50, 39.46, -0.48, 39.48]}
FAR = {"id": "far", "name": "Lejos", "bbox": [-2.0, 38.0, -1.9, 38.1]}

INVENTORY = (
    "SPM00008284  39.4890   -0.4820 TMAX 1973 2025\n"
    "SPM00008284  39.4890   -0.4820 PRCP 1973 2025\n"
    "SPOLD000001  39.4700   -0.3800 PRCP 1900 1950\n"  # nearer, but stopped measuring long before
    "SP000008416  39.4806    0.3664 PRCP 1937 2026\n"
)
STATIONS = ("SPM00008284  39.4890   -0.4820   68.6    VALENCIA                               08284\n"
            "SP000008416  39.4806    0.3664   11.0    VALENCIA                               08285\n")


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    folder = tmp_path / "packages" / "ghcn-daily"
    folder.mkdir(parents=True)
    (folder / "ghcnd-inventory.txt").write_text(INVENTORY, encoding="utf-8")
    (folder / "ghcnd-stations.txt").write_text(STATIONS, encoding="utf-8")
    ghcn._inventory.cache_clear()
    return tmp_path


def service_csv(first=2020, last=2021, missing=(), flagged=()):
    """Shaped like the NCEI daily-summaries CSV with units=metric: mm with one decimal, flags 'm,q,s'."""
    import pandas as pd

    lines = ['"STATION","DATE","PRCP","PRCP_ATTRIBUTES"']
    for day in pd.date_range(f"{first}-01-01", f"{last}-12-31"):
        t = day.strftime("%Y-%m-%d")
        if t in missing:
            lines.append(f'"SPM00008284","{t}",,')
            continue
        v = 50.0 + day.year - 2000 if day.strftime("%m-%d") == "10-29" else float(day.day % 3)
        q = "X" if t in flagged else ""
        lines.append(f'"SPM00008284","{t}","{v:.1f}",",{q},S"')
    return "\n".join(lines) + "\n"


class Answer:
    def __init__(self, status=200, text=""):
        self.status_code, self.text, self.content, self.ok = status, text, text.encode(), status < 400


@pytest.fixture
def server(monkeypatch):
    asked = []

    def get(url, headers=None, timeout=None):
        asked.append((url, headers))
        return Answer(text=service_csv(missing={"2021-03-01"}, flagged={"2021-10-29"}))

    monkeypatch.setattr(ghcn.requests, "get", get)
    return asked


def test_the_nearest_gauge_with_rain_in_the_period_is_the_candidate(data):
    candidates, beyond = ghcn.nearest(VALENCIA, {"start": "1960-01-01", "end": "1970-12-31"})
    assert beyond is None and [c["id"] for c in candidates] == ["SP000008416"]
    candidates, _ = ghcn.nearest(AIRPORT, {"start": "2020-01-01", "end": "2021-12-31"})
    station = candidates[0]
    assert station["id"] == "SPM00008284" and station["name"] == "VALENCIA"
    assert station["km"] == pytest.approx(ghcn.km_between(39.47, -0.49, 39.489, -0.482), rel=1e-6)
    old, _ = ghcn.nearest(AIRPORT, {"start": "1920-01-01", "end": "1930-12-31"})
    assert old[0]["id"] == "SPOLD000001"  # nearest of those measuring in the period


def test_the_listed_position_is_corrected_from_the_table_with_its_source(data):
    row = ghcn.station("SP000008416")
    assert (row["lat"], row["lon"]) == (39.4806, -0.3664) and "ECA&D" in row["fix"] and "STAID 237" in row["fix"]
    assert ghcn.station("SPM00008284")["fix"] == ""


def test_at_a_similar_distance_the_gauge_with_more_valid_years_is_read(data, monkeypatch):
    def get(url, **_):
        if "SP000008416" in url:  # the city gauge misses most of 2021: one valid year against two
            missing = {f"2021-{m:02d}-01" for m in range(1, 13)} | {f"2021-01-{d:02d}" for d in range(2, 20)}
            return Answer(text=service_csv(missing=missing).replace("SPM00008284", "SP000008416"))
        return Answer(text=service_csv())

    monkeypatch.setattr(ghcn.requests, "get", get)
    zone = {"id": "between", "bbox": [-0.42, 39.47, -0.40, 39.49]}  # nearer to the city gauge, by under 5 km
    candidates, _ = ghcn.nearest(zone, {"start": "2020-01-01", "end": "2021-12-31"})
    assert [c["id"] for c in candidates] == ["SP000008416", "SPM00008284"]
    got = ghcn.read(ghcn.YEARLY_MAX, zone, {"start": "2020-01-01", "end": "2021-12-31"})
    assert got["sources"][0]["items"] == ["SPM00008284"] and len(got["data"]) == 2
    assert "SP000008416" in got["limits"][-1]["en"] and "1 valid years" in got["limits"][-1]["en"]
    city = ghcn.read(ghcn.DAILY, VALENCIA, {"start": "2020-01-01", "end": "2021-12-31"})
    assert city["sources"][0]["items"] == ["SP000008416"]
    assert any("Posición corregida" in limit["es"] for limit in city["limits"])


def test_beyond_the_maximum_distance_it_is_an_absence_that_names_the_nearest(data, server):
    got = ghcn.read(ghcn.DAILY, FAR, {"start": "2020-01-01", "end": "2021-12-31"})
    assert got["absence"]["reason"] == "no_coverage" and "SPM00008284" in got["absence"]["detail"]["es"]
    assert "20 km" in got["absence"]["detail"]["en"] and server == []
    near = ghcn.read(ghcn.DAILY, FAR, {"start": "2020-01-01", "end": "2021-12-31"}, max_km=300)
    assert near["absence"] is None


def test_daily_rain_is_read_in_mm_cached_and_recorded(data, server):
    got = ghcn.read(ghcn.DAILY, AIRPORT, {"start": "2020-01-01", "end": "2021-12-31"})
    assert got["absence"] is None and got["origin"] == "remote" and got["unit"] == "mm/day"
    url, headers = server[0]
    assert "units=metric" in url and "stations=SPM00008284" in url and "startDate=1973-01-01" in url
    assert headers == {"User-Agent": "python-requests"}
    series = got["data"].set_index("t").v
    assert series["2020-10-29"] == 70.0 and "2021-03-01" not in series and "2021-10-29" not in series
    assert len(series) == 731 - 2 and got["coverage"]["n_dates"] == 729
    assert got["sources"][0]["items"] == ["SPM00008284"] and "point measurement" in got["sources"][0]["product"]
    assert "km from the zone centre" in got["sources"][0]["product"]
    texts = " ".join(limit["es"] for limit in got["limits"])
    assert "Medida puntual" in texts and "GSOD" in got["limits"][1]["en"] and "1 valores" in texts
    lines = [json.loads(t) for t in (data / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1 and lines[0]["source"] == "ghcn-daily" and lines[0]["item"] == "SPM00008284"
    assert lines[0]["path"] == "cache/ghcn-daily/SPM00008284.csv" and lines[0]["period"]["end"] == "2021-12-31"
    server.clear()
    again = ghcn.read(ghcn.DAILY, AIRPORT, {"start": "2021-01-01", "end": "2021-01-31"})
    assert server == [] and again["origin"] == "cache" and len(again["data"]) == 31


def test_yearly_maximum_needs_95_percent_of_the_days_and_is_not_filled(data, monkeypatch):
    missing = {f"2021-01-{d:02d}" for d in range(1, 20)}  # 19 days missing: 346 of 365, below 95 %
    monkeypatch.setattr(ghcn.requests, "get", lambda url, **_: Answer(text=service_csv(missing=missing)))
    got = ghcn.read(ghcn.YEARLY_MAX, AIRPORT, {"start": "2020-01-01", "end": "2021-12-31"})
    assert got["unit"] == "mm" and got["data"].to_dict(orient="list") == {"t": ["2020"], "v": [70.0]}
    assert got["coverage"] == {"valid_pct": 50.0, "n_dates": 1}
    none = ghcn.read(ghcn.YEARLY_MAX, AIRPORT, {"start": "2021-01-01", "end": "2021-12-31"})
    assert none["absence"]["reason"] == "insufficient_qa"


def test_a_period_that_starts_or_ends_within_a_year_does_not_make_that_piece_a_year(data, server):
    """The 95 % rule counts the days of the calendar year, not the days asked: 41 days of 2021 are not a year."""
    got = ghcn.read(ghcn.YEARLY_MAX, AIRPORT, {"start": "2020-01-01", "end": "2021-02-10"})
    assert list(got["data"].t) == ["2020"]
    late = ghcn.read(ghcn.YEARLY_MAX, AIRPORT, {"start": "2020-11-01", "end": "2021-12-31"})
    assert list(late["data"].t) == ["2021"]
    season = ghcn.read(ghcn.YEARLY_MAX, AIRPORT, {"start": "2020-01-01", "end": "2021-12-31", "months": [10]})
    assert list(season["data"].t) == ["2020", "2021"]  # a season keeps its use: October is the whole of it


def test_offline_without_cache_is_source_unreachable_and_a_failing_service_too(data, monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    got = ghcn.read(ghcn.DAILY, VALENCIA, {"start": "2020-01-01", "end": "2020-12-31"})
    assert got["absence"]["reason"] == "source_unreachable"
    monkeypatch.delenv("HUP_OFFLINE")

    def fail(url, **_):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(ghcn.requests, "get", fail)
    monkeypatch.setattr(ghcn.time, "sleep", lambda _: None)
    got = ghcn.read(ghcn.DAILY, VALENCIA, {"start": "2020-01-01", "end": "2020-12-31"})
    assert got["absence"]["reason"] == "source_unreachable" and "ConnectionError" in got["absence"]["detail"]["en"]
    monkeypatch.setattr(ghcn.requests, "get", lambda url, **_: Answer(text="<html>maintenance</html>"))
    got = ghcn.read(ghcn.DAILY, VALENCIA, {"start": "2020-01-01", "end": "2020-12-31"})
    assert got["absence"]["reason"] == "source_unreachable" and not (data / "cache" / "ghcn-daily").exists()


def test_a_period_after_the_last_saved_day_asks_for_the_series_again(data, monkeypatch):
    asked = []

    def get(url, **_):
        asked.append(url)
        return Answer(text=service_csv(last=2022 if len(asked) > 1 else 2021))

    monkeypatch.setattr(ghcn.requests, "get", get)
    first = ghcn.read(ghcn.DAILY, AIRPORT, {"start": "2021-01-01", "end": "2021-12-31"})
    assert first["origin"] == "remote" and len(asked) == 1
    again = ghcn.read(ghcn.DAILY, AIRPORT, {"start": "2022-01-01", "end": "2022-12-31"}, max_km=5)
    assert len(asked) == 1 and again["absence"]["reason"] == "no_coverage"  # saved just now: not asked again yet
    old = time.time() - 2 * 86400
    os.utime(data / "cache" / "ghcn-daily" / "SPM00008284.csv", (old, old))
    newer = ghcn.read(ghcn.DAILY, AIRPORT, {"start": "2022-01-01", "end": "2022-12-31"}, max_km=5)
    assert len(asked) == 2 and "endDate=2025-12-31" in asked[1]
    assert newer["absence"] is None and newer["origin"] == "remote" and len(newer["data"]) == 365
    lines = [json.loads(t) for t in (data / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [line["path"] for line in lines] == ["cache/ghcn-daily/SPM00008284.csv",
                                               "cache/ghcn-daily/SPM00008284_2022-12-31.csv"]
    for line in lines:  # the first line still matches its file
        assert hashlib.sha256((data / line["path"]).read_bytes()).hexdigest() == line["sha256"]
    monkeypatch.setenv("HUP_OFFLINE", "1")
    late = ghcn.read(ghcn.DAILY, AIRPORT, {"start": "2022-06-01", "end": "2023-03-31"}, max_km=5)
    assert late["absence"] is None and len(asked) == 2
    assert any("acaba el 31-12-2022: sin red" in limit["es"] for limit in late["limits"])
    assert any("ends on 2022-12-31: offline" in limit["en"] for limit in late["limits"])


def test_when_the_nearest_gauges_have_no_value_in_the_period_the_next_ones_are_tried(data, monkeypatch):
    def get(url, **_):
        if "SP000008416" in url:  # the city gauge, 1.7 km away, has nothing in 2020-2021
            return Answer(text=service_csv(first=1990, last=1991).replace("SPM00008284", "SP000008416"))
        return Answer(text=service_csv())

    monkeypatch.setattr(ghcn.requests, "get", get)
    period = {"start": "2020-01-01", "end": "2021-12-31"}
    assert [c["id"] for c in ghcn.nearest(VALENCIA, period)[0]] == ["SP000008416"]  # the airport is 9 km away
    got = ghcn.read(ghcn.YEARLY_MAX, VALENCIA, period)
    assert got["absence"] is None and got["sources"][0]["items"] == ["SPM00008284"]
    assert any("SP000008416" in limit["es"] and "sin ningún valor" in limit["es"] for limit in got["limits"])
    none = ghcn.read(ghcn.DAILY, VALENCIA, period, max_km=5)
    assert none["absence"]["reason"] == "no_coverage" and "SP000008416" in none["absence"]["detail"]["en"]


def test_a_failed_download_with_network_is_not_called_offline_and_is_not_retried_twice(data, monkeypatch):
    asked = []

    def get(url, **_):
        asked.append(url)
        if "SP000008416" in url:
            raise requests.ConnectionError("down")
        return Answer(text=service_csv())

    monkeypatch.setattr(ghcn.requests, "get", get)
    monkeypatch.setattr(ghcn.time, "sleep", lambda _: None)
    zone = {"id": "between", "bbox": [-0.42, 39.47, -0.40, 39.49]}  # both gauges within 5 km of the nearest
    got = ghcn.read(ghcn.YEARLY_MAX, zone, {"start": "2020-01-01", "end": "2021-12-31"})
    texts = " ".join(limit["en"] for limit in got["limits"])
    assert got["sources"][0]["items"] == ["SPM00008284"] and "did not give their series): SP000008416" in texts
    assert "offline" not in texts
    asked.clear()

    def down(url, **_):
        asked.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(ghcn.requests, "get", down)
    for file in (data / "cache" / "ghcn-daily").glob("*.csv"):
        file.unlink()
    fails = ghcn.read(ghcn.YEARLY_MAX, zone, {"start": "2020-01-01", "end": "2021-12-31"})
    assert fails["absence"]["reason"] == "source_unreachable" and "ConnectionError" in fails["absence"]["detail"]["en"]
    assert len(asked) == 2 * ghcn.ATTEMPTS  # each candidate once, and no second round for the first one


def test_without_the_inventory_it_needs_the_package(data):
    (data / "packages" / "ghcn-daily" / "ghcnd-inventory.txt").unlink()
    got = ghcn.read(ghcn.DAILY, VALENCIA, {"start": "2020-01-01", "end": "2020-12-31"})
    assert got["absence"]["reason"] == "needs_package"


def test_parse_reads_tenths_free_metric_values_and_flags():
    table = ghcn.parse('"STATION","DATE","PRCP","PRCP_ATTRIBUTES"\n"X","2024-10-28","17.0",",,S"\n'
                       '"X","2024-10-29",,\n"X","2024-10-30","1.3","T,G,E"\n')
    assert table.to_dict(orient="list") == {"t": ["2024-10-28", "2024-10-30"], "v": [17.0, 1.3],
                                           "qflag": ["", "G"], "sflag": ["S", "E"]}


def test_both_variables_are_registered_for_read_series():
    assert {ghcn.DAILY, ghcn.YEARLY_MAX} <= set(readers.variables())
