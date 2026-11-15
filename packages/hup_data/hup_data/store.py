"""Derived tier of the store: results and their files, named by fingerprint.

Layout: <data>/derived/<fp[:2]>/<fp>.json and, next to it, <fp><suffix> for each file (for example .parquet, .tif).
Every write is atomic: a temporary file in the same folder, then a rename.
"""
import json
import os
import re
import tempfile
import time
from pathlib import Path

from hup_data import paths

_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_SUFFIX = re.compile(r"^\.[a-z0-9]+(\.[a-z0-9]+)?$")


def _check(fp: str) -> str:
    if not isinstance(fp, str) or not _FINGERPRINT.fullmatch(fp):
        raise ValueError("a fingerprint is 64 lowercase hexadecimal characters")
    return fp


def derived_path(fp: str, suffix: str) -> Path:
    """Where the file of a fingerprint lives. `suffix` starts with a dot: ".json", ".parquet", ".tif"."""
    if not _SUFFIX.fullmatch(suffix):
        raise ValueError(f"not a file suffix: {suffix!r}")
    return paths.derived() / _check(fp)[:2] / f"{fp}{suffix}"


def _replace(temporary: str, path: Path) -> None:
    """Rename over the target. On Windows this is refused while another thread or process is reading the
    target, which happens whenever the web polls a file the engine is rewriting: wait a moment and try again."""
    for attempt in range(40):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)


def read_text(path: Path) -> str:
    """Read a file that another thread may be replacing right now. On Windows the file cannot be opened for
    an instant while it is renamed over: wait a moment and try again, instead of failing the request."""
    for attempt in range(40):
        try:
            return path.read_text(encoding="utf-8")
        except PermissionError:
            if attempt == 39:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


def write_atomic(path: Path, data: bytes) -> None:
    """Write the whole file or nothing: a reader never sees it half written. Shared by every package."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        _replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def get_derived(fp: str) -> dict | None:
    """The saved result of that fingerprint, or None when it has not been computed."""
    path = derived_path(fp, ".json")
    if not path.is_file():
        return None
    return json.loads(read_text(path))


def put_derived(fp: str, result: dict, files: dict[str, bytes] | None = None) -> None:
    """Save a result and its files. The .json goes last: if it exists, everything it points to exists."""
    for suffix, data in (files or {}).items():
        if suffix == ".json":
            raise ValueError(".json is reserved for the result itself")
        write_atomic(derived_path(fp, suffix), data)
    text = json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False)
    write_atomic(derived_path(fp, ".json"), text.encode("utf-8"))
