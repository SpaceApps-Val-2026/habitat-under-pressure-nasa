"""Satellite picture of the place: framing, choice of source, cache, offline and the two routes. No network: the
transport (hup_data.place_image._get) is replaced by a stand-in that answers like the WMS servers."""
import math
from datetime import date

import numpy as np
import pytest
import requests
from fastapi.testclient import TestClient
from rasterio.io import MemoryFile

from hup_api import main
from hup_data import place_image

pytestmark = pytest.mark.filterwarnings("ignore::rasterio.errors.NotGeoreferencedWarning")

BBOX = "-0.3284,39.6351,-0.2116,39.7249"  # about 10 km around Sagunt; nothing in the code knows it


def picture(driver: str, value: int, bands: int = 3, alpha: int = 255) -> bytes:
    data = np.full((bands, 16, 16), value, np.uint8)
    if bands == 4:
        data[3] = alpha
    with MemoryFile() as memory:
        with memory.open(driver=driver, width=16, height=16, count=bands, dtype="uint8") as image:
            image.write(data)
        return memory.read()


class Answer:
    def __init__(self, content: bytes, kind: str, status: int = 200):
        self.content, self.status_code, self.headers = content, status, {"content-type": kind}


GROUND_JPEG = picture("JPEG", 90)
GROUND_PNG = picture("PNG", 90, bands=4)
EMPTY_PNG = picture("PNG", 0, bands=4, alpha=0)
CLOUD_PNG = picture("PNG", 230, bands=4)


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    return tmp_path


def transport(monkeypatch, eox=None, hls=None):
    """eox: an Answer, or an exception to raise. hls: day → Answer for the GIBS days (others are empty)."""
    calls = []

    def get(url, params, timeout):
        calls.append(params)
        assert timeout <= place_image.DEADLINE_S
        if url == place_image.EOX_URL:
            if isinstance(eox, Exception):
                raise eox
            return eox
        return (hls or {}).get(params["TIME"], Answer(EMPTY_PNG, "image/png"))

    monkeypatch.setattr(place_image, "_get", get)
    return calls


def test_the_frame_adds_a_margin_keeps_3_km_and_the_ground_aspect():
    box = place_image.frame((-0.3, 39.6, -0.2, 39.7))
    assert box[2] - box[0] == pytest.approx(0.13) and box[3] - box[1] == pytest.approx(0.13)
    tiny = place_image.frame((10.0, 50.0, 10.0001, 50.0001))
    assert (tiny[3] - tiny[1]) * place_image.KM_PER_DEGREE == pytest.approx(3.0)
    assert (tiny[2] - tiny[0]) * place_image.KM_PER_DEGREE * math.cos(math.radians(50)) == pytest.approx(3.0, rel=1e-4)
    width, height = place_image.size(tiny, 1200)
    assert (width, height) == (1200, 1200)  # a square on the ground is a square picture, at any latitude
    width, height = place_image.size((0, 0, 1, 10), 1200)
    assert height == 2000 and 190 < width < 210  # tall boxes are capped at 2000 px


def test_eox_first_then_cache_with_long_headers(data, monkeypatch):
    calls = transport(monkeypatch, eox=Answer(GROUND_JPEG, "image/jpeg"))
    client = TestClient(main.app)

    info = client.get("/api/places/image/info", params={"bbox": BBOX}).json()
    assert info["available"] is True and info["absence"] is None
    assert info["source"]["id"] == "eox-s2cloudless-2016" and info["period"] == "2016"
    assert "CC BY 4.0" in info["license"]["en"] and set(info["credit"]) == {"en", "es"}
    assert info["resolution_m"] >= 10
    assert info["url"] == f"/api/places/image?bbox={BBOX}&width=1200"
    assert len(calls) == 1 and calls[0]["LAYERS"] == "s2cloudless"

    image = client.get(info["url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    assert "max-age=2592000" in image.headers["cache-control"]
    assert image.content == GROUND_JPEG
    assert len(calls) == 1  # served from the cache
    assert list((data / "cache" / "place-image").glob("*.json"))


def test_eox_retries_once_then_falls_back_to_the_clearest_recent_hls_day(data, monkeypatch):
    days = {"2026-11-10": Answer(CLOUD_PNG, "image/png"), "2026-11-08": Answer(GROUND_PNG, "image/png")}
    calls = transport(monkeypatch, eox=requests.ConnectionError("down"), hls=days)

    result = place_image.image((-0.33, 39.63, -0.21, 39.72), 600, today=date(2026, 11, 15))

    assert sum(c["LAYERS"] == "s2cloudless" for c in calls) == 2
    assert result["available"] and result["source"]["id"] == "nasa-gibs-hls"
    assert result["period"] == "2026-11-08" and result["media_type"] == "image/png"
    assert "2026-11-08" in result["credit"]["en"] and result["resolution_m"] >= 30
    assert calls[-1]["TIME"] == "2026-11-08" and calls[-1]["WIDTH"] == 600  # only the chosen day at full size


def test_no_clear_picture_is_a_404_with_both_languages(data, monkeypatch):
    transport(monkeypatch, eox=Answer(b"<ServiceException/>", "text/xml", 500), hls={})
    client = TestClient(main.app)

    info = client.get("/api/places/image/info", params={"bbox": BBOX}).json()
    assert info["available"] is False and info["absence"]["reason"] == "source_unreachable"

    response = client.get("/api/places/image", params={"bbox": BBOX})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found" and error["text"]["en"] and error["text"]["es"]


def test_offline_reads_only_the_cache(data, monkeypatch):
    transport(monkeypatch, eox=Answer(GROUND_JPEG, "image/jpeg"))
    assert place_image.image((1.0, 1.0, 1.1, 1.1))["available"]

    monkeypatch.setenv("HUP_OFFLINE", "1")
    monkeypatch.setattr(place_image, "_get", lambda *a, **k: pytest.fail("no network when offline"))
    assert place_image.image((1.0, 1.0, 1.1, 1.1))["origin"] == "cache"
    other = place_image.image((2.0, 2.0, 2.1, 2.1))
    assert not other["available"] and "Offline" in other["absence"]["detail"]["en"]


@pytest.mark.parametrize("bbox", ["1,2,3", "3,0,1,1", "0,0,1,95", "a,b,c,d"])
def test_a_wrong_bbox_is_refused(data, bbox):
    response = TestClient(main.app).get("/api/places/image", params={"bbox": bbox})
    assert response.status_code == 422 and set(response.json()["error"]["text"]) == {"en", "es"}


def test_width_is_bounded(data):
    response = TestClient(main.app).get("/api/places/image", params={"bbox": BBOX, "width": 5000})
    assert response.status_code == 422
