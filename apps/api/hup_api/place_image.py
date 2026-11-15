"""A satellite picture of the place a report talks about: GET /api/places/image and GET /api/places/image/info.

Any bounding box on Earth, true colour, north up, with a margin around the zone (hup_data.place_image). The
picture is fetched the first time it is asked for and served from the cache afterwards, with long cache headers.
`info` says where it comes from, its date, resolution, credit and licence, or the reason there is none.
"""
from urllib.parse import quote

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from hup_api.errors import ApiError
from hup_data import place_image

router = APIRouter(prefix="/api")

CACHE = "public, max-age=2592000, immutable"  # the same box and width always give the same picture
_BBOX = r"^-?[0-9.]+,-?[0-9.]+,-?[0-9.]+,-?[0-9.]+$"
_FIELDS = ("available", "source", "period", "resolution_m", "credit", "license", "absence")


def _bbox(text: str) -> tuple[float, float, float, float]:
    try:
        west, south, east, north = (float(v) for v in text.split(","))
    except ValueError:
        west = south = east = north = float("nan")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ApiError(422, "invalid_request",
                       "bbox must be west,south,east,north in degrees, west < east and south < north.",
                       "bbox debe ser oeste,sur,este,norte en grados, con oeste < este y sur < norte.")
    return west, south, east, north


@router.get("/places/image")
async def get_image(bbox: str = Query(..., pattern=_BBOX), width: int = Query(1200, ge=64, le=2000)) -> FileResponse:
    """True-colour picture of the box (JPEG or PNG), north up, in the aspect of the ground at its latitude."""
    result = await run_in_threadpool(place_image.image, _bbox(bbox), width)
    if not result["available"]:
        detail = result["absence"]["detail"]
        raise ApiError(404, "not_found", f"No satellite image is available for this zone. {detail['en']}",
                       f"No hay imagen de satélite disponible para esta zona. {detail['es']}")
    return FileResponse(result["file"], media_type=result["media_type"], headers={"Cache-Control": CACHE})


@router.get("/places/image/info")
async def get_image_info(bbox: str = Query(..., pattern=_BBOX)) -> dict:
    """Source, date, resolution, credit and licence of the picture at the default width, or why there is none.

    Asking for it fetches and caches the picture, so the image URL it returns is then served from the cache.
    """
    result = await run_in_threadpool(place_image.image, _bbox(bbox), 1200)
    return {**{key: result.get(key) for key in _FIELDS},
            "url": f"/api/places/image?bbox={quote(bbox, safe=',.-')}&width=1200"}
