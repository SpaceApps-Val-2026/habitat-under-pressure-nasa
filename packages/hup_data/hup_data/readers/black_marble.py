"""Annual observed nighttime radiance in the downloaded VIIRS Black Marble tiles."""
import re

import h5py
import numpy as np
import pandas as pd
from shapely import contains_xy, make_valid
from shapely.geometry import shape

from hup_data import cancellation, paths
from hup_data.prepare.common import manifest_lines
from hup_data.readers import absent, register

SOURCE = "black-marble"
VARIABLE = "nighttime_radiance"
REF = {"source": SOURCE, "version": "002", "product": "VNP46A4",
       "url": "https://doi.org/10.5067/VIIRS/VNP46A4.002"}
FIELD = "NearNadir_Composite_Snow_Free"
GRID = "HDFEOS/GRIDS/VIIRS_Grid_DNB_2d/Data Fields"


def _window(file, bbox):
    meta = file["HDFEOS INFORMATION"]["StructMetadata.0"][()]
    if not isinstance(meta, bytes):
        meta = meta.item()
    coordinates = re.findall(rb"(?:UpperLeftPointMtrs|LowerRightMtrs)=\(([^)]+)\)", meta)
    if len(coordinates) != 2 or b"HE5_GCTP_GEO" not in meta:
        raise ValueError("expected the geographic VNP46A4 grid")
    (tile_west, tile_north), (tile_east, tile_south) = [
        [float(value) / 1e6 for value in pair.split(b",")] for pair in coordinates
    ]
    west, south, east, north = bbox
    if west < tile_west or east > tile_east or south < tile_south or north > tile_north:
        return None
    height, width = file[GRID][FIELD].shape
    lon = tile_west + (np.arange(width) + 0.5) * ((tile_east - tile_west) / width)
    lat = tile_north - (np.arange(height) + 0.5) * ((tile_north - tile_south) / height)
    x = np.flatnonzero((west <= lon) & (lon <= east))
    y = np.flatnonzero((south <= lat) & (lat <= north))
    if not len(x) or not len(y):
        return None
    return (slice(y[0], y[-1] + 1), slice(x[0], x[-1] + 1)), lon[x], lat[y]


@register("1", VARIABLE)
def read(variable: str, zone: dict, period: dict) -> dict:
    first, last = int(period["start"][:4]), int(period["end"][:4])
    if period.get("months") or (first == last and (period["start"][5:] != "01-01" or period["end"][5:] != "12-31")):
        return absent("no_coverage", "This Black Marble product describes complete years.",
                      "Este producto de Black Marble describe años completos.", REF, origin="package")
    geometry = make_valid(shape(zone["geometry"])) if zone.get("geometry") else None
    rows = {}
    held = False
    for entry in manifest_lines(sources={SOURCE}):
        cancellation.checkpoint()
        path = paths.data_dir() / entry["path"]
        match = re.match(r"VNP46A4\.A(\d{4})001\.", path.name)
        if not match or not first <= (year := int(match[1])) <= last:
            continue
        if not (period["start"] <= f"{year}-01-01" and period["end"] >= f"{year}-12-31"):
            continue
        with h5py.File(path, "r") as file:
            chosen = _window(file, zone["bbox"])
            if chosen is None:
                continue
            held = True
            window, lon, lat = chosen
            grid = file[GRID]
            values = grid[FIELD][window]
            good = (grid[FIELD + "_Quality"][window] == 0) & np.isfinite(values) & (values >= 0)
            inside = contains_xy(geometry, *np.meshgrid(lon, lat)) if geometry is not None else np.ones_like(good)
            good &= inside
            if good.any():
                weights = np.broadcast_to(np.cos(np.deg2rad(lat))[:, None], values.shape)
                rows[year] = (float(np.average(values[good], weights=weights[good])),
                              float(100 * weights[good].sum() / weights[inside].sum()))
    if not rows:
        if held:
            return absent("insufficient_qa", "No good-quality VIIRS pixels cover this area and period.",
                          "No hay píxeles VIIRS de buena calidad para esta zona y periodo.", REF, origin="package")
        return absent("needs_package", "A downloaded annual Black Marble tile must cover the whole area.",
                      "Hace falta una tesela anual descargada de Black Marble que cubra toda la zona.",
                      REF, origin="package")
    data = pd.DataFrame([(str(y), v) for y, (v, _) in sorted(rows.items())], columns=["t", "v"])
    return {"data": data, "absence": None, "origin": "package", "sources": [{**REF, "n_items": len(rows)}],
            "support": {"resolution_m": 500, "since": 2012, "revisit_days": 365},
            "coverage": {"n_dates": len(rows), "valid_pct": min(share for _, share in rows.values())},
            "unit": "nW/(cm²·sr)", "input_fingerprint": None,
            "limits": [{"en": "Annual nighttime radiance under clear, snow-free conditions. It is light seen by "
                              "the satellite. Electricity use and the types of activity need their own evidence.",
                        "es": "Radiancia nocturna anual con cielo despejado y sin nieve. Es luz observada por el "
                              "satélite. El consumo eléctrico y el tipo de actividad requieren fuentes propias."}]}
