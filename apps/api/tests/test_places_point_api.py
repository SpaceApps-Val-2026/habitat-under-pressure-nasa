"""The routes behind the place picker: a point chosen on a map, and candidates ordered by nearness."""
from fastapi.testclient import TestClient

from hup_api import contracts, main
from hup_data import places


def test_a_point_gives_a_zone_that_a_plan_accepts():
    with TestClient(main.app) as client:
        answer = client.post("/api/places/point", json={"lon": -0.4186, "lat": 39.4283, "radius_km": 3}).json()
        assert answer["status"] == "found"
        assert not [e for e in contracts.errors("plan", {"zone": answer["zone"]}) if e.startswith("zone")]
        assert client.post("/api/places/point", json={"lon": 500, "lat": 0}).status_code == 422
        assert client.post("/api/places/point", json={"lon": 0, "lat": 0, "radius_km": 1000}).status_code == 422


def test_near_orders_the_candidates_and_is_not_passed_to_the_gazetteer(monkeypatch):
    seen = {}

    def fake(text, language):
        seen["args"] = (text, language)
        box = lambda lon, lat: [lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1]  # noqa: E731
        return {"status": "ambiguous", "candidates": [
            {"id": "far", "name": "far", "bbox": box(3.0, 36.7), "source": "t"},
            {"id": "near", "name": "near", "bbox": box(-0.4, 39.1), "source": "t"}]}

    monkeypatch.setattr(places, "resolve", fake)
    with TestClient(main.app) as client:
        got = client.post("/api/places/resolve", json={"text": "Alzira", "language": "es", "near": [-0.38, 39.47]})
        assert [c["id"] for c in got.json()["candidates"]] == ["near", "far"]
        assert seen["args"] == ("Alzira", "es")  # the position never reaches the lookup
        assert client.post("/api/places/resolve", json={"text": "x", "near": [999, 0]}).status_code == 422
