"""IMERG Final daily through OPeNDAP. Answers of CMR and GES DISC are simulated: no network and no key here."""
import json
from datetime import date

import numpy as np
import pytest
import requests

from hup_data import paths, readers, settings
from hup_data.readers import imerg_daily as daily

HORTA = {"id": "horta", "name": "Horta Sud", "bbox": [-0.6, 39.1, -0.1, 39.8]}


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    monkeypatch.setenv("EARTHDATA_TOKEN", "not-a-real-key")
    return tmp_path


class Answer:
    def __init__(self, status=200, text="", body=None, headers=None):
        self.status_code, self.text, self.headers = status, text, headers or {}
        self.content, self.ok, self._body = text.encode(), status < 400, body

    def json(self):
        return self._body


def rain(day: date, i: int, j: int) -> float:
    """A made-up field: grows with the day of the month and with the cell, 0 on the 15th (a dry day)."""
    return 0.0 if day.day == 15 else round(day.day + 0.5 * (i - 1794) + 0.1 * (j - 1291), 3)


def opendap_csv(url: str, fill_cell=None) -> str:
    """Shaped like a GES DISC .dap.csv answer: one line per longitude, values along latitude."""
    name = url.split("?")[0].split("/")[-1].split(".dap.csv")[0]
    day = date(int(name[21:25]), int(name[25:27]), int(name[27:29]))
    i0, i1 = map(int, url.split("[0][")[1].split("]")[0].split(":"))
    j0, j1 = map(int, url.split("][")[-1].rstrip("]").split(":"))
    lines = [f"Dataset: {name}"]
    for row, i in enumerate(range(i0, i1 + 1)):
        values = [daily.FILL if (i, j) == fill_cell else rain(day, i, j) for j in range(j0, j1 + 1)]
        lines.append(f"/precipitation[0][{row}], " + ", ".join(str(v) for v in values))
    return "\n".join(lines) + "\n"


def cmr(first: date, last: date) -> dict:
    entries = []
    for ordinal in range(first.toordinal(), last.toordinal() + 1):
        day = date.fromordinal(ordinal)
        if day > date(2025, 9, 30):  # end of the record in CMR on 2026-11-14
            break
        name = f"3B-DAY.MS.MRG.3IMERG.{day:%Y%m%d}-S000000-E235959.V07B.nc4"
        entries.append({"title": f"GPM_3IMERGDF.07:{name}", "time_start": f"{day}T00:00:00.000Z"})
    return {"feed": {"entry": entries}}


@pytest.fixture
def server(monkeypatch):
    """Replaces the network: CMR and OPeNDAP answers, and the URLs asked for."""
    asked = []

    def get(client, url, **options):
        if url == daily.CMR:
            first, last = (date.fromisoformat(part[:10]) for part in options["params"]["temporal"].split(","))
            return Answer(body=cmr(first, last))
        asked.append(url)
        return Answer(text=opendap_csv(url))

    monkeypatch.setattr(daily, "_get", get)
    return asked


def manifest_lines(data):
    file = data / "manifest.jsonl"
    return [json.loads(text) for text in file.read_text(encoding="utf-8").splitlines()] if file.is_file() else []


def test_cell_window_and_area_weights_follow_the_grid():
    """Cell i has its centre at -179.95 + 0.1 i; the Horta rectangle has exact 0.1° edges: 5 × 7 cells."""
    assert daily.window(HORTA["bbox"]) == (1794, 1798, 1291, 1297)
    assert daily.window([-0.44, 39.41, -0.40, 39.44]) == (1795, 1795, 1294, 1294)
    half = daily.weights([-0.55, 39.1, -0.4, 39.2], (1794, 1795, 1291, 1291))  # half of 1794, all of 1795
    assert half[1, 0] == pytest.approx(2 * half[0, 0])


def test_parse_reads_the_opendap_csv_and_drops_the_fill_value():
    url = daily._url(date(2024, 10, 29), "3B-DAY.MS.MRG.3IMERG.20241029-S000000-E235959.V07B.nc4",
                     (1794, 1795, 1291, 1292))
    block = daily.parse(opendap_csv(url, fill_cell=(1795, 1292)), (1794, 1795, 1291, 1292))
    assert block.shape == (2, 2) and block[0, 0] == 29.0 and block[1, 0] == 29.5 and np.isnan(block[1, 1])
    with pytest.raises(ValueError):
        daily.parse("Dataset: x\n/precipitation[0][0], 1.0\n", (1794, 1795, 1291, 1292))


def test_daily_series_is_the_area_weighted_mean_cached_and_recorded(data, server):
    got = daily.read("precipitation_daily", HORTA, {"start": "2024-10-01", "end": "2024-10-31"})
    assert got["absence"] is None and got["origin"] == "remote" and got["unit"] == "mm/day"
    assert len(got["data"]) == 31 and got["coverage"] == {"valid_pct": 100.0, "n_dates": 31}
    weight = daily.weights(HORTA["bbox"], (1794, 1798, 1291, 1297))
    field = np.array([[rain(date(2024, 10, 29), i, j) for j in range(1291, 1298)] for i in range(1794, 1799)])
    assert got["data"].set_index("t").v["2024-10-29"] == pytest.approx(np.average(field, weights=weight))
    assert got["data"].set_index("t").v["2024-10-15"] == 0.0  # a dry day is a zero, not a gap
    lines = manifest_lines(data)
    assert len(lines) == 31 and lines[0]["source"] == "imerg-daily" and lines[0]["format"] == "csv"
    assert lines[0]["variables"] == ["precipitation_daily", "pr_max_1day_observed"]
    assert lines[0]["bbox"] == [-0.6, 39.1, -0.1, 39.8] and lines[0]["item"].endswith(".V07B.nc4")
    assert all("not-a-real-key" not in json.dumps(line) for line in lines)
    server.clear()
    again = daily.read("precipitation_daily", HORTA, {"start": "2024-10-01", "end": "2024-10-31"})
    assert server == [] and again["origin"] == "cache"


def test_a_town_inside_a_cached_rectangle_reads_it_without_the_network(data, server, monkeypatch):
    daily.read("precipitation_daily", HORTA, {"start": "2024-10-28", "end": "2024-10-30"})
    monkeypatch.setenv("HUP_OFFLINE", "1")
    town = daily.read("precipitation_daily", {"id": "t", "bbox": [-0.44, 39.41, -0.40, 39.44]},
                      {"start": "2024-10-28", "end": "2024-10-30"})
    assert town["absence"] is None and town["origin"] == "cache"
    assert list(town["data"].v) == [rain(date(2024, 10, d), 1795, 1294) for d in (28, 29, 30)]


def test_only_the_months_asked_for_are_read(data, server):
    got = daily.read("precipitation_daily", HORTA, {"start": "2023-01-01", "end": "2024-12-31", "months": [10, 11]})
    months = {t[5:7] for t in got["data"].t}
    assert months == {"10", "11"} and len(got["data"]) == 2 * 61 and len(server) == 122


def test_yearly_maximum_of_the_daily_zone_mean(data, server):
    got = daily.read("pr_max_1day_observed", HORTA, {"start": "2022-01-01", "end": "2023-12-31", "months": [10]})
    assert got["absence"] is None and got["unit"] == "mm" and list(got["data"].t) == ["2022", "2023"]
    weight = daily.weights(HORTA["bbox"], (1794, 1798, 1291, 1297))
    wettest = np.average([[rain(date(2022, 10, 31), i, j) for j in range(1291, 1298)] for i in range(1794, 1799)],
                         weights=weight)
    assert got["data"].v.iloc[0] == pytest.approx(wettest)


def test_a_year_with_too_few_days_has_no_maximum(data, server):
    """2025 ends in CMR on 30 September: a whole-year maximum would be a maximum of nine months."""
    got = daily.read("pr_max_1day_observed", HORTA, {"start": "2024-01-01", "end": "2025-12-31", "months": [9, 10]})
    assert list(got["data"].t) == ["2024"] and got["coverage"]["valid_pct"] == 50.0


def test_a_period_that_ends_within_a_year_does_not_make_that_piece_a_year(data, server, monkeypatch):
    """The 95 % rule counts the days of the calendar year (of the months asked), not the days asked: 41 days of
    2025 are not a year."""
    monkeypatch.setattr(daily, "MAX_REMOTE_DAYS", 1000)
    got = daily.read("pr_max_1day_observed", HORTA, {"start": "2024-01-01", "end": "2025-02-10"})
    assert list(got["data"].t) == ["2024"]
    starts = daily.read("pr_max_1day_observed", HORTA, {"start": "2024-12-01", "end": "2025-12-31", "months": [12]})
    assert starts["absence"] is None and list(starts["data"].t) == ["2024"]  # 2025 has no December in CMR here
    assert daily.calendar_days(2024) == 366 and daily.calendar_days(2023, [10, 11]) == 61


def test_absences_have_their_reasons(data, server, monkeypatch):
    before = daily.read("precipitation_daily", HORTA, {"start": "1990-01-01", "end": "1997-12-31"})
    assert before["absence"]["reason"] == "no_coverage"
    pole = daily.read("precipitation_daily", {"id": "x", "bbox": [0, -95, 1, -89]}, {"start": "2020-01-01",
                                                                                    "end": "2020-01-02"})
    assert pole["absence"]["reason"] == "no_coverage"
    late = daily.read("precipitation_daily", HORTA, {"start": "2026-01-01", "end": "2026-01-31"})
    assert late["absence"]["reason"] == "no_coverage" and server == []
    too_many = daily.read("precipitation_daily", HORTA, {"start": "2000-01-01", "end": "2010-12-31"})
    assert too_many["absence"]["reason"] == "needs_package" and server == []
    monkeypatch.setenv("HUP_OFFLINE", "1")
    offline = daily.read("precipitation_daily", HORTA, {"start": "2024-10-01", "end": "2024-10-02"})
    assert offline["absence"]["reason"] == "source_unreachable" and server == []


def test_without_an_account_the_absence_does_not_mention_the_key(data, server, monkeypatch, tmp_path):
    monkeypatch.delenv("EARTHDATA_TOKEN")
    monkeypatch.setattr(settings.Settings, "secret", lambda self, name: None)  # a local .env must not leak in
    monkeypatch.setenv("EARTHDATA_TOKEN_FILE", str(tmp_path / "missing"))
    got = daily.read("precipitation_daily", HORTA, {"start": "2024-10-01", "end": "2024-10-02"})
    assert got["absence"]["reason"] == "source_unreachable" and server == []
    text = json.dumps(got["absence"], ensure_ascii=False)
    assert "Earthdata" in text and "token" not in text.lower() and "clave" not in text.lower()


def test_network_failures_are_retried_a_few_times_then_reported(data, monkeypatch):
    calls = []

    class Down:
        def get(self, url, **options):
            calls.append(options["timeout"])
            raise requests.ConnectionError("no route")

    monkeypatch.setattr(daily.time, "sleep", lambda seconds: None)
    with pytest.raises(daily.Unreachable):
        daily._get(Down(), "https://gpm1.gesdisc.eosdis.nasa.gov/x")
    assert len(calls) == daily.ATTEMPTS and calls[0] == daily.TIMEOUT

    class Refused:
        def get(self, url, **options):
            calls.append(url)
            return Answer(status=401)

    calls.clear()
    with pytest.raises(daily.Unreachable, match="did not accept"):
        daily._get(Refused(), "https://gpm1.gesdisc.eosdis.nasa.gov/x")
    assert len(calls) == 1  # a refusal is not retried


def test_a_failed_day_makes_the_read_an_absence_and_keeps_what_was_cached(data, monkeypatch):
    def get(client, url, **options):
        if url == daily.CMR:
            first, last = (date.fromisoformat(part[:10]) for part in options["params"]["temporal"].split(","))
            return Answer(body=cmr(first, last))
        if "20241002" in url:
            raise daily.Unreachable("gpm1.gesdisc.eosdis.nasa.gov did not answer (ReadTimeout).")
        return Answer(text=opendap_csv(url))

    monkeypatch.setattr(daily, "_get", get)
    got = daily.read("precipitation_daily", HORTA, {"start": "2024-10-01", "end": "2024-10-03"})
    assert got["absence"]["reason"] == "source_unreachable"
    assert daily._file((1794, 1798, 1291, 1297), date(2024, 10, 1)).is_file()


def test_the_key_stays_on_earthdata_redirects_only():
    session = daily._Session()

    class Prepared:
        def __init__(self, url):
            self.url, self.headers = url, {"Authorization": "Bearer x"}

    inside, outside = Prepared("https://urs.earthdata.nasa.gov/oauth"), Prepared("https://example.org/")
    session.rebuild_auth(inside, None)
    session.rebuild_auth(outside, None)
    assert "Authorization" in inside.headers and "Authorization" not in outside.headers


def test_both_variables_are_registered_and_in_the_catalog():
    import yaml
    catalog = {v["id"]: v for v in yaml.safe_load((paths.CATALOG_DIR / "variables.yaml").read_text(encoding="utf-8"))}
    assert {"precipitation_daily", "pr_max_1day_observed"} <= set(readers.variables())
    assert catalog["precipitation_daily"]["unit"] == "mm/day" and catalog["pr_max_1day_observed"]["unit"] == "mm"
    assert catalog["precipitation_daily"]["eurovoc"] == catalog["precipitation"]["eurovoc"]


@pytest.mark.data
def test_real_dana_day_at_paiporta_is_its_imerg_cell(monkeypatch):
    """29 October 2024 at Paiporta (INE municipality 46186), read from the predownloaded Horta rectangle without the
    network. Paiporta falls in one 0.1° cell, so the zone value must be that cell in the cached GES DISC answer."""
    import shapely
    from shapely.geometry import mapping

    from hup_data.readers import vectors as layers

    monkeypatch.setenv("HUP_OFFLINE", "1")
    sections = layers.read_layer("ine/seccionado_2026__SECC_CE_20260101", [-0.5, 39.3, -0.3, 39.5])
    if sections["absence"] is not None:
        pytest.skip("INE sections are not prepared on this computer")
    rows = sections["data"]
    town = shapely.union_all(rows[rows.CUMUN == "46186"].geometry.values)
    paiporta = {"id": "paiporta", "geometry": mapping(town), "bbox": list(town.bounds)}
    got = daily.read("precipitation_daily", paiporta, {"start": "2024-10-29", "end": "2024-10-29"})
    if got["absence"] is not None:
        pytest.skip("IMERG daily for the DANA is not predownloaded on this computer")
    cells = daily.window(paiporta["bbox"])
    assert cells[0] == cells[1] and cells[2] == cells[3]
    held = next(h for h in daily._holders(cells) if daily._file(h, date(2024, 10, 29)).is_file())
    block = daily.parse(daily._file(held, date(2024, 10, 29)).read_text(encoding="utf-8"), held)
    expected = block[cells[0] - held[0], cells[2] - held[2]]
    assert got["data"].v.iloc[0] == pytest.approx(expected) and 20 < expected < 300
