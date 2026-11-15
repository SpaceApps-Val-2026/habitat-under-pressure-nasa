"""Mapped water in a zone, from the official flood layers prepared here, measured with masks.zone_mask.

Two variables, because an observed episode and a hazard map are not the same thing:

- `flood_extent`: the share of the mapped part of the zone that a Copernicus EMS delineation (EMSR773, DEL product)
  shows as reached by the episode: flooded area (water still seen in the images) plus flood trace (marks left where
  the water had already gone when the images were taken, 30-31 October for an event of 29 October). One row per
  episode dated within the period, with both classes apart. Outside the area of interest and the image footprint of
  the product nothing is known.
- `flood_hazard_extent`: the share of the zone inside the flood-prone area of a hazard map, one row per map and return
  period: SNCZI (MITECO, return periods of 10, 100 and 500 years) and PATRICOVA (Generalitat Valenciana, 25, 100 and
  500 years, each period with every level whose return period is that one or shorter). Its date is the version of
  the map, not a flood.

Neither is a probability of flooding nor every event. A zone outside a layer is `no_coverage`.
"""
import re

import pandas as pd
import shapely

from hup_data import cancellation
from hup_data.masks import _local, zone_mask
from hup_data.readers import absent, register, vectors
from hup_data.readers.exposure import _project
from hup_data.readers.population import zone_geometry

EMS = "copernicus-ems-emsr773"
EMS_URL = "https://mapping.emergency.copernicus.eu/activations/EMSR773/"
EVENTS = {EMS: "2024-10-29"}  # date of the episode mapped by the activation (Copernicus EMS, EMSR773)
CLASSES = {"flooded_area": "Flooded area", "flood_trace": "Flood trace"}  # notation values
SNCZI = "snczi"
SNCZI_URL = "https://gis.miteco.gob.es/descargas/app/DescargaFichero?f=laminasPB-q100.zip"  # as in the manifest
SNCZI_DATE = "2026-07-27"  # version of the third-cycle files prepared here (manifest: 3Ciclo_20260727)
PATRICOVA = "patricova"
PATRICOVA_URL = ("https://dadesobertes.gva.es/es/dataset/patricova-peligrosidad-por-inundacion-plan-de-accion-"
                 "territorial-de-caracter-sectorial-sobre-pr")
PATRICOVA_DATE = "2026-11-11"  # version of the package (catalog/sources.yaml)
# Return period of each PATRICOVA hazard level, as its own d_pelig field says ("Frecuencia alta (25 años)"...)
PATRICOVA_LEVELS = {1: 25, 2: 100, 3: 25, 4: 100, 5: 500, 6: 500}
COLUMNS = ["t", "v", "source", "kind", "return_years", "hectares", "mapped_hectares", "zone_hectares", "covered_pct"]
OBSERVED_COLUMNS = [*COLUMNS, *(f"{name}_{part}" for name in CLASSES for part in ("hectares", "share"))]


def _hectares(geometry, metric) -> float:
    return float(_project(geometry, metric).area) / 1e4 if not geometry.is_empty else 0.0


def _row(t, source, kind, years, water, mapped, zone_ha, metric) -> dict:
    mapped_ha = _hectares(mapped, metric)
    water_ha = _hectares(water.intersection(mapped), metric)
    return {"t": t, "v": water_ha / mapped_ha, "source": source, "kind": kind, "return_years": years,
            "hectares": water_ha, "mapped_hectares": mapped_ha, "zone_hectares": zone_ha,
            "covered_pct": round(min(100.0, 100 * mapped_ha / zone_ha), 2)}


def _delineation(zone: dict) -> tuple:
    """EMSR773 DEL products inside the zone, all AOIs together: (mapped part, {class: geometry}, sources, failure).

    The mapped part is area of interest ∩ image footprint; outside it nothing is known."""
    index = vectors.layers(EMS)
    floods = sorted(key for key in index if "DEL_PRODUCT" in key and "_observedEventA_" in key)
    mapped, sources = shapely.Polygon(), []
    found = {name: shapely.Polygon() for name in CLASSES}
    for key in floods:
        cancellation.checkpoint()
        prefix = key.split("__")[0] + "__"
        aoi = next((k for k in index if k.startswith(prefix) and "_areaOfInterestA_" in k), None)
        image = next((k for k in index if k.startswith(prefix) and "_imageFootprintA_" in k), None)
        if aoi is None or image is None:
            continue
        parts = [zone_mask(aoi, zone), zone_mask(image, zone),
                 *(zone_mask(key, zone, {"notation": value}) for value in CLASSES.values())]
        failed = next((p["absence"] for p in parts if p["absence"] and p["absence"]["reason"] != "no_coverage"), None)
        if failed:
            return mapped, found, [p["sources"][0] for p in parts], failed
        if any(p["absence"] for p in parts) or parts[0]["geometry"].is_empty:
            continue
        here = parts[0]["geometry"].intersection(parts[1]["geometry"])
        if here.is_empty or here.area == 0:
            continue
        mapped = mapped.union(here)
        for name, part in zip(CLASSES, parts[2:], strict=True):
            found[name] = found[name].union(part["geometry"])
        sources.append(parts[2]["sources"][0])
    return mapped, found, sources, None


def _observed(zone: dict, outline, metric, zone_ha: float) -> tuple[list[dict], list[dict], dict | None]:
    """EMSR773 DEL products: flooded area inside (area of interest ∩ image footprint), all AOIs together."""
    mapped, found, sources, failed = _delineation(zone)
    if failed:
        return [], sources, failed
    if mapped.is_empty:
        return [], sources, None
    row = _row(EVENTS[EMS], EMS, "observed", None, shapely.union_all(list(found.values())), mapped, zone_ha, metric)
    for name, geometry in found.items():
        hectares = _hectares(geometry.intersection(mapped), metric)
        row[f"{name}_hectares"], row[f"{name}_share"] = hectares, hectares / row["mapped_hectares"]
    return [row], sources, None


def _hazard(zone: dict, outline, metric, zone_ha: float) -> tuple[list[dict], list[dict], dict | None]:
    """SNCZI and PATRICOVA. The share is of the whole zone; where a layer covers only part of it, says so."""
    rows, sources = [], []
    snczi = vectors.layers(SNCZI)
    by_period: dict[int, str] = {}
    for key in sorted(snczi, key=lambda k: "_jucar" in k):  # the national file first, the Júcar extract if alone
        found = re.search(r"q(\d+)", key.rsplit("/", 1)[-1], re.IGNORECASE)
        if found:
            by_period.setdefault(int(found.group(1)), key)
    for years, key in sorted(by_period.items()):
        cancellation.checkpoint()
        got = zone_mask(key, zone)
        sources.append(got["sources"][0])
        if got["absence"]:
            if got["absence"]["reason"] != "no_coverage":
                return [], sources, got["absence"]
            continue
        rows.append({**_row(SNCZI_DATE, SNCZI, "hazard", years, got["geometry"], outline, zone_ha, metric),
                     "covered_pct": got["coverage"]["valid_pct"]})
    levels = {n: f"{PATRICOVA}/patricova_peligrosidad_{n}" for n in PATRICOVA_LEVELS}
    present = vectors.layers(PATRICOVA)
    if all(key in present for key in levels.values()):
        masks = {n: zone_mask(key, zone) for n, key in levels.items()}
        sources.append(masks[1]["sources"][0])
        failed = next((m["absence"] for m in masks.values() if m["absence"]), None)
        if failed and failed["reason"] != "no_coverage":
            return [], sources, failed
        if not failed:
            for years in sorted(set(PATRICOVA_LEVELS.values())):
                water = shapely.union_all([masks[n]["geometry"] for n, t in PATRICOVA_LEVELS.items() if t <= years])
                rows.append({**_row(PATRICOVA_DATE, PATRICOVA, "hazard", years, water, outline, zone_ha, metric),
                             "covered_pct": min(m["coverage"]["valid_pct"] for m in masks.values())})
    return rows, sources, None


@register("2", "flood_extent", "flood_hazard_extent")  # 2: flood trace counts
def read(variable: str, zone: dict, period: dict) -> dict:
    observed = variable == "flood_extent"
    fallback = {"source": EMS, "url": EMS_URL} if observed else {"source": SNCZI, "url": SNCZI_URL}
    outline = zone_geometry(zone)
    metric = _local(outline)
    zone_ha = _hectares(outline, metric)
    rows, sources, failed = (_observed if observed else _hazard)(zone, outline, metric, zone_ha)
    sources = sources or [fallback]
    if failed:
        return {**absent(failed["reason"], failed["detail"]["en"], failed["detail"]["es"], sources[0], "package"),
                "sources": sources}
    if observed:
        rows = [row for row in rows if period["start"] <= row["t"] <= period["end"]]
    if not rows:
        en, es = (("No mapped flood episode covers this zone in this period.",
                   "Ningún episodio de inundación cartografiado cubre esta zona en este periodo.") if observed else
                  ("No flood hazard map prepared here covers this zone.",
                   "Ningún mapa de peligrosidad de inundación preparado aquí cubre esta zona."))
        return {**absent("no_coverage", en, es, sources[0], "package"), "sources": sources}
    table = pd.DataFrame(rows, columns=OBSERVED_COLUMNS if observed else COLUMNS)
    valid_pct = float(table["covered_pct"].min())
    limits = [{"en": "Share of the zone inside the mapped polygons, measured in an equal-area projection; the maps are "
                     "vectors, so the figure has no grid of its own.",
               "es": "Fracción de la zona dentro de los polígonos cartografiados, medida en una proyección de área "
                     "equivalente; los mapas son vectoriales y la cifra no tiene rejilla propia."}]
    if observed:
        limits += [
            {"en": "Extent the episode reached according to Copernicus EMS: flooded area (water still seen in the "
                   "images) plus flood trace (marks where the water had gone when the images were taken), with "
                   "each class apart in the table. One mapped episode, not every flood, and not a probability.",
             "es": "Extensión que alcanzó el episodio según Copernicus EMS: área inundada (agua aún visible en las "
                   "imágenes) más huella de inundación (señales donde el agua ya se había retirado al tomar las "
                   "imágenes), con cada clase aparte en la tabla. Un episodio cartografiado, no todas las "
                   "inundaciones ni una probabilidad."},
            {"en": "This emergency map captures little of the built-up area.",
             "es": "Este mapa de emergencia recoge poco casco urbano."}]
    else:
        limits += [
            {"en": "Flood-prone area for a return period, from hazard models: a probability map, not an observed "
                   "flood. The date is the version of the map. SNCZI maps only the river stretches studied; zero "
                   "elsewhere means not mapped, not safe.",
             "es": "Zona inundable para un periodo de retorno, de modelos de peligrosidad: un mapa de probabilidad, no "
                   "una inundación observada. La fecha es la versión del mapa. El SNCZI solo cartografía los tramos "
                   "estudiados; cero fuera de ellos es no cartografiado, no seguro."}]
    if valid_pct < 99.99:
        limits.append({"en": f"A map covers only {valid_pct}% of the zone (column covered_pct); outside it "
                             "nothing is known. Observed shares are of the mapped part; hazard shares, of the zone.",
                       "es": f"Un mapa cubre solo el {valid_pct}% de la zona (columna covered_pct); fuera de él no "
                             "se sabe nada. La fracción observada es de la parte cartografiada; la de peligrosidad, "
                             "de la zona."})
    return {"data": table, "absence": None, "origin": "package", "sources": sources,
            "support": {}, "coverage": {"valid_pct": valid_pct, "n_dates": int(table["t"].nunique())},
            "unit": "1", "limits": limits, "input_fingerprint": None}



def footprint(area: dict, period: dict | None = None) -> dict:
    """The extent a mapped flood episode reached inside `area` (flooded area plus flood trace, as `flood_extent`), as an
    exclusion for `places.regional`: a regional control that keeps flooded land follows the flood, not the climate.

    Only episodes dated on or before the end of `period` (all of them without one). Returns a zone (geometry None
    when nothing is left out) plus "sources", "limits" and "absence", as `places.planned_development`.
    """
    from hup_data import places

    label = {"en": "area reached by the mapped flood", "es": "área que alcanzó la inundación cartografiada"}
    if period and EVENTS[EMS] > period["end"]:
        return places.exclusion(area, "flood", label, None, [], [])
    outline = zone_geometry(area)
    mapped, found, sources, failed = _delineation(area)
    sources = sources or [{"source": EMS, "url": EMS_URL}]
    if failed:
        return places.exclusion(area, "flood", label, None, sources, [], failed)
    if mapped.is_empty:
        return places.exclusion(area, "flood", label, None, sources, [{
            "en": f"The flood of {EVENTS[EMS]} (Copernicus EMS EMSR773) is not mapped in the regional ring: "
                  "nothing was left out for it, and whether it reached the ring is unknown.",
            "es": f"La inundación del {EVENTS[EMS]} (Copernicus EMS EMSR773) no está cartografiada en el anillo "
                  "regional: no se ha quitado nada por ella y no se sabe si llegó al anillo."}])
    metric = _local(outline)
    covered = round(min(100.0, 100 * _hectares(mapped, metric) / _hectares(outline, metric)), 1)
    covered_es = str(covered).replace(".", ",")
    reached = shapely.union_all(list(found.values())).intersection(mapped)
    return places.exclusion(area, "flood", label, reached, sources, [{
        "en": f"Without the area the flood of {EVENTS[EMS]} reached according to Copernicus EMS (EMSR773: flooded area "
              f"plus flood trace). The delineation covers {covered}% of the regional ring; outside it any flooded land "
              "stays in.",
        "es": f"Sin el área que alcanzó la inundación del {EVENTS[EMS]} según Copernicus EMS (EMSR773: área inundada "
              f"más huella de inundación). La delimitación cubre el {covered_es} % del anillo regional; fuera de "
              "ella, la tierra inundada que hubiera sigue dentro."}])
