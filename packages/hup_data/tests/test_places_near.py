"""A place chosen on a map, and candidates ordered by how near they are to the person."""
import pytest

from hup_data import places


def zone(name, lon, lat):
    return {"id": name, "name": name, "bbox": [lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1], "source": "test"}


def test_ambiguous_candidates_come_nearest_first_and_nothing_is_chosen():
    answer = {"status": "ambiguous", "candidates": [zone("far", 3.0, 36.7), zone("near", -0.4, 39.1)]}
    ordered = places.nearest_first(answer, (-0.38, 39.47))
    assert ordered["status"] == "ambiguous"
    assert [c["id"] for c in ordered["candidates"]] == ["near", "far"]
    assert ordered["candidates"][0]["distance_km"] < 60 < ordered["candidates"][1]["distance_km"]
    assert answer["candidates"][0]["id"] == "far" and "distance_km" not in answer["candidates"][0]


def test_without_a_position_or_with_one_clear_place_the_answer_is_untouched():
    ambiguous = {"status": "ambiguous", "candidates": [zone("a", 0, 0), zone("b", 1, 1)]}
    found = {"status": "found", "zone": zone("a", 0, 0)}
    assert places.nearest_first(ambiguous, None) is ambiguous
    assert places.nearest_first(found, (0.0, 0.0)) is found


def test_a_point_on_the_map_is_a_circle_that_says_what_it_is():
    got = places.point_zone(-0.4186, 39.4283, 2)
    west, south, east, north = got["bbox"]
    assert west < -0.4186 < east and south < 39.4283 < north
    assert 0.03 < north - south < 0.04  # about 4 km across
    assert "not an official boundary" in got["source"] and "2 km" in got["source"]
    assert got["geometry"]["type"] == "Polygon" and got["id"].startswith("point-")
    assert places.point_zone(-0.4186, 39.4283, 2, "My street")["name"] == "My street"


@pytest.mark.parametrize("lon, lat, radius", [(200, 0, 2), (0, 95, 2), (0, 0, 0), (0, 0, 500)])
def test_a_point_out_of_range_is_refused(lon, lat, radius):
    with pytest.raises(ValueError):
        places.point_zone(lon, lat, radius)
