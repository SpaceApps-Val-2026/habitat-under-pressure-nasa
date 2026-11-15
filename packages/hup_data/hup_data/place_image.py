"""A true-colour satellite picture of any zone on Earth, for the report: fetched when asked, cached, with its credit.

First choice: EOxCloudless 2016, a cloud-free Sentinel-2 mosaic at 10 m from the public EOX WMS. It is the only
EOX vintage under CC BY 4.0 that the EOX FAQ allows from the public endpoint for any use (2018 onwards are
CC BY-NC-SA 4.0 and the public endpoint is not for production). Fallback: NASA GIBS HLS (Harmonized Landsat and
Sentinel-2, 30 m, daily), the most recent date that is mostly clear within the time left. Choice and trials:
.dev/notes/2026-11-15-traspaso-imagen-del-lugar.md.

Nothing is prepared per place. The picture is kept in <data>/cache/place-image/<key>.<jpg|png> with <key>.json
beside it (where it came from, when, credit and licence); the JSON is written last, so it marks a complete entry.
With HUP_OFFLINE=1 only the cache is read. An absence is never cached: the next request tries again.
"""
import hashlib
import json
import math
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import numpy as np
import requests
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile

from hup_data import paths, settings, store

# A web picture has no coordinates; that is expected here (a catch_warnings block is not safe across threads)
warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)

CACHE = "place-image"
VERSION = "1"  # changes when the framing or the choice of source changes, so older pictures are not reused
DEADLINE_S = 15.0  # never block a request longer than this, retries included
PAD = 0.15  # margin on each side, as a share of the zone
MIN_KM = 3.0  # a smaller zone is shown inside a 3 km frame
MAX_SIDE = 2000
KM_PER_DEGREE = 111.32

EOX_URL = "https://tiles.maps.eox.at/wms"
EOX_LAYER = "s2cloudless"  # the 2016 vintage; s2cloudless-2017 also says CC BY in its capabilities, 2018+ NC-SA
GIBS_URL = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
HLS_LAYERS = ("HLS_S30_Nadir_BRDF_Adjusted_Reflectance", "HLS_L30_Nadir_BRDF_Adjusted_Reflectance")
EOX_TRY_S = 6.0  # each of the two EOX tries; what is left goes to the fallback
HLS_DAYS = 30  # how far back the fallback looks for a clear day
HLS_GOOD = 0.9  # share of clear pixels that makes a day good enough: the most recent such day wins
HLS_ENOUGH = 0.6  # share of clear pixels below which no HLS picture is shown
THUMB = 256  # width of the thumbnails that rank the days
FULL_S = 4.0  # time kept for the full-size HLS picture

EOX = {
    "source": {"id": "eox-s2cloudless-2016", "name": {"en": "EOxCloudless 2016 (Sentinel-2 cloudless mosaic)",
                                                      "es": "EOxCloudless 2016 (mosaico de Sentinel-2 sin nubes)"}},
    "period": "2016",
    "native_m": 10,
    "credit": {"en": "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus "
                     "Sentinel data 2016)",
               "es": "EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus "
                     "Sentinel data 2016)"},
    "license": {"en": "CC BY 4.0 (the 2016 vintage). The credit must be visible wherever the image is shown, and "
                      "embedded in any screenshot or export. Later vintages (2018 onwards) are CC BY-NC-SA 4.0 and "
                      "are not used.",
                "es": "CC BY 4.0 (la edición de 2016). El crédito debe verse siempre que se muestre la imagen e ir "
                      "incrustado en cualquier captura o exportación. Las ediciones posteriores (desde 2018) son "
                      "CC BY-NC-SA 4.0 y no se usan."},
}
HLS = {
    "source": {"id": "nasa-gibs-hls", "name": {"en": "NASA HLS true colour (GIBS)",
                                               "es": "NASA HLS en color real (GIBS)"}},
    "native_m": 30,
    "credit": {"en": "Imagery from NASA Global Imagery Browse Services (GIBS), part of NASA EOSDIS: Harmonized "
                     "Landsat and Sentinel-2 ({product}), {day}. Contains modified Copernicus Sentinel data.",
               "es": "Imagen de NASA Global Imagery Browse Services (GIBS), parte de NASA EOSDIS: Harmonized "
                     "Landsat and Sentinel-2 ({product}), {day}. Contiene datos modificados de Copernicus "
                     "Sentinel."},
    "license": {"en": "NASA EOSDIS data are openly shared without restriction; acknowledge NASA GIBS and HLS. "
                      "Do not imply NASA endorsement.",
                "es": "Los datos de NASA EOSDIS se comparten abiertamente y sin restricción; reconocer a NASA GIBS "
                      "y a HLS. No sugerir respaldo de la NASA."},
}


def _get(url: str, params: dict, timeout: float) -> requests.Response:
    """The only network call; tests replace it."""
    return requests.get(url, params=params, timeout=(min(5.0, timeout), timeout))


def frame(bbox: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """The box that is drawn: the zone with a 15 % margin on each side, at least 3 km across, inside the world."""
    west, south, east, north = bbox
    lat = (south + north) / 2
    km_lon = KM_PER_DEGREE * max(math.cos(math.radians(lat)), 0.01)
    half_w = max((east - west) * (0.5 + PAD), MIN_KM / 2 / km_lon)
    half_h = max((north - south) * (0.5 + PAD), MIN_KM / 2 / KM_PER_DEGREE)
    lon = (west + east) / 2
    return (max(lon - half_w, -180.0), max(lat - half_h, -90.0), min(lon + half_w, 180.0), min(lat + half_h, 90.0))


def size(box: tuple[float, float, float, float], width: int) -> tuple[int, int]:
    """Pixels for the box, north up, with the ground aspect at its latitude so that it does not look stretched."""
    west, south, east, north = box
    ground_w = (east - west) * math.cos(math.radians((south + north) / 2))
    ratio = (north - south) / max(ground_w, 1e-9)
    height = round(width * ratio)
    if height > MAX_SIDE:  # a tall box: the height is capped and the width follows
        width, height = max(round(MAX_SIDE / ratio), 1), MAX_SIDE
    return width, max(height, 1)


def _ground_m(box: tuple[float, float, float, float], width: int) -> float:
    west, south, east, north = box
    return (east - west) * KM_PER_DEGREE * 1000 * math.cos(math.radians((south + north) / 2)) / width


def _pixels(data: bytes) -> np.ndarray | None:
    """Bands × rows × columns of a PNG or JPEG answer, or None if it does not decode."""
    try:
        with MemoryFile(data) as memory, memory.open() as image:
            return image.read()
    except Exception:  # noqa: BLE001 — anything GDAL cannot read is simply not a picture
        return None


def clear_share(data: bytes, clouds: bool = True) -> float:
    """Share of pixels that show the ground: not transparent, not black (no data) and, with `clouds`, not bright
    and grey. A rough screen: grey roofs count as cloud too, so it ranks days rather than measuring cloud cover."""
    pixels = _pixels(data)
    if pixels is None or pixels.shape[0] < 3:
        return 0.0
    rgb = pixels[:3].astype(np.int16)
    valid = rgb.max(0) > 8
    if pixels.shape[0] >= 4:
        valid &= pixels[3] > 0
    if clouds:
        valid &= ~((rgb.mean(0) > 150) & ((rgb.max(0) - rgb.min(0)) < 45))
    return float(valid.mean())


def _wms(url: str, layer: str, box, width: int, height: int, fmt: str, deadline: float, day: str | None = None,
         cap: float = 10.0):
    params = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1", "LAYERS": layer, "STYLES": "",
              "SRS": "EPSG:4326", "BBOX": ",".join(f"{v:.6f}" for v in box), "WIDTH": width, "HEIGHT": height,
              "FORMAT": fmt}
    if day:
        params["TIME"] = day
    left = deadline - time.monotonic()
    if left < 1:
        return None
    response = _get(url, params, min(left, cap))
    kind = response.headers.get("content-type", "")
    if response.status_code != 200 or not kind.startswith("image/"):
        return None
    return response.content, kind.split(";")[0]


def _eox(box, width: int, height: int, deadline: float) -> tuple[bytes, str, dict] | None:
    for _ in range(2):  # one retry
        try:
            answer = _wms(EOX_URL, EOX_LAYER, box, width, height, "image/jpeg", deadline, cap=EOX_TRY_S)
        except requests.RequestException:
            continue
        if answer and clear_share(answer[0], clouds=False) > 0.5:  # a cloud-free mosaic: only check it is not empty
            return answer[0], answer[1], {**EOX}
        if answer:  # a picture came back but shows nothing: asking again will not change it
            return None
    return None


def _hls(box, width: int, height: int, deadline: float, today: date) -> tuple[bytes, str, dict] | None:
    """A clear recent day of HLS: the last HLS_DAYS days are ranked on small thumbnails, a few at a time (most days
    are empty for a given place and answer fast); the most recent day clear enough wins, else the clearest one
    above HLS_ENOUGH. Only that day is fetched at full size."""
    thumb = (THUMB, max(round(THUMB * height / width), 1))
    scan_until = deadline - FULL_S  # keep time for the full-size picture

    def rank(item: tuple[str, str]) -> float:
        day, layer = item
        try:
            answer = _wms(GIBS_URL, layer, box, *thumb, "image/png", scan_until, day, cap=3.0)
        except requests.RequestException:
            return 0.0
        return clear_share(answer[0]) if answer else 0.0

    tries = [((today - timedelta(days=back)).isoformat(), layer)
             for back in range(1, HLS_DAYS + 1) for layer in HLS_LAYERS]
    with ThreadPoolExecutor(max_workers=6) as pool:
        shares = list(pool.map(rank, tries))
    ranked = [(share, item) for share, item in zip(shares, tries, strict=True) if share >= HLS_ENOUGH]
    if not ranked:
        return None
    good = [r for r in ranked if r[0] >= HLS_GOOD]
    day, layer = good[0][1] if good else max(ranked, key=lambda r: r[0])[1]
    try:
        answer = _wms(GIBS_URL, layer, box, width, height, "image/png", deadline, day)
    except requests.RequestException:
        return None
    if not answer:
        return None
    product = "S30" if "S30" in layer else "L30"
    meta = {**HLS, "period": day, "credit": {k: v.format(product=product, day=day) for k, v in HLS["credit"].items()}}
    return answer[0], answer[1], meta


def _key(box, width: int) -> str:
    text = "|".join([VERSION, *(f"{v:.5f}" for v in box), str(width)])
    return hashlib.sha256(text.encode()).hexdigest()[:32]


def _absence(reason: str, en: str, es: str) -> dict:
    return {"available": False, "source": EOX["source"], "period": None, "resolution_m": None,
            "credit": EOX["credit"], "license": EOX["license"], "file": None, "media_type": None,
            "absence": {"reason": reason, "detail": {"en": en, "es": es}}}


def image(bbox: tuple[float, float, float, float], width: int = 1200, today: date | None = None) -> dict:
    """The picture of a zone: from the cache, or fetched now and cached. Never raises for a missing picture.

    Returns available, source, period, resolution_m, credit, license, absence, and when available the cached
    `file` and its `media_type`, plus `origin` ("cache" or "remote").
    """
    box = frame(bbox)
    w, h = size(box, width)
    folder = paths.cache() / CACHE
    key = _key(box, w)
    meta_file = folder / f"{key}.json"
    if meta_file.is_file():
        meta = json.loads(store.read_text(meta_file))
        file = folder / meta["file"]
        if file.is_file():
            return {**meta, "file": file, "origin": "cache"}
    if settings.load().offline:
        return _absence("source_unreachable", "Offline, and no picture of this zone has been fetched before.",
                        "Sin red, y no se había descargado antes ninguna imagen de esta zona.")
    deadline = time.monotonic() + DEADLINE_S
    found = _eox(box, w, h, deadline) or _hls(box, w, h, deadline, today or datetime.now(UTC).date())
    if found is None:
        return _absence("source_unreachable", "No clear satellite picture of this zone could be fetched now.",
                        "No se ha podido descargar ahora ninguna imagen de satélite despejada de esta zona.")
    data, media_type, meta = found
    name = f"{key}.{'png' if media_type == 'image/png' else 'jpg'}"
    resolution = max(meta.pop("native_m"), _ground_m(box, w))
    record = {"available": True, **meta, "resolution_m": round(resolution, 1), "file": name,
              "media_type": media_type, "bbox": [round(v, 6) for v in box], "width": w, "height": h,
              "fetched": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "absence": None}
    store.write_atomic(folder / name, data)
    store.write_atomic(meta_file, json.dumps(record, ensure_ascii=False).encode("utf-8"))  # last: the entry is whole
    return {**record, "file": folder / name, "origin": "remote"}
