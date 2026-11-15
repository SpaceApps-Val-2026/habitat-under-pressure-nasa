"""Reader of the prepared vector layers: the features of a layer that touch a zone, without opening the whole layer."""
import geopandas as gpd

from hup_data import paths
from hup_data.prepare.common import read_index


def layers(source: str | None = None) -> dict[str, dict]:
    """Prepared layers on this computer: "<source>/<name>" → rows, bbox, columns, licence. Empty if none."""
    found = read_index("vectors")
    return {key: entry for key, entry in found.items() if source is None or entry["source"] == source}


def read_layer(key: str, bbox: list[float] | None = None) -> dict:
    """Features of the layer `key` whose bounding box touches `bbox` (west, south, east, north in EPSG:4326).

    Returns {"data": GeoDataFrame | None, "absence": None | {...}, "entry": index entry | None}.
    A zone outside the layer is `no_coverage`: the layer says nothing there, which is not the same as "nothing".
    """
    entry = read_index("vectors").get(key)
    if entry is None or not (paths.data_dir() / entry["path"]).is_file():
        return {"data": None, "entry": None, "absence": {"reason": "needs_package", "detail": {
            "en": f"The layer {key} has not been prepared on this computer.",
            "es": f"La capa {key} no está preparada en este ordenador."}}}
    if bbox is not None and entry["bbox"] is not None:
        west, south, east, north = entry["bbox"]
        if bbox[2] < west or bbox[0] > east or bbox[3] < south or bbox[1] > north:
            return {"data": None, "entry": entry, "absence": {"reason": "no_coverage", "detail": {
                "en": "This layer does not cover the zone; it says nothing about it.",
                "es": "Esta capa no cubre la zona; no dice nada sobre ella."}}}
    data = gpd.read_parquet(paths.data_dir() / entry["path"], bbox=tuple(bbox) if bbox is not None else None)
    return {"data": data, "entry": entry, "absence": None}
