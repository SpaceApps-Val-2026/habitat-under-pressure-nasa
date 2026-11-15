"""groundwater_level from the CHJ piezometric network (F71): synthetic ZIPs with the layout of the real one; the test
marked `data` reads the real package for Sagunt and runs hup_ops on it."""
import hashlib
import io
import json
import zipfile
from xml.sax.saxutils import escape

import numpy as np
import pandas as pd
import pytest
import shapely
from pyproj import Transformer
from shapely.geometry import box, mapping

from hup_data import paths, places, readers
from hup_data.prepare.common import manifest_lines
from hup_data.readers import piezometry

TO_DEGREES = Transformer.from_crs(piezometry.CRS, 4326, always_xy=True)
HEADER_WELLS = ["Cód. Punto", "Nombre", "Cód. Munic.", "Municipio", "Cód. Masa PHJ22 Geo.",
                "Masa Subterránea PHJ22 Geo.", "Cód. Masa PHJ22 Hidrog.", "Masa Subterránea PHJ22 Hidrog.",
                "UTM ETRS89 X", "UTM ETRS89 Y", "Auto. Activo", "Red Operativa", "Red Representativa"]
HEADER_LEVELS = ["Cód. Punto", "Fecha", "Med. Valid. Afectada (m)", "Med. Valid. Dinámica (m)",
                 "Med. Valid. Estática (m)", "Med. Valid. Otras (m)", "Niv. Valid. Afectada (m.s.n.m.)",
                 "Niv. Valid. Dinámica (m.s.n.m.)", "Niv. Valid. Estática (m.s.n.m.)", "Niv. Valid. Otras (m.s.n.m.)"]
# code, x, y (UTM ETRS89 zone 30): two wells in the test zone, one 2 km east of it, one far away
WELLS = [("09.01.001", 700_000, 4_400_000), ("09.01.002", 700_500, 4_400_500), ("09.01.003", 703_000, 4_400_000),
         ("09.01.004", 800_000, 4_300_000)]


def serial(day: str) -> float:
    """An Excel serial date, as the CHJ books store them (here at 23:59, like the automatic wells)."""
    return (pd.Timestamp(day) - piezometry.EXCEL_EPOCH).days + 0.9993


def book(header: list[str], rows: list[list]) -> bytes:
    """A minimal XLSX: one sheet, strings inline, numbers as values. Header strings go through sharedStrings."""
    letters = [chr(ord("A") + i) for i in range(len(header))]
    shared = "".join(f"<si><t>{escape(text)}</t></si>" for text in header)
    cells = ["".join(f'<c r="{c}1" t="s"><v>{i}</v></c>' for i, c in enumerate(letters))]
    for n, row in enumerate(rows, start=2):
        out = []
        for c, value in zip(letters, row, strict=False):
            if value is None:
                continue
            out.append(f'<c r="{c}{n}"><v>{value}</v></c>' if isinstance(value, (int, float))
                       else f'<c r="{c}{n}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        cells.append("".join(out))
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = (f'<worksheet xmlns="{ns}"><sheetData>'
             + "".join(f'<row r="{n}">{c}</row>' for n, c in enumerate(cells, start=1)) + "</sheetData></worksheet>")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as parts:
        parts.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}">{shared}</sst>')
        parts.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def package(data, levels: dict[str, list[tuple[str, float]]], names: dict[str, str | None] | None = None) -> None:
    """The F71 ZIP with the four wells and these static levels, in two field books, plus its manifest line.
    `names` replaces the name of some wells (None: an empty cell, as some points of the real inventory)."""
    names = names or {}
    wells = [[code, names.get(code, f"Pozo {code[-1]}"), "46000", "Municipio", "080-999", "Masa de prueba", "080-999",
              "Masa de prueba", x, y, 0, 1, 1] for code, x, y in WELLS]
    rows = [[code, serial(day), None, 9.9, 1.0, None, None, -50.0, value, None]
            for code, dated in levels.items() for day, value in dated]
    rows.append(["09.01.004", serial("2020-01-01"), None, 5.0, None, None, None, 1.0, None, None])  # dynamic only
    half = len(rows) // 2
    file = data / "packages" / "chj-f71" / "F71_Puntos_de_control_piezometrico.zip"
    file.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(file, "w") as archive:
        archive.writestr("F71_M10_Todas.xlsx", book(HEADER_WELLS, wells))
        archive.writestr("F71_D7_Medida en campo_1.xlsx", book(HEADER_LEVELS, rows[:half]))
        archive.writestr("F71_D7_Medida en campo_2.xlsx", book(HEADER_LEVELS, rows[half:]))
    line = {"source": "chj-f71", "version": "test", "item": file.stem, "scope": "cases",
            "path": file.relative_to(data).as_posix(), "mb": 0.01, "format": "zip", "license": "test",
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload", "url": piezometry.URL}}
    with (data / "manifest.jsonl").open("a", encoding="utf-8") as manifest:
        manifest.write(json.dumps(line) + "\n")


def zone(west, south, east, north, zone_id="z"):
    """A zone drawn in UTM metres, handed over in degrees like every zone."""
    outline = shapely.transform(box(west, south, east, north), lambda xy: np.column_stack(TO_DEGREES.transform(*xy.T)))
    return {"id": zone_id, "name": zone_id, "bbox": list(outline.bounds), "geometry": mapping(outline)}


LEVELS = {
    "09.01.001": [("2019-07-02", 3.0), ("2020-07-01", 2.5), ("2021-07-01", 2.0)],
    "09.01.002": [("2019-01-10", 10.0), ("2019-07-10", 8.0), ("2020-01-10", 9.0), ("2020-07-10", 7.0),
                  ("2021-07-10", 6.0)],
    "09.01.003": [("2019-07-03", 40.0), ("2020-07-03", 41.0)],
}
PERIOD = {"start": "2019-01-01", "end": "2021-12-31"}
INSIDE = (699_000, 4_399_000, 701_000, 4_401_000)


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    piezometry.tables.cache_clear()
    yield tmp_path
    piezometry.tables.cache_clear()


def test_inside_the_zone_the_well_with_most_dates_is_read(data):
    package(data, LEVELS)
    got = piezometry.read("groundwater_level", zone(*INSIDE), PERIOD)
    assert got["absence"] is None and got["unit"] == "m a.s.l." and got["origin"] == "package"
    assert got["data"].to_dict(orient="list") == {
        "t": ["2019-01-10", "2019-07-10", "2020-01-10", "2020-07-10", "2021-07-10"], "v": [10.0, 8.0, 9.0, 7.0, 6.0]}
    assert got["sources"][0]["items"] == ["09.01.002"] and got["sources"][0]["version"] == "test"
    assert got["support"] == {"since": 2019}
    assert got["coverage"] == {"valid_pct": round(100 * 5 / 36, 1), "n_dates": 5}
    point, others = got["limits"]
    assert "09.01.002" in point["en"] and "Masa de prueba" in point["es"]
    assert "not the level of the whole" in point["en"]
    assert "09.01.001" in others["en"] and "09.01.003" not in others["en"]


def test_months_of_a_season_and_the_period_bound_the_dates(data):
    package(data, LEVELS)
    got = piezometry.read("groundwater_level", zone(*INSIDE), {"start": "2019-03", "end": "2020", "months": [7]})
    # Within these dates both wells inside have two Julys: the tie goes to the first code.
    assert got["sources"][0]["items"] == ["09.01.001"] and list(got["data"].t) == ["2019-07-02", "2020-07-01"]
    assert got["coverage"]["valid_pct"] == 100.0  # two Julys asked (2019 and 2020), two measured


def test_without_a_well_inside_the_nearest_within_the_declared_distance(data):
    package(data, LEVELS)
    got = piezometry.read("groundwater_level", zone(704_000, 4_399_000, 706_000, 4_401_000), PERIOD)
    assert got["sources"][0]["items"] == ["09.01.003"] and list(got["data"].v) == [40.0, 41.0]
    assert "1.0 km from the zone outline" in got["limits"][0]["en"]
    assert "a 1,0 km del contorno" in got["limits"][0]["es"]


def test_absences_say_why(data):
    missing = piezometry.read("groundwater_level", zone(*INSIDE), PERIOD)
    assert missing["absence"]["reason"] == "needs_package" and missing["data"] is None
    package(data, LEVELS)
    far = piezometry.read("groundwater_level", zone(600_000, 4_300_000, 601_000, 4_301_000), PERIOD)
    assert far["absence"]["reason"] == "no_coverage" and "5 km" in far["absence"]["detail"]["en"]
    dynamic_only = piezometry.read("groundwater_level", zone(799_000, 4_299_000, 801_000, 4_301_000), PERIOD)
    assert dynamic_only["absence"]["reason"] == "no_coverage"
    assert "09.01.004" in dynamic_only["absence"]["detail"]["es"]
    before = piezometry.read("groundwater_level", zone(*INSIDE), {"start": "1990-01-01", "end": "1999-12-31"})
    assert before["absence"]["reason"] == "no_coverage" and before["sources"][0]["source"] == "chj-f71"


def test_a_well_without_a_name_is_told_by_its_code_and_never_as_nan(data):
    """U16 of the user QA: an empty «Nombre» came out as «(nan, Alzira, …)» in the limits."""
    package(data, LEVELS, names={"09.01.002": None})
    point = piezometry.read("groundwater_level", zone(*INSIDE), PERIOD)["limits"][0]
    assert "nan" not in point["en"].lower() and "nan" not in point["es"].lower()
    assert point["es"].startswith("Medida puntual en el piezómetro de la CHJ 09.01.002 (Municipio, masa de agua "
                                  "subterránea 080-999 Masa de prueba), dentro de la zona")
    assert "CHJ monitoring well 09.01.002 (Municipio, groundwater body 080-999 Masa de prueba)" in point["en"]


def test_parsed_once_and_kept_by_the_sha_of_the_zip(data):
    package(data, LEVELS)
    piezometry.read("groundwater_level", zone(*INSIDE), PERIOD)
    kept = sorted(path.name for path in (paths.ready() / "chj-f71").iterdir())
    sha = manifest_lines(sources={"chj-f71"})[0]["sha256"]
    assert kept == [f"{sha[:16]}-{piezometry.LAYOUT}-levels.parquet", f"{sha[:16]}-{piezometry.LAYOUT}-wells.parquet"]
    piezometry.tables.cache_clear()
    again = piezometry.read("groundwater_level", zone(*INSIDE), PERIOD)
    assert len(again["data"]) == 5


def test_through_the_door_the_limits_travel_with_the_saved_series(data):
    package(data, LEVELS)
    first = readers.read_series("groundwater_level", zone(*INSIDE), PERIOD)
    saved = readers.read_series("groundwater_level", zone(*INSIDE), PERIOD)
    assert first["origin"] == "package" and saved["origin"] == "derived"
    assert saved["limits"] == first["limits"] and saved["sources"][0]["items"] == ["09.01.002"]


def test_yearly_value_from_read_series(data):
    """The annual mean is made by the read_series operation, from the dated values, months included."""
    from hup_ops.ingest import read_series
    from hup_ops.registry import Context

    package(data, LEVELS)
    the_zone = zone(*INSIDE)
    ctx = Context(the_zone, PERIOD, 1, readers, lambda *_: None)
    yearly = read_series({"variable": "groundwater_level", "aggregate": "year", "statistic": "mean"}, {}, ctx)
    assert yearly["series"]["series"] == [{"t": "2019", "v": 9.0}, {"t": "2020", "v": 8.0}, {"t": "2021", "v": 6.0}]
    january = Context(the_zone, {**PERIOD, "months": [1]}, 1, readers, lambda *_: None)
    winter = read_series({"variable": "groundwater_level", "aggregate": "year"}, {}, january)["series"]["series"]
    assert winter == [{"t": "2019", "v": 10.0}, {"t": "2020", "v": 9.0}]


INSPECTED_SHA = "a869ae928e9cbdbc4615353a7acbbbac3a151e91e0eaf919cdcd96e6113079a1"  # Nadia's note, 14 Nov 2026


@pytest.mark.data
def test_real_sagunt_series_and_its_trend_from_hup_ops():
    """Sagunt's well comes out of the zone, not out of the code; the trend is that of hup_ops, whatever it says."""
    from hup_ops.ingest import read_series
    from hup_ops.registry import Context
    from hup_ops.trends import trend

    line = piezometry._package()
    if line is None:
        pytest.skip("The CHJ F71 package is not under HUP_DATA_DIR")
    found = places.resolve("Sagunt", "es")
    if found["status"] != "found":
        pytest.skip("Sagunt does not resolve here")
    sagunt = found["zone"]
    got = piezometry.read("groundwater_level", sagunt, {"start": "1950-01-01", "end": "2026-12-31"})
    assert got["absence"] is None and got["unit"] == "m a.s.l."
    if line["sha256"] == INSPECTED_SHA:  # the download Nadia inspected: same dates as her count
        assert got["sources"][0]["items"] == ["08.21.005"] and got["coverage"]["n_dates"] == 293
        assert (got["data"].t.min(), got["data"].t.max()) == ("1981-02-19", "2026-10-15")
    period = {"start": "1999-01-01", "end": "2025-12-31"}  # the longest run of years with a measurement
    ctx = Context(sagunt, period, 1, readers, lambda *_: None)
    yearly = read_series({"variable": "groundwater_level", "aggregate": "year", "statistic": "mean"}, {}, ctx)
    series = yearly["series"]
    assert [row["t"] for row in series["series"]] == [str(year) for year in range(1999, 2026)]
    result = trend({}, {"read": [series]}, ctx)["trend"]
    assert result["diagnostics"]["n_years"] == 27 and result["unit"] == "m a.s.l./year"
