"""Gridded files that arrive inside a zip (population, built-up surface) are taken out so they can be read by window.

Input:  manifest lines with format zip that contain .tif or .nc members
Output: <data>/ready/grids/<source>/<zip name>/<member>, exactly as it was in the zip. Nothing is reprojected here:
        the zone is reprojected when reading, never the file.
"""
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from hup_data import paths
from hup_data.prepare.common import Index, manifest_lines

LAYOUT = "1"
GRIDS = (".tif", ".nc")
SIDECARS = (".ovr",)  # overviews travel with their raster


def build(force: bool = False) -> dict:
    """Unpack every gridded member that is new or has changed. Returns what was done."""
    index = Index("grids", LAYOUT)
    done, kept = [], []
    for line in manifest_lines(formats={"zip"}):
        archive_path = paths.data_dir() / line["path"]
        with zipfile.ZipFile(archive_path) as archive:
            members = [info for info in archive.infolist()
                       if not info.is_dir() and info.filename.lower().endswith(GRIDS + SIDECARS)]
            if not any(info.filename.lower().endswith(GRIDS) for info in members):
                continue
            folder = index.folder / line["source"] / Path(line["path"]).stem
            for info in members:
                name = PurePosixPath(info.filename).name  # flat: no path from inside the zip is trusted
                key = f"{line['source']}/{folder.name}/{name}"
                target = folder / name
                if index.fresh(key, line["sha256"], target) and not force:
                    kept.append(key)
                    continue
                folder.mkdir(parents=True, exist_ok=True)
                temporary = target.with_name(target.name + ".tmp")
                with archive.open(info) as source, temporary.open("wb") as out:
                    shutil.copyfileobj(source, out, length=4 * 2**20)
                temporary.replace(target)
                index.put(key, {"source": line["source"], "from": line["path"], "member": info.filename,
                                "input_sha256": line["sha256"], "mb": round(info.file_size / 2**20, 2),
                                "path": target.relative_to(paths.data_dir()).as_posix(),
                                "license": line.get("license"), "version": line.get("version")})
                done.append(key)
                print(f"  {key}", flush=True)
    return {"rebuilt": bool(done), "n_files": len(done) + len(kept), "built": done, "kept": kept, "failed": {}}
