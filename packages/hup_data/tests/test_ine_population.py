"""INE population by census section, spread by residential built-up surface at 100 m, and GHSL where the INE is not.

Synthetic: three sections of one municipality on a 100 m built-up grid in Mollweide, with INE tables written in the
format of the INE CSV export. The test with the real files compares the total of a municipality with the official
figure of the INE: "Cifras oficiales de población de los municipios españoles: Revisión del Padrón Municipal",
1 January 2025, table 2903 (Valencia/València), https://www.ine.es/jaxiT3/Tabla.htm?t=2903, read on 2026-11-14 from
https://www.ine.es/jaxiT3/files/t/es/csv_bdsc/2903.csv (the package ine-padron-municipios-46).
"""
import hashlib
import json

import numpy as np
import pytest
import rasterio
import shapely
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from hup_data import masks, readers
from hup_data.prepare import vectors
from hup_data.readers import ine_population, population

MOLLWEIDE = "ESRI:54009"
X, Y = -30000.0, 4770000.0  # Mollweide metres, about 0.35 W, 39.6 N: the corner of the built-up grid
TILE = "GHS_BUILT_S{}_E2025_GLOBE_R2023A_54009_100_V1_0_R5_C19"
GHSL_KEY = "ghsl/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0/GHS_POP_E2025_GLOBE_R2023A_54009_1000_V1_0.tif"
# Census on 1 January 2025: section A 400 people, B 600 (and C, created in 2026, has no figure); register 1100.
CENSUS = {"4699901001": 400, "4699901002": 600}
REGISTER = 1100


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    return tmp_path


def to_wgs84(geometry):
    back = Transformer.from_crs(MOLLWEIDE, 4326, always_xy=True).transform
    return shapely.transform(shapely.segmentize(geometry, 25), lambda xy: np.column_stack(back(xy[:, 0], xy[:, 1])))


def metres(x0, y0, x1, y1):
    """A box in metres from the grid corner (y downwards), in EPSG:4326."""
    return to_wgs84(box(X + x0, Y - y1, X + x1, Y - y0))


# Section A: the two cells of row 0, cols 0..1. B: cols 2..3 of row 0. C: cols 4..5 of row 0, next to B.
A, B, C = metres(0, 0, 200, 100), metres(200, 0, 400, 100), metres(400, 0, 600, 100)


def zone(geometry):
    return {"id": "z", "name": "z", "bbox": list(geometry.bounds), "geometry": mapping(geometry)}


def add_line(data, source, file, fmt):
    line = {"source": source, "item": file.stem, "scope": "cases", "path": file.relative_to(data).as_posix(),
            "mb": 0.01, "format": fmt, "license": "test", "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "product": f"test {file.stem}",
            "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload", "url": f"https://example.org/{file.name}"}}
    with (data / "manifest.jsonl").open("a", encoding="utf-8") as manifest:
        manifest.write(json.dumps(line) + "\n")


def write_tables(data, census=CENSUS, register=REGISTER):
    folder = data / "packages" / "ine-population"
    folder.mkdir(parents=True, exist_ok=True)
    rows = ["Provincias;Municipios;Secciones;Sexo;Nacionalidad;Periodo;Total",
            f'46 Valencia/València;;;Total;Total;2025;{sum(census.values()):,}'.replace(",", "."),
            f'46 Valencia/València;46999 Prova;;Total;Total;2025;{sum(census.values()):,}'.replace(",", ".")]
    for code, people in census.items():
        rows += [f"46 Valencia/València;46999 Prova;{code} Prova sección {code[5:]};{sex};Total;2025;{value}"
                 for sex, value in (("Total", people), ("Hombres", people // 2))]
        rows.append(f'46 Valencia/València;46999 Prova;{code} Prova sección {code[5:]};Total;Total;2024;""')
    (folder / "69274.csv").write_text("\n".join(rows) + "\n", encoding="utf-8-sig")
    municipal = ["Municipios;Sexo;Periodo;Total", f"46999 Prova;Total;2025;{register:,}".replace(",", "."),
                 "46999 Prova;Mujeres;2025;1", "46999 Prova;Total;2024;5"]
    (folder / "2903.csv").write_text("\n".join(municipal) + "\n", encoding="utf-8-sig")
    add_line(data, "ine-population", folder / "69274.csv", "csv")
    add_line(data, "ine-population", folder / "2903.csv", "csv")


def write_sections(data, extra=()):
    """Sections A, B and C of municipality 46999, and `extra` (code, geometry) sections of other municipalities."""
    folder = data / "packages" / "ine"
    folder.mkdir(parents=True, exist_ok=True)
    features = [{"type": "Feature", "geometry": mapping(geometry),
                 "properties": {"CUSEC": code, "CUMUN": code[:5], "CDIS": code[5:7], "CPRO": code[:2]}}
                for code, geometry in (("4699901001", A), ("4699901002", B), ("4699901003", C), *extra)]
    file = folder / "seccionado_2026__SECC_CE_20260101.geojson"
    file.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    add_line(data, "ine", file, "geojson")
    assert not vectors.build()["failed"]


def write_grid(data, key, values, transform, nodata, source):
    path = data / "ready" / "grids" / key
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=values.shape[0], width=values.shape[1], count=1,
                       dtype=values.dtype, crs=MOLLWEIDE, transform=transform, nodata=nodata) as out:
        out.write(values, 1)
    index = data / "ready" / "grids" / "index.json"
    entries = json.loads(index.read_text(encoding="utf-8"))["entries"] if index.is_file() else {}
    entries[key] = {"source": source, "path": path.relative_to(data).as_posix(), "version": "test", "license": "test",
                    "from": f"packages/{source}/{key.split('/')[1]}.zip"}
    index.write_text(json.dumps({"entries": entries}), encoding="utf-8")


@pytest.fixture
def built(data):
    """Row 0 of a 100 m grid: cell 0 holds 500 m² built (200 non-residential), cell 1 holds 100; B and C, nothing."""
    total, nres = np.zeros((10, 10), "uint16"), np.zeros((10, 10), "uint16")
    total[0, 0], nres[0, 0], total[0, 1] = 500, 200, 100
    for kind, values in (("", total), ("_NRES", nres)):
        name = TILE.format(kind)
        write_grid(data, f"ghsl-built-s/{name}/{name}.tif", values, from_origin(X, Y, 100, 100), 65535,
                   "ghsl-built-s")


@pytest.fixture
def ine(data, built):
    write_tables(data)
    write_sections(data)


@pytest.fixture
def ghsl(data):
    """A 20 x 20 km GHSL-like grid of 1 km cells around the sections, 10 people in each cell."""
    write_grid(data, GHSL_KEY, np.full((20, 20), 10.0), from_origin(X - 10000, Y + 10000, 1000, 1000), -200.0, "ghsl")


def test_the_municipal_total_is_the_official_register_figure(ine):
    whole = shapely.union_all([A, B, C])
    got = population.population_now("population", zone(whole))
    assert got["absence"] is None and got["data"].t.tolist() == [2025]
    assert got["data"].v.iloc[0] == pytest.approx(REGISTER, rel=1e-9)
    assert [s["source"] for s in got["sources"]] == ["ine-population", "ine", "ghsl-built-s"]
    assert got["support"]["resolution_m"] == 100 and got["coverage"]["valid_pct"] == 100
    assert "official register figure" in got["limits"][0]["en"] and "modelled split" in got["limits"][0]["en"]


def test_inside_a_section_people_follow_the_residential_built_up_surface(ine):
    scale = REGISTER / sum(CENSUS.values())
    first = population.population_now("population", zone(metres(0, 0, 100, 100)))  # 300 m² residential of 400
    second = population.population_now("population", zone(metres(100, 0, 200, 100)))  # 100 m² of 400
    assert first["data"].v.iloc[0] == pytest.approx(400 * scale * 300 / 400, rel=1e-6)
    assert second["data"].v.iloc[0] == pytest.approx(400 * scale * 100 / 400, rel=1e-6)
    half = population.population_now("population", zone(metres(0, 0, 50, 100)))  # half of the first cell
    assert half["data"].v.iloc[0] == pytest.approx(400 * scale * 150 / 400, rel=1e-4)


def test_a_section_without_built_up_surface_is_spread_by_area_and_says_so(ine):
    scale = REGISTER / sum(CENSUS.values())
    got = population.population_now("population", zone(metres(200, 0, 300, 100)))  # half of B, which has no built-up
    # B is pooled with C (created in 2026, no figure): B's people over B and C, by area, so a quarter of them here.
    assert got["data"].v.iloc[0] == pytest.approx(600 * scale / 4, rel=1e-4)
    text = " ".join(limit["en"] for limit in got["limits"])
    assert "spread by area" in text and "pooled" in text


def test_without_the_built_up_grid_everything_is_spread_by_area(data):
    write_tables(data)
    write_sections(data)
    scale = REGISTER / sum(CENSUS.values())
    got = population.population_now("population", zone(metres(0, 0, 100, 100)))
    assert got["data"].v.iloc[0] == pytest.approx(400 * scale / 2, rel=1e-4)
    assert [s["source"] for s in got["sources"]] == ["ine-population", "ine"]
    assert any("built-up grid is not prepared" in limit["en"] for limit in got["limits"])


def test_a_zone_outside_the_sections_uses_ghsl(ine, ghsl):
    far = metres(5000, 5000, 7000, 7000)  # inside the GHSL grid, far from the sections
    got = population.population_now("population", zone(far))
    assert got["absence"] is None and got["sources"][0]["source"] == "ghsl"
    assert got["data"].v.iloc[-1] == pytest.approx(10.0 * 4, rel=1e-3)
    across = population.population_now("population", zone(metres(0, 0, 600, 1000)))  # mostly outside the sections
    assert across["sources"][0]["source"] == "ghsl"
    assert any("outside the census sections" in limit["en"] and "GHSL" in limit["en"] for limit in across["limits"])


def test_a_coastal_strip_outside_every_section_counts_as_no_one_and_says_so(ine):
    coast = metres(0, 0, 600, 103)  # A, B and C plus a 3 m strip with no section: 2.9% of the zone
    got = population.population_now("population", zone(coast))
    assert got["sources"][0]["source"] == "ine-population"
    assert got["data"].v.iloc[0] == pytest.approx(REGISTER, rel=1e-6)
    limit = next(limit for limit in got["limits"] if "no census section" in limit["en"])
    assert limit["en"].startswith("2.91%") and limit["es"].startswith("El 2,91 % de la zona no pertenece")


def test_a_strip_outside_every_section_beyond_the_cap_uses_ghsl(ine, ghsl):
    wide = metres(0, 0, 600, 110)  # a 10 m strip with no section: 9.1% of the zone, over NO_SECTION
    got = population.population_now("population", zone(wide))
    assert got["sources"][0]["source"] == "ghsl"
    assert any("outside the census sections with an INE figure" in limit["en"] for limit in got["limits"])


def test_a_section_without_a_figure_still_counts_against_the_sliver(data, built, ghsl):
    write_tables(data)
    write_sections(data, extra=[("4699801001", metres(600, 0, 610, 100))])  # municipality 46998 has no figure
    got = population.population_now("population", zone(metres(0, 0, 610, 100)))  # 1.6% in that section
    assert got["sources"][0]["source"] == "ghsl"
    assert any("1.6% of the zone lies outside the census sections with an INE figure" in limit["en"]
               for limit in got["limits"])


def test_near_a_border_province_the_part_outside_every_section_counts_against_the_sliver(data, built, ghsl):
    write_tables(data)
    write_sections(data, extra=[("1799901001", metres(600, 0, 601, 103))])  # a section of Girona in the zone
    got = population.population_now("population", zone(metres(0, 0, 601, 103)))  # 2.9% with no section
    assert got["sources"][0]["source"] == "ghsl"
    assert not any("no census section" in limit["en"] for limit in got["limits"])


def test_a_series_saved_by_an_older_population_reader_is_not_served(ine):
    from hup_data import fingerprint, store

    whole, period = zone(shapely.union_all([A, B, C])), {"start": "2020-01-01", "end": "2025-12-31"}
    old = fingerprint.sha({"read_series": "population", "reader": "2", "zone": fingerprint.zone_key(whole),
                           "bbox": whole["bbox"], "period": period})  # the key of version 2, as read_series made it
    store.put_derived(old, {"meta": {"sources": [{"source": "ghsl"}], "support": {}, "coverage": {}, "unit": "people"},
                            "rows": {"t": [2020, 2025], "v": [1.0, 2.0]}})
    got = readers.read_series("population", whole, period)
    assert got["origin"] == "package" and got["sources"][0]["source"] == "ine-population"
    assert got["data"].v.iloc[-1] == pytest.approx(REGISTER, rel=1e-9)
    assert store.get_derived(old) is not None  # unreachable, not deleted
    assert readers.read_series("population", whole, period)["origin"] == "derived"  # the new reading is saved


def test_without_ine_tables_ghsl_answers_through_the_common_door(data, built, ghsl):
    write_sections(data)
    got = readers.read_series("population", zone(A), {"start": "2025-01-01", "end": "2025-12-31"})
    assert got["absence"] is None and got["sources"][0]["source"] == "ghsl"


def test_population_in_mask_uses_the_ine_split(ine, data):
    folder = data / "packages" / "test-layer"
    folder.mkdir(parents=True)
    file = folder / "first-cell.geojson"
    feature = {"type": "Feature", "properties": {}, "geometry": mapping(metres(0, 0, 100, 100))}
    file.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}), encoding="utf-8")
    add_line(data, "test-layer", file, "geojson")
    assert not vectors.build()["failed"]
    got = masks.population_in_mask("test-layer/first-cell", zone(shapely.union_all([A, B, C])))
    assert got["absence"] is None and got["year"] == 2025
    assert got["population"] == pytest.approx(400 * REGISTER / sum(CENSUS.values()) * 300 / 400, rel=1e-4)
    assert [s["source"] for s in got["sources"]] == ["test-layer", "ine-population", "ine", "ghsl-built-s"]
    assert got["support"]["resolution_m"] == 100


# ---------------------------------------------------------------- with the real files
INE_2025 = {"46017": ("Alzira", 48236), "46220": ("Sagunt", 73031), "46250": ("València", 840792)}  # table 2903


@pytest.mark.data
@pytest.mark.parametrize("code", sorted(INE_2025))
def test_real_municipality_total_is_the_official_register_figure(code):
    from hup_data.readers import vectors as layers

    found = layers.read_layer("ine/seccionado_2026__SECC_CE_20260101", [-0.6, 39.0, -0.1, 39.8])
    if found["absence"] is not None or ine_population.figures()[0] is None:
        pytest.skip("The INE sections or the INE population tables are not prepared on this computer")
    outline = shapely.union_all(found["data"][found["data"].CUMUN == code].geometry.values)
    got = population.population_now("population", zone(outline))
    assert got["absence"] is None and got["sources"][0]["source"] == "ine-population"
    assert got["data"].t.tolist() == [2025] and got["data"].v.iloc[0] == pytest.approx(INE_2025[code][1], abs=0.5)

