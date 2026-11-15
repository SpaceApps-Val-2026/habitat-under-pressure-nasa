"""Climate cube: the yearly NEX-GDDP-CMIP6 indicators, reorganised so that one place reads in well under a second.

Input:  <data>/packages/nex-gddp-cmip6/<model>/<scenario>/<file variable>_<year>.nc (one small file each)
Output: <data>/ready/climate.zarr, dimensions model, scenario, year, lat, lon, and a .meta.json next to it

Longitudes go from 0-360 in the files to -180..180 in the cube. There is no Antarctica (latitude starts at -59.875).
The cube is built with the models found on this computer and says which; it never fills what is missing.
"""
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import dask.array
import numpy as np
import xarray as xr

from hup_data import fingerprint, paths

SOURCE = "nex-gddp-cmip6"
LAYOUT = "1"  # bump when the shape of the cube changes, so that old cubes are rebuilt
CHUNKS = {"model": 1, "scenario": 1, "lat": 100, "lon": 120}  # every year of a place sits in one chunk


def cube_path() -> Path:
    return paths.ready() / "climate.zarr"


def meta_path() -> Path:
    return paths.ready() / "climate.zarr.meta.json"


def read_meta() -> dict | None:
    """What the cube holds and what it was built from, or None when there is no finished cube."""
    if not (meta_path().is_file() and cube_path().is_dir()):
        return None
    return json.loads(meta_path().read_text(encoding="utf-8"))


def inputs() -> list[dict]:
    """Manifest lines of the source, sorted by path. Only files that are really on disk count."""
    manifest = paths.manifest()
    if not manifest.is_file():
        return []
    lines = (json.loads(text) for text in manifest.read_text(encoding="utf-8").splitlines() if text.strip())
    found = [line for line in lines if line["source"] == SOURCE and (paths.data_dir() / line["path"]).is_file()]
    return sorted(found, key=lambda line: line["path"])


def _parse(line: dict) -> tuple[str, str, int]:
    model, scenario, name = Path(line["path"]).parts[-3:]
    return model, scenario, int(name.rsplit(".", 1)[0].rsplit("_", 1)[1])


def _load(path: Path) -> dict[str, np.ndarray]:
    """One file as arrays on the cube grid: fill values become NaN and longitudes are rolled to -180..180."""
    with xr.open_dataset(path) as ds:
        if not (float(ds.lon.min()) >= 0 and float(ds.lon.max()) > 180):
            raise ValueError(f"{path.name}: longitudes are not 0-360; this reader would shift the map")
        shift = int((ds.lon.values >= 180).argmax())
        return {name: np.roll(ds[name].values.astype("float32"), -shift, axis=-1) for name in ds.data_vars}


def build(force: bool = False) -> dict:
    """Build the cube unless a finished one already comes from the same input files. Returns its metadata."""
    lines = inputs()
    if not lines:
        raise FileNotFoundError(f"No {SOURCE} files in the manifest under {paths.data_dir()}")
    files = [[line["path"], line["sha256"]] for line in lines]
    source_fingerprint = fingerprint.sha({"layout": LAYOUT, "files": files})
    current = read_meta()
    if current and current["input_fingerprint"] == source_fingerprint and not force:
        return {**current, "rebuilt": False}

    parsed = [(*_parse(line), line) for line in lines]
    models = sorted({model for model, _, _, _ in parsed})
    scenarios = sorted({scenario for _, scenario, _, _ in parsed})
    years = list(range(min(year for _, _, year, _ in parsed), max(year for _, _, year, _ in parsed) + 1))
    with xr.open_dataset(paths.data_dir() / lines[0]["path"]) as first:
        lat = first.lat.values
        lon = np.sort(((first.lon.values + 180) % 360) - 180)
    units: dict[str, str] = {}
    for line in {Path(line["path"]).name.rsplit("_", 1)[0]: line for line in lines}.values():
        with xr.open_dataset(paths.data_dir() / line["path"]) as ds:
            units.update({name: ds[name].attrs.get("units", "") for name in ds.data_vars})

    shape = (len(models), len(scenarios), len(years), len(lat), len(lon))
    chunks = (CHUNKS["model"], CHUNKS["scenario"], len(years), CHUNKS["lat"], CHUNKS["lon"])
    dims = ("model", "scenario", "year", "lat", "lon")
    coords = {"model": models, "scenario": scenarios, "year": years, "lat": lat, "lon": lon}
    template = xr.Dataset(
        {name: (dims, dask.array.full(shape, np.nan, dtype="float32", chunks=chunks), {"units": unit})
         for name, unit in sorted(units.items())},
        coords=coords,
        attrs={"title": "Yearly indicators from NEX-GDDP-CMIP6, one cube", "source": SOURCE, "layout": LAYOUT},
    )
    building = cube_path().with_name("climate.zarr.building")
    shutil.rmtree(building, ignore_errors=True)
    building.parent.mkdir(parents=True, exist_ok=True)
    encoding = {name: {"chunks": chunks, "dtype": "float32", "_FillValue": np.nan} for name in units}
    template.to_zarr(building, mode="w", compute=False, zarr_format=2, encoding=encoding, consolidated=False)

    # One model and scenario at a time: each chunk is written exactly once, and memory stays near 1 GB.
    for i, model in enumerate(models):
        for j, scenario in enumerate(scenarios):
            group = [(year, line) for m, s, year, line in parsed if m == model and s == scenario]
            if not group:
                continue
            block = {name: np.full((1, 1, *shape[2:]), np.nan, dtype="float32") for name in units}
            with ThreadPoolExecutor(max_workers=8) as pool:
                loaded = pool.map(lambda item: _load(paths.data_dir() / item[1]["path"]), group)
                for (year, _), arrays in zip(group, loaded, strict=True):
                    for name, values in arrays.items():
                        block[name][0, 0, years.index(year)] = values
            part = xr.Dataset({name: (dims, values) for name, values in block.items()})
            region = {"model": slice(i, i + 1), "scenario": slice(j, j + 1)}
            part.to_zarr(building, mode="r+", region=region, consolidated=False)
            print(f"  {model} {scenario}: {len(group)} files", flush=True)

    span = {s: [year for _, scenario, year, _ in parsed if scenario == s] for s in scenarios}
    meta = {
        "source": SOURCE, "layout": LAYOUT, "input_fingerprint": source_fingerprint, "n_files": len(lines),
        "models": models, "scenarios": scenarios, "years": [years[0], years[-1]],
        "years_by_scenario": {s: [min(span[s]), max(span[s])] for s in scenarios},
        "variables": dict(sorted(units.items())), "resolution_deg": round(float(abs(lat[1] - lat[0])), 6),
        "version": lines[0].get("version"), "license": lines[0].get("license"),
        "built": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # Swap the finished cube in: a reader sees the old cube or the new one, never half of one.
    meta_path().unlink(missing_ok=True)
    shutil.rmtree(cube_path(), ignore_errors=True)
    building.rename(cube_path())
    meta_path().write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**meta, "rebuilt": True}
