"""Resolve a place: a name becomes a zone with its geometry, or a question. Never a default rectangle.

Order: known zones (catalog/zones/*.geojson), the cache (<data>/places/), the gazetteer (Nominatim, OpenStreetMap).
Only the text of the place is ever sent out. With HUP_OFFLINE=1 nothing is sent: known zones and cache only.
"""
import json
import math
import re
import threading
import time
import unicodedata

import numpy as np
import requests
import shapely
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape

from hup_data import fingerprint, paths, settings, store

NOMINATIM = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "habitat-under-pressure/0.1 (open-data analysis tool)"
MIN_SECONDS = 1.1  # the gazetteer allows one request per second
PLACE_CLASSES = {"boundary", "place"}  # towns, districts, regions: not shops or streets that share the name
MAX_VERTICES = 4000
POINT_RADIUS_KM = 2.0  # a place mapped only as a point: a circle the person can adjust, and the zone says so
MAX_POINT_RADIUS_KM = 100.0  # a person's circle on the map: larger than this is a region, not a place
_lock = threading.Lock()
_last_call = [0.0]


def normalise(text: str) -> str:
    """Lower case, no accents, single spaces: the key of the cache and of the known zones."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s,'-]", " ", plain.lower())).strip()


def _zone(zone_id: str, name: str, geometry, source: str) -> dict:
    west, south, east, north = geometry.bounds
    if shapely.get_num_coordinates(geometry) > MAX_VERTICES:  # a country outline: lighter, same shape on the map
        geometry = geometry.simplify(max(east - west, north - south) / 2000, preserve_topology=True)
    plain = json.loads(json.dumps(mapping(geometry)))  # lists, not tuples: equal before and after the cache
    return {"id": zone_id, "name": name, "bbox": [round(v, 6) for v in (west, south, east, north)],
            "geometry": plain, "source": source}


def known_zones() -> dict[str, dict]:
    """Zones that ship with the catalog, keyed by normalised name and by id. Each file names its own source."""
    folder = paths.CATALOG_DIR / "zones"
    found: dict[str, dict] = {}
    for file in sorted(folder.glob("*.geojson")) if folder.is_dir() else []:
        document = json.loads(file.read_text(encoding="utf-8"))
        features = document["features"] if document.get("type") == "FeatureCollection" else [document]
        properties = features[0].get("properties", {})
        geometry = shapely.union_all([shape(feature["geometry"]) for feature in features])
        zone = _zone(file.stem, properties.get("name", file.stem), geometry, properties.get("source", "catalog"))
        names = [zone["name"], file.stem, *properties.get("aliases", [])]
        for key in {normalise(name) for name in names}:
            found[key] = zone
    return found


def _ask_gazetteer(text: str, language: str) -> list[dict]:
    """One polite request: identified client, starts at least a second apart, a timeout and no retry loop.

    The lock only hands out the start times: a slow answer does not hold the other lookups behind it."""
    with _lock:
        start = max(time.monotonic(), _last_call[0] + MIN_SECONDS)
        _last_call[0] = start
    wait = start - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    response = requests.get(
        NOMINATIM, timeout=15, headers={"User-Agent": USER_AGENT, "Accept-Language": language},
        params={"q": text, "format": "jsonv2", "polygon_geojson": 1, "limit": 5, "addressdetails": 0,
                "namedetails": 1},
    )
    response.raise_for_status()
    return response.json()


def _display_name(row: dict, language: str | None) -> str:
    """The gazetteer's name with its first part in the reader's language when the gazetteer knows it.

    The first part of `display_name` is the local name (Dubai comes as its Arabic name whatever language is
    asked for); `namedetails` carries `name:es`, `name:en`... A repeated part after the swap is written once."""
    display = row["display_name"]
    details = row.get("namedetails") or {}
    local = (details.get(f"name:{language}") if language else None) or (
        details.get("name:en") if not display.split(",")[0].isascii() else None)
    if not local:
        return display
    parts = [local.strip(), *(part.strip() for part in display.split(",")[1:])]
    return ", ".join(part for i, part in enumerate(parts) if part and (i == 0 or part != parts[i - 1]))


def _candidates(rows: list[dict], language: str | None = None) -> list[dict]:
    """Places with an outline. Only when none has one, places mapped as a point, as a circle that says what it is."""
    outlined, points = [], []
    for row in rows:
        name = _display_name(row, language)
        geometry = row.get("geojson")
        if row.get("category", row.get("class")) not in PLACE_CLASSES or not geometry:
            continue
        origin = f"OpenStreetMap (Nominatim), {row['osm_type']} {row['osm_id']}"
        zone_id = f"osm-{row['osm_type']}-{row['osm_id']}"
        importance = float(row.get("importance") or 0)
        if geometry["type"] in ("Polygon", "MultiPolygon"):
            zone = _zone(zone_id, name, shape(geometry), origin)
            outlined.append({"zone": zone, "importance": importance})
        elif geometry["type"] == "Point":
            projected, back = _metric(shape(geometry))
            circle = _reproject(projected.buffer(POINT_RADIUS_KM * 1000, quad_segs=16), back)
            note = f"{origin}; mapped as a point: circle of {POINT_RADIUS_KM:g} km, not an official boundary"
            points.append({"zone": _zone(zone_id, name, circle, note), "importance": importance})
    return outlined or points


def _decide(candidates: list[dict]) -> dict:
    """One clear place, several comparable ones, or none. A clear winner is at least 0.1 more important."""
    if not candidates:
        return {"status": "not_found"}
    ranked = sorted(candidates, key=lambda item: -item["importance"])
    if len(ranked) == 1 or ranked[0]["importance"] - ranked[1]["importance"] >= 0.1:
        return {"status": "found", "zone": ranked[0]["zone"]}
    return {"status": "ambiguous", "candidates": [item["zone"] for item in ranked[:5]]}


def _distance_km(zone: dict, near: tuple[float, float]) -> float:
    """Great-circle distance from a point (lon, lat) to the centre of a zone's box."""
    west, south, east, north = zone["bbox"]
    lon1, lat1, lon2, lat2 = map(math.radians, (near[0], near[1], (west + east) / 2, (south + north) / 2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def nearest_first(answer: dict, near: tuple[float, float] | None) -> dict:
    """The same answer with the candidates of an ambiguous place ordered by distance to `near` (lon, lat).

    It never chooses for the person: the place stays ambiguous, the nearest one just comes first, each with its
    distance. The point is used here and nowhere else: it is not sent to the gazetteer, cached or logged.
    """
    if near is None or answer.get("status") != "ambiguous":
        return answer
    ranked = sorted(answer["candidates"], key=lambda zone: _distance_km(zone, near))
    return {**answer, "candidates": [{**zone, "distance_km": round(_distance_km(zone, near), 1)} for zone in ranked]}


def point_zone(lon: float, lat: float, radius_km: float = POINT_RADIUS_KM, name: str | None = None) -> dict:
    """A zone for a point a person chose on a map: a circle that says what it is, never an official boundary."""
    if not (-180 <= lon <= 180 and -90 <= lat <= 90) or not 0 < radius_km <= MAX_POINT_RADIUS_KM:
        raise ValueError("a point zone needs a longitude, a latitude and a radius within range")
    projected, back = _metric(shape({"type": "Point", "coordinates": [lon, lat]}))
    circle = _reproject(projected.buffer(radius_km * 1000, quad_segs=16), back)
    label = name.strip() if name and name.strip() else f"{lat:.4f}, {lon:.4f}"
    zone_id = f"point-{lon:.4f}-{lat:.4f}-{radius_km:g}km".replace(".", "p").replace("--", "-m")
    return _zone(zone_id, label, circle,
                 f"Point chosen on the map: circle of {radius_km:g} km, not an official boundary")


def resolve(text: str, language: str = "en", near: tuple[float, float] | None = None) -> dict:
    """{"status": "found", "zone"} | {"status": "ambiguous", "candidates"} | {"status": "not_found"}.

    Never raises for a place that cannot be found or a gazetteer that cannot be reached. With `near` (lon, lat),
    the candidates of an ambiguous place come nearest first; see `nearest_first`.
    """
    return nearest_first(_resolve(text, language), near)


def _resolve(text: str, language: str) -> dict:
    key = normalise(text or "")
    if not key:
        return {"status": "not_found"}
    known = known_zones().get(key)
    if known:
        return {"status": "found", "zone": known}
    cached = paths.places() / f"{fingerprint.sha({'q': key, 'language': language, 'names': 2})}.json"
    if cached.is_file():
        return json.loads(cached.read_text(encoding="utf-8"))
    if settings.load().offline:
        return {"status": "not_found"}
    try:
        answer = _decide(_candidates(_ask_gazetteer(key, language), language))
    except (requests.RequestException, ValueError):
        return {"status": "not_found"}  # unreachable now: not cached, so the next request asks again
    store.write_atomic(cached, json.dumps(answer, ensure_ascii=False).encode("utf-8"))
    return answer


# ---------------------------------------------------------------- rings and reference for impact designs
def _metric(geometry):
    """A local projection in metres centred on the zone, and the way back. Distances in degrees would be wrong."""
    centre = geometry.centroid
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={centre.y} +lon_0={centre.x} +datum=WGS84 +units=m")
    forward = Transformer.from_crs(4326, local, always_xy=True)
    back = Transformer.from_crs(local, 4326, always_xy=True)
    return _reproject(geometry, forward), back


def _reproject(geometry, transformer):
    return shapely.transform(geometry, lambda xy: np.column_stack(transformer.transform(xy[:, 0], xy[:, 1])))


def rings(zone: dict, km: list[float]) -> list[dict]:
    """Concentric rings around the zone: from its edge to km[0], from km[0] to km[1]... They never overlap."""
    if not zone.get("geometry"):
        raise ValueError("rings need the geometry of the zone, not only its bbox")
    distances = sorted(float(value) for value in km)
    if not distances or distances[0] <= 0:
        raise ValueError("ring distances are positive kilometres")
    projected, back = _metric(shape(zone["geometry"]))
    made, inner, start = [], projected, 0.0
    for distance in distances:
        outer = projected.buffer(distance * 1000, quad_segs=16)
        ring = _reproject(outer.difference(inner), back)
        label = f"{start:g}-{distance:g} km"
        made.append(_zone(f"{zone.get('id', 'zone')}-ring-{start:g}-{distance:g}km".replace(".", "p"),
                          f"{zone['name']}: {label}", ring, f"Ring {label} around {zone.get('source', zone['name'])}"))
        inner, start = outer, distance
    return made


def reference(zone: dict, km: float) -> dict:
    """The surroundings used as control: everything within `km` of the zone, without the zone itself."""
    return rings(zone, [km])[0]


REGIONAL_KM = (3.0, 20.0)  # Guillem, 2026-11-14: inner edge 3 km (end of the measured effect), outer 20 km (Nadia)


def regional(zone: dict, km: tuple[float, float] = REGIONAL_KM, exclude: list[dict] | None = None) -> dict:
    """The regional control of a chain: the ring from km[0] to km[1] around the zone, without permanent water.

    The zone itself (the works) never enters: the ring starts km[0] from its edge. `exclude` removes other areas
    (other works, a flood mask...), each a zone with its geometry. An exclusion made here (`planned_development`,
    `readers.flood.footprint`) also brings its `sources` and `limits`, which join those of the ring, and may be an
    absence: then nothing is removed for it and its reason becomes a limit, never a silent omission. Water is JRC
    Global Surface Water occurrence of landsat.PERMANENT_WATER per cent or more (sea, reservoirs, lagoons), the land
    mask of the Landsat reader, from one tile or a mosaic of them; with no JRC tiles holding the whole ring it is
    `needs_package`, never a ring with the water left in.

    Returns {"zone", "absence", "sources", "limits", "removed": {"water_ha", "excluded_ha"}}.
    """
    import rasterio
    from rasterio.features import shapes
    from rasterio.windows import from_bounds

    from hup_data.readers import landsat

    outer = rings(zone, list(km))[-1]
    result = {"zone": None, "absence": None, "sources": [], "limits": [], "removed": {}}
    tile = landsat.water_tile(outer)
    if tile is None:
        result["absence"] = {"reason": "needs_package", "detail": {
            "en": "No JRC Global Surface Water tile holds the whole regional ring; water cannot be left out.",
            "es": "Ninguna tesela de JRC Global Surface Water contiene todo el anillo regional; no se puede quitar el "
                  "agua."}}
        result["sources"] = [{"source": landsat.WATER_SOURCE, "url": landsat.WATER_URL}]
        return result
    ring = shapely.make_valid(shape(outer["geometry"]))
    with rasterio.open(landsat.water_path(tile)) as raster:
        window = from_bounds(*outer["bbox"], transform=raster.transform).round_offsets().round_lengths()
        occurrence = raster.read(1, window=window, boundless=True, fill_value=255)
        transform = raster.window_transform(window)
    wet = (occurrence >= landsat.PERMANENT_WATER) & (occurrence <= 100)  # 255 is no data
    water = shapely.union_all([shape(g) for g, _ in shapes(wet.astype("uint8"), mask=wet, transform=transform)]) \
        if wet.any() else shapely.Polygon()
    land = ring.difference(water)
    usable = [item for item in exclude or [] if not item.get("absence") and item.get("geometry")]
    removed = [shapely.make_valid(shape(item["geometry"])) for item in usable]
    removed = [geometry for geometry in removed if not geometry.is_empty]
    kept = land.difference(shapely.union_all(removed)) if removed else land
    if kept.is_empty:
        result["absence"] = {"reason": "no_coverage", "detail": {
            "en": "Nothing is left of the regional ring once water and the excluded areas are removed.",
            "es": "No queda nada del anillo regional al quitar el agua y las áreas excluidas."}}
        return result
    centre = ring.centroid
    local = CRS.from_proj4(f"+proj=laea +lat_0={centre.y} +lon_0={centre.x} +datum=WGS84 +units=m")
    forward = Transformer.from_crs(4326, local, always_xy=True)
    area = {name: _reproject(geometry, forward).area / 1e4 for name, geometry in (("ring", ring), ("land", land),
                                                                                 ("kept", kept))}
    label = f"{km[0]:g}-{km[1]:g} km"
    made = _zone(f"{zone.get('id', 'zone')}-regional-{km[0]:g}-{km[1]:g}km".replace(".", "p"),
                 f"{zone['name']}: regional ring {label}", kept,
                 f"Ring {label} around {zone.get('source', zone['name'])}, without permanent water")
    sources = [landsat.water_source(tile)]
    for item in exclude or []:
        sources += [ref for ref in item.get("sources", []) if ref not in sources]
    result.update(zone=made, sources=sources,
                  removed={"water_ha": area["ring"] - area["land"], "excluded_ha": area["land"] - area["kept"]})
    result["limits"] = [
        {"en": f"Regional control: the ring from {label} around the zone, without permanent water (JRC occurrence of "
               f"{landsat.PERMANENT_WATER}% or more){' and the excluded areas' if removed else ''}. The distances "
               "are a working choice, not a result.",
         "es": f"Control regional: el anillo de {label} alrededor de la zona, sin agua permanente (ocurrencia JRC "
               f"del {landsat.PERMANENT_WATER} % o más){' ni las áreas excluidas' if removed else ''}. Las distancias "
               "son una elección de trabajo, no un resultado."}]
    for item in exclude or []:
        notes = [*item.get("limits", []), *([item["absence"]["detail"]] if item.get("absence") else [])]
        result["limits"] += [note for note in notes if note not in result["limits"]]
    return result


PLANNING = "icv-planeamiento"
URBANIZABLE = {"clas_suelo": "SUZ"}  # land classified for new development: residential, industrial and tertiary


def planned_development(area: dict) -> dict:
    """The urbanizable land (SUZ) of the official planning inside `area`, as an exclusion for `regional`.

    Every prepared layer of the planning source (Generalitat Valenciana, ICV) that has the classification column takes
    part; nothing here knows about a place. Urbanizable land is where works may be under way in any year of a period:
    a regional control that keeps it can follow the works instead of the climate. The planning says where land can be
    built, not when it was: land already built inside it leaves too, and works outside it (urban land, roads) stay.

    Returns a zone with its geometry (empty when nothing is urbanizable there) plus "sources", "limits" and
    "absence" (`needs_package` with no layer prepared, `no_coverage` where the layers say nothing).
    """
    from hup_data.masks import zone_mask
    from hup_data.readers import vectors

    parts, sources, absence = [], [], None
    for key in sorted(vectors.layers(PLANNING)):
        found = zone_mask(key, area, URBANIZABLE)
        if found["absence"] and found["absence"]["reason"] == "insufficient_qa":
            continue  # a layer without the classification column: it says nothing about urbanizable land
        if found["absence"]:
            absence = absence or found["absence"]
            continue
        sources.append(found["sources"][0])
        parts.append(found["geometry"])
    label = {"en": "urbanizable land of the official planning", "es": "suelo urbanizable del planeamiento oficial"}
    if not parts:
        absence = absence or {"reason": "needs_package", "detail": {
            "en": "The official planning layer is not prepared on this computer.",
            "es": "La capa del planeamiento oficial no está preparada en este ordenador."}}
        return exclusion(area, "urbanizable", label, None, sources or [{"source": PLANNING}], [], absence)
    return exclusion(area, "urbanizable", label, shapely.union_all(parts), sources, [{
        "en": f"Without the {label['en']} (classification SUZ of the Generalitat Valenciana): works may happen "
              "there in any year. It says where land can be built, not when; works on urban land or roads stay in, "
              "and outside the Comunitat Valenciana the planning says nothing.",
        "es": f"Sin el {label['es']} (clasificación SUZ de la Generalitat Valenciana): ahí puede haber obras "
              "cualquier año. Dice dónde se puede construir, no cuándo; las obras en suelo urbano o en carreteras "
              "siguen dentro, y fuera de la Comunitat Valenciana el planeamiento no dice nada."}])


def exclusion(area: dict, suffix: str, label: dict, geometry, sources: list[dict], limits: list[dict],
              absence: dict | None = None) -> dict:
    """An area to leave out of `regional`: a zone (geometry None when empty) with its sources, limits and absence.

    `label` names it in en/es. With an absence its reason is rewritten as "could not be left out", so that the
    regional control says what it kept in."""
    if absence:
        detail = absence["detail"]
        absence = {"reason": absence["reason"], "detail": {
            "en": f"The {label['en']} could not be left out of the regional control: {detail['en']}",
            "es": f"No se ha podido quitar del control regional el {label['es']}: {detail['es']}"}}
        geometry = None
    empty = geometry is None or geometry.is_empty
    made = {"id": f"{area.get('id', 'zone')}-{suffix}", "name": label["en"], "bbox": area["bbox"], "geometry": None,
            "source": sources[0]["source"] if sources else ""} if empty else \
        _zone(f"{area.get('id', 'zone')}-{suffix}", label["en"], geometry, sources[0]["source"])
    return {**made, "sources": sources, "limits": limits, "absence": absence}
