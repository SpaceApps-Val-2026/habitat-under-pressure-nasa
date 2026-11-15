"""Package A4: a place name becomes a zone, or a question. Gazetteer answers are recorded: no network here."""
import json

import pytest
import requests
from shapely.geometry import shape

from hup_data import paths, places


def square(west, south, size):
    ring = [[west, south], [west + size, south], [west + size, south + size], [west, south + size], [west, south]]
    return {"type": "Polygon", "coordinates": [ring]}


def row(osm_id, name, importance, geometry, category="boundary", osm_type="relation"):
    return {"osm_type": osm_type, "osm_id": osm_id, "display_name": name, "importance": importance,
            "category": category, "type": "administrative", "geojson": geometry}


RECORDED = {
    "townville": [row(1, "Townville, Region, Country", 0.62, square(-0.3, 39.6, 0.1)),
                  row(9, "Townville Bakery", 0.90, {"type": "Point", "coordinates": [-0.25, 39.65]}, "shop", "node")],
    "springfield": [row(2, "Springfield, North", 0.55, square(10, 50, 0.2)),
                    row(3, "Springfield, South", 0.52, square(-80, 30, 0.2))],
    "capital": [row(4, "Capital, Country", 0.85, square(2, 41, 0.3)),
                row(5, "Capital, a hamlet elsewhere", 0.30, square(100, 10, 0.05))],
    "a point only": [row(6, "A point only", 0.7, {"type": "Point", "coordinates": [1, 1]}, "place", "node")],
    "mi pueblo": [],
}


@pytest.fixture
def gazetteer(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_OFFLINE", raising=False)
    monkeypatch.setattr(places.paths, "CATALOG_DIR", tmp_path / "catalog")
    asked = []

    def ask(text, language):
        asked.append((text, language))
        return RECORDED[text]

    monkeypatch.setattr(places, "_ask_gazetteer", ask)
    return asked


def test_a_town_is_found_with_polygon_and_source(gazetteer):
    answer = places.resolve("  Townville ", "en")
    assert answer["status"] == "found"
    zone = answer["zone"]
    assert zone["id"] == "osm-relation-1" and zone["bbox"] == [-0.3, 39.6, -0.2, 39.7]
    assert shape(zone["geometry"]).area > 0 and "OpenStreetMap" in zone["source"]
    assert gazetteer == [("townville", "en")]  # only the place text travels, normalised


def test_the_second_time_comes_from_the_cache(gazetteer):
    first = places.resolve("Townville")
    again = places.resolve("TOWNVILLE")
    assert again == first and len(gazetteer) == 1
    assert len(list(paths.places().glob("*.json"))) == 1


def test_a_name_in_several_places_is_a_question(gazetteer):
    answer = places.resolve("Springfield")
    assert answer["status"] == "ambiguous"
    assert [zone["name"] for zone in answer["candidates"]] == ["Springfield, North", "Springfield, South"]
    assert places.resolve("Capital")["zone"]["name"] == "Capital, Country"  # a clear winner is not a question


def test_no_place_no_zone_never_a_default_box(gazetteer):
    assert places.resolve("mi pueblo") == {"status": "not_found"}
    assert places.resolve("") == {"status": "not_found"} and places.resolve("   ") == {"status": "not_found"}
    assert ("", "en") not in gazetteer


def test_a_place_mapped_as_a_point_is_a_circle_that_says_so(gazetteer):
    zone = places.resolve("A point only")["zone"]
    west, south, east, north = zone["bbox"]
    assert (north - south) * 111.2 == pytest.approx(4, rel=0.02)  # a circle of 2 km radius
    assert "circle of 2 km" in zone["source"] and "not an official boundary" in zone["source"]
    # A shop that shares the name of a town never beats the town, and never becomes a zone.
    assert places.resolve("Townville")["zone"]["id"] == "osm-relation-1"


def test_offline_and_unreachable_do_not_raise_and_are_not_cached(gazetteer, monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    assert places.resolve("Townville") == {"status": "not_found"} and gazetteer == []
    monkeypatch.delenv("HUP_OFFLINE")

    def down(text, language):
        raise requests.ConnectionError("no route")

    monkeypatch.setattr(places, "_ask_gazetteer", down)
    assert places.resolve("Townville") == {"status": "not_found"}
    assert not paths.places().exists() or not list(paths.places().glob("*.json"))


def test_known_zones_of_the_catalog_win_and_work_offline(gazetteer, monkeypatch, tmp_path):
    folder = tmp_path / "catalog" / "zones"
    folder.mkdir(parents=True)
    feature = {"type": "Feature", "geometry": square(5, 5, 1),
               "properties": {"name": "Some Site", "source": "Official register, plot 7", "aliases": ["the site"]}}
    (folder / "some-site.geojson").write_text(json.dumps(feature), encoding="utf-8")
    monkeypatch.setenv("HUP_OFFLINE", "1")
    for wording in ("Some Site", "some-site", "The  SITE"):
        zone = places.resolve(wording)["zone"]
        assert zone["id"] == "some-site" and zone["source"] == "Official register, plot 7"
    assert gazetteer == []


def test_rings_do_not_overlap_and_are_measured_in_metres(gazetteer):
    zone = places.resolve("Townville")["zone"]
    made = places.rings(zone, [3, 1])  # any order in, sorted out
    assert [ring["id"] for ring in made] == ["osm-relation-1-ring-0-1km", "osm-relation-1-ring-1-3km"]
    core, near, far = shape(zone["geometry"]), shape(made[0]["geometry"]), shape(made[1]["geometry"])
    for a, b in ((core, near), (core, far), (near, far)):
        assert a.intersection(b).area < 1e-9
    # 1 km north of the zone edge is about 0.009 degrees of latitude, whatever the longitude scale.
    assert near.bounds[3] - core.bounds[3] == pytest.approx(1 / 111.2, rel=0.02)
    assert near.bounds[2] - core.bounds[2] == pytest.approx(1 / (111.3 * 0.77), rel=0.03)  # cos(39.65°) ≈ 0.77
    reference = places.reference(zone, 3)
    assert shape(reference["geometry"]).area == pytest.approx(near.area + far.area, rel=1e-3)
    assert "Ring" in reference["source"] and reference["bbox"][0] < zone["bbox"][0]


def test_rings_need_a_geometry_and_positive_distances(gazetteer):
    with pytest.raises(ValueError):
        places.rings({"name": "box only", "bbox": [0, 0, 1, 1]}, [1])
    zone = places.resolve("Townville")["zone"]
    for bad in ([], [0], [-1, 2]):
        with pytest.raises(ValueError):
            places.rings(zone, bad)


def test_the_name_comes_in_the_reader_language_when_the_gazetteer_has_it():
    """Dubai came with its Arabic name as the first part, whatever language was asked for."""
    row = {"display_name": "دبي, Dubái, Emiratos Árabes Unidos",
           "namedetails": {"name": "دبي", "name:es": "Dubái", "name:en": "Dubai"}}
    assert places._display_name(row, "es") == "Dubái, Emiratos Árabes Unidos"
    assert places._display_name(row, "en") == "Dubai, Dubái, Emiratos Árabes Unidos"
    assert places._display_name({"display_name": "Paiporta, Valencia, España"}, "es") == "Paiporta, Valencia, España"
    only_english = {"display_name": "دبي, Dubái", "namedetails": {"name:en": "Dubai"}}
    assert places._display_name(only_english, "ca") == "Dubai, Dubái"
