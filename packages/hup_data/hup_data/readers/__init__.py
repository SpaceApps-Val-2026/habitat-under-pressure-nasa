"""Readers: one module per source, and one door for all of them.

`read_series(variable, zone, period)` picks the reader of the variable and goes down the read ladder:
a saved series with the same fingerprint (origin "derived"), then whatever the reader finds in the cache, in the
downloaded packages or on the network. Every reader returns the common shape of docs/paquetes/interfaces.md,
section 4, and never raises for missing data: it returns an absence with its reason and its source.
"""
import inspect
from collections.abc import Callable

import pandas as pd

from hup_data import cancellation, fingerprint, store

READERS: dict[str, tuple[str, Callable[[str, dict, dict], dict]]] = {}


def register(version: str, *variables: str):
    """Declare that a function reads these variables. `version` changes when what it returns changes."""
    def wrap(function):
        for variable in variables:
            READERS[variable] = (version, function)
        return function
    return wrap


def absent(reason: str, en: str, es: str, source: dict, origin: str = "cache") -> dict:
    """The common shape when there is no data: a reason, in two languages, and the source that was tried."""
    return {"data": None, "absence": {"reason": reason, "detail": {"en": en, "es": es}}, "origin": origin,
            "sources": [source], "support": {}, "coverage": {}, "unit": None, "input_fingerprint": None}


def _load_all() -> None:
    """Import every reader module once, so that each one registers its variables."""
    from hup_data.readers import (  # noqa: F401
        black_marble,
        built_up,
        climate,
        flood,
        ghcn_daily,
        grace,
        hot_cells,
        imerg,
        imerg_daily,
        imperviousness,
        landsat,
        nasa_power,
        piezometry,
        population,
        sentinel1,
        sst,
    )


def variables() -> list[str]:
    _load_all()
    return sorted(READERS)


NO_LAND_MASK = {"applied": False, "detail": {
    "en": "Leaving out permanent water applies only to Landsat zone medians; this series is read with water in.",
    "es": "Quitar el agua permanente solo se aplica a las medianas de Landsat por zona; esta serie se lee con el "
          "agua dentro."}}


def _masks(reader) -> bool:
    """Whether a reader can leave out permanent water (it takes `land_only`)."""
    return "land_only" in inspect.signature(reader).parameters


def read_series(variable: str, zone: dict, period: dict, *, grid_resolution_m=None, grid_min_years=1,
                land_only=False, tier1_only=True, percentile=None, base_period=None) -> dict:
    """A series of one variable for a zone and a period, from the first rung of the ladder that has it.

    `land_only` reaches the readers that take it (Landsat) and enters the derived key only when set, so a masked and
    an unmasked read never share a saved series. Elsewhere it is ignored and the result says so in `land_mask`.
    `tier1_only` (Landsat: only Tier 1 scenes; the default since decision 1 of A10) reaches the readers that take it
    either way, and enters the derived key only when True. `percentile` and `base_period` (a threshold set on a base
    period, hot cells) reach only the readers that take them and enter the key only when given; a reader's
    `threshold` is saved with the series and returned on a derived read."""
    cancellation.checkpoint()
    tier1 = {"tier1_only": bool(tier1_only)}
    if grid_resolution_m is not None:
        from hup_data.readers import landsat_grid

        got = landsat_grid.read(variable, zone, period, grid_resolution_m, grid_min_years, **tier1)
        return {**got, "land_mask": NO_LAND_MASK} if land_only else got
    _load_all()
    if variable not in READERS:
        return absent("not_computed", f"No reader is available for the variable {variable}.",
                      f"No hay lector disponible para la variable {variable}.",
                      {"source": "none", "url": "https://github.com/SpaceApps-Val-2026/Habitat-Under-Pressure"})
    version, reader = READERS[variable]
    masked = bool(land_only) and _masks(reader)
    accepted = inspect.signature(reader).parameters
    tier1 = tier1 if "tier1_only" in accepted else {}
    given = {name: value for name, value in (("percentile", percentile), ("base_period", base_period))
             if value is not None and name in accepted}
    fields = {"read_series": variable, "reader": version, "zone": fingerprint.zone_key(zone),
              "bbox": zone["bbox"], "period": period}
    if masked:
        fields["land_only"] = True
    if tier1.get("tier1_only"):
        fields["tier1_only"] = True
    fields.update(given)
    key = fingerprint.sha(fields)
    saved = store.get_derived(key)
    if saved is not None:
        got = {**saved["meta"], "data": pd.DataFrame(saved["rows"]), "absence": None, "origin": "derived",
               "input_fingerprint": key}
        return {**got, "land_mask": NO_LAND_MASK} if land_only and not masked else got
    options = {"land_only": True} if masked else {}
    cancellation.checkpoint()
    result = reader(variable, zone, period, **options, **tier1, **given)
    mask = result.get("land_mask")
    # Without a water tile the masked read is the unmasked one: not saved, so it is redone when the tile arrives.
    if result["absence"] is None and (not masked or (mask or {}).get("applied")):
        # Only data is saved: an absence may be filled later, when a package arrives or the source answers.
        meta = {name: result[name] for name in ("sources", "support", "coverage", "unit")}
        if result.get("limits"):  # what a reader says about its own reading travels with the saved series
            meta["limits"] = result["limits"]
        if result.get("threshold"):
            meta["threshold"] = result["threshold"]
        if masked:
            meta["land_mask"] = mask
        store.put_derived(key, {"meta": meta, "rows": result["data"].to_dict(orient="list")})
        result["input_fingerprint"] = key
    if land_only and not masked:
        result = {**result, "land_mask": NO_LAND_MASK}
    return result


def reference_zone(zone: dict, km: float) -> dict:
    """The surroundings of a zone used as control: everything within `km` of it, without the zone itself."""
    from hup_data import places

    return places.reference(zone, km)


def read_exposure(params: dict, zone: dict, period: dict) -> dict:
    from hup_data.readers.exposure import read_exposure as read

    return read(params, zone, period)


def ring_zones(zone: dict, km: list[float]) -> list[dict]:
    """Concentric rings around a zone, from its edge outwards. They never overlap."""
    from hup_data import places

    return places.rings(zone, km)
