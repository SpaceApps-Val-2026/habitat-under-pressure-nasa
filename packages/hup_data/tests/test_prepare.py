"""Package A2, the other preparations: vector layers, gridded files inside zips, and documents."""
import hashlib
import json
import zipfile

import geopandas as gpd
import pytest
from pypdf import PdfWriter
from shapely.geometry import box

from hup_data import paths
from hup_data.prepare import docs, unpack, vectors
from hup_data.prepare.common import read_index
from hup_data.readers import docs as docs_reader
from hup_data.readers import vectors as vectors_reader


def line(root, source, path, fmt):
    file = root / path
    return {"source": source, "item": path, "scope": "global", "path": path, "mb": 1, "format": fmt, "license": "test",
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            "ingest": {"date": "2026-11-12T00:00:00Z", "origin": "predownload", "url": "https://example.org/doc"}}


def write_manifest(root, lines):
    (root / "manifest.jsonl").write_text("".join(json.dumps(item) + "\n" for item in lines), encoding="utf-8")


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    return tmp_path


def squares(crs, origin_x, origin_y, size, n=30):
    """A row of n squares, each with its number: enough to see that a window returns only some of them."""
    shapes = [box(origin_x + i * size, origin_y, origin_x + (i + 1) * size, origin_y + size) for i in range(n)]
    return gpd.GeoDataFrame({"n": list(range(n))}, geometry=shapes, crs=crs)


# ---------------------------------------------------------------- vectors
def test_vector_layers_from_geojson_and_from_a_zipped_shapefile(data):
    folder = data / "packages" / "layers"
    folder.mkdir(parents=True)
    squares(4326, 0, 40, 0.1).to_file(folder / "plain.geojson", driver="GeoJSON")
    # A shapefile in a projected system (UTM 30N), inside a zip, as the official layers arrive.
    squares(25830, 700000, 4400000, 1000).to_file(data / "utm.shp")
    with zipfile.ZipFile(folder / "official.zip", "w") as archive:
        for part in data.glob("utm.*"):
            archive.write(part, f"inner folder/{part.name}")
    write_manifest(data, [line(data, "layers", "packages/layers/plain.geojson", "geojson"),
                          line(data, "layers", "packages/layers/official.zip", "zip")])

    result = vectors.build()
    assert sorted(result["built"]) == ["layers/official__utm", "layers/plain"] and not result["failed"]
    assert vectors.build()["built"] == []  # nothing changed: nothing to do

    index = vectors_reader.layers()
    assert index["layers/official__utm"]["original_crs"] == "EPSG:25830"
    assert index["layers/plain"]["rows"] == 30 and index["layers/plain"]["columns"] == ["n"]

    window = vectors_reader.read_layer("layers/plain", [0.95, 40.0, 1.25, 40.1])
    assert window["absence"] is None and sorted(window["data"].n) == [9, 10, 11, 12]
    reprojected = vectors_reader.read_layer("layers/official__utm")["data"]
    assert reprojected.crs.to_epsg() == 4326 and len(reprojected) == 30
    west, south, east, north = reprojected.total_bounds
    assert -1 < west < east < 0 and 39 < south < north < 40.5  # UTM 30N metres became degrees near Greenwich

    far = vectors_reader.read_layer("layers/plain", [100, 0, 101, 1])
    assert far["data"] is None and far["absence"]["reason"] == "no_coverage"
    assert vectors_reader.read_layer("layers/not-there")["absence"]["reason"] == "needs_package"


def test_a_layer_without_coordinate_system_is_reported_not_guessed(data):
    folder = data / "packages" / "layers"
    folder.mkdir(parents=True)
    squares(None, 0, 0, 1).to_file(data / "nocrs.shp")
    with zipfile.ZipFile(folder / "nocrs.zip", "w") as archive:
        for part in data.glob("nocrs.*"):
            archive.write(part, part.name)
    squares(4326, 0, 40, 0.1).to_file(folder / "good.geojson", driver="GeoJSON")
    write_manifest(data, [line(data, "layers", "packages/layers/nocrs.zip", "zip"),
                          line(data, "layers", "packages/layers/good.geojson", "geojson")])
    result = vectors.build()
    assert result["built"] == ["layers/good"]  # the good layer is not held back by the bad one
    assert "coordinate system" in result["failed"]["layers/nocrs__nocrs"]


# ---------------------------------------------------------------- grids inside zips
def test_grids_are_unpacked_flat_and_only_once(data):
    folder = data / "packages" / "pop"
    folder.mkdir(parents=True)
    with zipfile.ZipFile(folder / "grid.zip", "w") as archive:
        archive.writestr("deep/folder/population.tif", b"raster bytes")
        archive.writestr("deep/folder/population.tif.ovr", b"overviews")
        archive.writestr("readme.pdf", b"not a grid")
        archive.writestr("../../escape.tif", b"must not leave the folder")
    with zipfile.ZipFile(folder / "no-grid.zip", "w") as archive:
        archive.writestr("table.xlsx", b"nothing to unpack")
    write_manifest(data, [line(data, "pop", "packages/pop/grid.zip", "zip"),
                          line(data, "pop", "packages/pop/no-grid.zip", "zip")])

    assert len(unpack.build()["built"]) == 3
    target = data / "ready" / "grids" / "pop" / "grid"
    assert sorted(path.name for path in target.iterdir()) == ["escape.tif", "population.tif", "population.tif.ovr"]
    assert (target / "population.tif").read_bytes() == b"raster bytes"
    assert not (data / "escape.tif").exists() and not (data / "ready" / "grids" / "pop" / "no-grid").exists()
    assert unpack.build()["built"] == []
    assert read_index("grids")["pop/grid/population.tif"]["member"] == "deep/folder/population.tif"


# ---------------------------------------------------------------- documents
def test_documents_are_indexed_by_page_and_replaced_when_they_change(data):
    folder = data / "packages" / "plans"
    folder.mkdir(parents=True)
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=200, height=200)
    writer.write(folder / "plan.pdf")
    write_manifest(data, [line(data, "plans", "packages/plans/plan.pdf", "pdf")])

    assert docs.build()["built"] == ["plans/plan"]
    entry = docs_reader.documents()["plans/plan"]
    assert entry["pages"] == 3 and entry["pages_without_text"] == 3  # blank pages: stored empty, and counted
    assert docs.build()["built"] == []
    assert docs_reader.search("water") == []

    # Search works on real text and is safe with any user input.
    import sqlite3
    con = sqlite3.connect(docs.database())
    with con:
        con.execute("INSERT INTO pages VALUES ('plans', 'other', 7, 'La demanda de agua del sistema Júcar crece.')")
        con.execute("INSERT INTO pages VALUES ('plans', 'other', 8, 'Nada que ver con el tema.')")
    con.close()
    hits = docs_reader.search("demanda jucar")  # accents and case do not matter
    assert [(hit["document"], hit["page"]) for hit in hits] == [("other", 7)] and "Júcar" in hits[0]["excerpt"]
    assert docs_reader.search("demanda", document="plan") == []
    for hostile in ('" OR 1=1 --', "agua AND NOT", "*", "NEAR(", ""):
        assert isinstance(docs_reader.search(hostile), list)

    # The document changes: its old pages go away, they are not duplicated.
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(folder / "plan.pdf")
    write_manifest(data, [line(data, "plans", "packages/plans/plan.pdf", "pdf")])
    assert docs.build()["built"] == ["plans/plan"]
    con = sqlite3.connect(docs.database())
    assert con.execute("SELECT count(*) FROM pages WHERE document = 'plan'").fetchone()[0] == 1
    con.close()


def test_nothing_prepared_is_empty_not_an_error(data):
    assert vectors_reader.layers() == {} and docs_reader.documents() == {} and docs_reader.search("water") == []
    assert paths.ready() == data / "ready"
