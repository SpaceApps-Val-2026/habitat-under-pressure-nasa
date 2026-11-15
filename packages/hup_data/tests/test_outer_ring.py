"""The outer reference proposed by Guillem: `rings(zone, [3, 20])[1]` is the 3-20 km annulus. Synthetic, no network."""
import math

import numpy as np
import pytest
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import Point, shape

from hup_data import places


def square(west, south, size):
    ring = [[west, south], [west + size, south], [west + size, south + size], [west, south + size], [west, south]]
    return {"type": "Polygon", "coordinates": [ring]}


def metric(zone):
    """Geometries of a zone and its rings in metres, all in one local projection centred on the zone."""
    centre = shape(zone["geometry"]).centroid
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={centre.y} +lon_0={centre.x} +datum=WGS84 +units=m")
    forward = Transformer.from_crs(4326, local, always_xy=True)
    return lambda geometry: shapely.transform(
        shape(geometry), lambda xy: np.column_stack(forward.transform(xy[:, 0], xy[:, 1])))


def polygon_zone():
    return {"id": "osm-relation-1", "name": "Townville", "bbox": [-0.45, 39.40, -0.40, 39.45],
            "geometry": square(-0.45, 39.40, 0.05), "source": "test"}


def point_zone():
    """A place mapped only as a point: the reader makes it a circle of POINT_RADIUS_KM."""
    row = {"osm_type": "node", "osm_id": 6, "display_name": "A point only", "importance": 0.7, "category": "place",
           "geojson": {"type": "Point", "coordinates": [-0.42, 39.42]}}
    return places._candidates([row])[0]["zone"]


@pytest.mark.parametrize("make", [polygon_zone, point_zone])
def test_the_second_ring_is_the_3_to_20_km_annulus(make):
    zone = make()
    disc, outer = places.rings(zone, [3, 20])
    assert disc["id"] == f"{zone['id']}-ring-0-3km" and outer["id"] == f"{zone['id']}-ring-3-20km"
    assert "3-20 km" in outer["name"]
    to_m = metric(zone)
    core, near, far = to_m(zone["geometry"]), to_m(disc["geometry"]), to_m(outer["geometry"])

    # It touches neither the zone nor the 0-3 km disc, and starts 3 km from the edge of the zone.
    assert far.intersection(core).area < 1.0 and far.intersection(near).area < 1.0  # square metres
    assert core.distance(far) == pytest.approx(3000, abs=30)
    edge = shapely.get_coordinates(far.exterior if far.geom_type == "Polygon" else far.geoms[0].exterior)
    assert max(core.distance(Point(xy)) for xy in edge) == pytest.approx(20000, rel=0.01)

    # Area of a buffer of a convex zone: A + P r + pi r^2. The annulus is the difference of two of them.
    expected = core.length * (20000 - 3000) + math.pi * (20000**2 - 3000**2)
    assert far.area == pytest.approx(expected, rel=0.01)
    # The disc and the ring together are everything within 20 km, without the zone.
    assert (near.area + far.area) == pytest.approx(core.buffer(20000, quad_segs=16).area - core.area, rel=1e-3)
    # And the same disc as the reference used today (`reference(zone, 3)`).
    assert near.symmetric_difference(to_m(places.reference(zone, 3)["geometry"])).area < 1.0


def test_a_point_zone_ring_is_a_circle_annulus_from_5_to_22_km():
    zone = point_zone()
    radius = places.POINT_RADIUS_KM
    far = metric(zone)(places.rings(zone, [3, 20])[1]["geometry"])
    assert far.area / 1e6 == pytest.approx(math.pi * ((radius + 20) ** 2 - (radius + 3) ** 2), rel=0.01)
