"""Vector layers: every GeoJSON and every shapefile inside a zip becomes a GeoParquet that can be read by window.

Input:  manifest lines with format geojson, and .shp members of lines with format zip
Output: <data>/ready/vectors/<source>/<name>.parquet in EPSG:4326, sorted along a space-filling curve and with a
        bounding box per row, so that reading one zone does not open the whole layer.
The rule is the same for every source: nothing here knows about a particular place or layer.
"""
import zipfile
from pathlib import Path, PurePosixPath

import geopandas as gpd
import shapely

from hup_data import paths
from hup_data.prepare.common import Index, manifest_lines

LAYOUT = "1"
ROW_GROUP = 2000  # rows per group at most: a window read skips the groups that do not touch the zone
GROUP_BYTES = 8e6  # geometry per group, as 2D WKB: layers of large polygons get fewer rows per group


def _layers(line: dict) -> list[tuple[str, str]]:
    """(name, path that geopandas can open) for every vector layer of a manifest line."""
    file = paths.data_dir() / line["path"]
    stem = Path(line["path"]).stem
    if line["format"] == "geojson":
        return [(stem, str(file))]
    with zipfile.ZipFile(file) as archive:
        members = sorted(name for name in archive.namelist() if name.lower().endswith(".shp"))
    return [(f"{stem}__{PurePosixPath(member).stem}", f"zip://{file.as_posix()}!{member}") for member in members]


def _convert(source: str, target: Path) -> dict:
    layer = gpd.read_file(source)
    epsg = layer.crs.to_epsg() if layer.crs else None
    original_crs = f"EPSG:{epsg}" if epsg else (layer.crs.name if layer.crs else None)
    if layer.crs is None:
        raise ValueError("the layer declares no coordinate system; it cannot be placed on the map")
    layer = layer[layer.geometry.notna() & ~layer.geometry.is_empty].to_crs(4326)
    if len(layer):
        layer = layer.iloc[layer.hilbert_distance().argsort()].reset_index(drop=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".parquet.tmp")
    coordinates = int(shapely.get_num_coordinates(layer.geometry.values).sum())
    rows = max(1, min(ROW_GROUP, int(len(layer) * GROUP_BYTES / max(16 * coordinates, 1))))
    layer.to_parquet(temporary, write_covering_bbox=True, row_group_size=rows, compression="zstd")
    temporary.replace(target)
    return {"rows": int(len(layer)), "bbox": [round(float(v), 6) for v in layer.total_bounds] if len(layer) else None,
            "columns": [name for name in layer.columns if name != layer.geometry.name], "original_crs": original_crs,
            "geometry_types": sorted(set(layer.geom_type))}


def build(force: bool = False) -> dict:
    """Convert every vector layer in the manifest that is new or has changed. Returns what was done."""
    index = Index("vectors", LAYOUT)
    done, kept, failed = [], [], {}
    for line in manifest_lines(formats={"geojson", "zip"}):
        for name, source in _layers(line):
            key = f"{line['source']}/{name}"
            target = index.folder / line["source"] / f"{name}.parquet"
            if index.fresh(key, line["sha256"], target) and not force:
                kept.append(key)
                continue
            try:
                entry = _convert(source, target)
            except Exception as error:  # one unreadable layer must not stop the others; it is reported
                failed[key] = f"{type(error).__name__}: {error}"[:300]
                print(f"  FAILED {key}: {failed[key]}", flush=True)
                continue
            index.put(key, {**entry, "source": line["source"], "from": line["path"], "input_sha256": line["sha256"],
                            "path": target.relative_to(paths.data_dir()).as_posix(), "license": line.get("license")})
            done.append(key)
            print(f"  {key}: {entry['rows']} rows", flush=True)
    return {"rebuilt": bool(done), "n_files": len(done) + len(kept), "built": done, "kept": kept, "failed": failed}
