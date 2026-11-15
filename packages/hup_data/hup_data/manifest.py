"""The manifest: one valid line per file brought by on-demand ingest. The coverage index (coverage.py) reads it.

On a disk the lines are appended to manifest.jsonl. On an object store (a mounted bucket) a file cannot be
appended to without uploading it whole, and one object takes about one write per second; there, with
HUP_SPLIT_APPENDS=1, each new line is written as a small file of its own in manifest.d/ (atomic, unique name).
Readers always read manifest.jsonl and then manifest.d/*.jsonl in name order, whatever the setting: a folder
written in split mode reads the same on a laptop.
"""
import hashlib
import json
import secrets
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from hup_data import paths, settings, store

_lock = threading.Lock()


def part_files() -> list[Path]:
    """The one-line files in manifest.d/, in name order (names start with the time they were written)."""
    folder = paths.manifest_parts()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix == ".jsonl" and p.is_file())


def texts() -> Iterator[str]:
    """Every non-empty line of the manifest: manifest.jsonl first, then the files of manifest.d/."""
    base = paths.manifest()
    if base.is_file():
        with base.open(encoding="utf-8") as lines:
            for text in lines:
                if text.strip():
                    yield text
    for part in part_files():
        for text in part.read_text(encoding="utf-8").splitlines():
            if text.strip():
                yield text


def lines() -> Iterator[dict]:
    """Every manifest line, parsed, in the order of texts()."""
    for text in texts():
        yield json.loads(text)


def stamp() -> tuple:
    """Changes whenever the manifest does: the base file's mtime and the names in manifest.d/ (new lines are new
    files, so a count and the last name are enough, and an mtime on a mounted bucket is not trusted for it)."""
    base = paths.manifest()
    parts = part_files()
    return (str(base), base.stat().st_mtime_ns if base.is_file() else None,
            len(parts), parts[-1].name if parts else None)


def _write_part(text: str) -> None:
    name = f"{datetime.now(UTC):%Y%m%dT%H%M%S%f}-{secrets.token_hex(4)}.jsonl"
    folder = paths.manifest_parts()
    folder.mkdir(parents=True, exist_ok=True)
    store.write_atomic(folder / name, text.encode("utf-8"))


@cache
def _validator() -> Draft202012Validator:
    schemas = [json.loads(file.read_text(encoding="utf-8")) for file in paths.CONTRACTS_DIR.glob("*.schema.json")]
    registry = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas)
    manifest = next(schema for schema in schemas if schema["$id"].endswith("manifest.schema.json"))
    return Draft202012Validator(manifest, registry=registry, format_checker=FormatChecker())


def record(file: Path, *, source: str, item: str, scope: str, fmt: str, url: str, license: str, **optional) -> dict:
    """Add the manifest line of a file that on-demand ingest has just saved under the data folder.

    `optional` takes the other fields of the contract: product, version, bbox, period, variables, tier, quality.
    The line is validated before it is written: a wrong line would break the next sync between computers.
    """
    data = file.read_bytes()
    line = {
        "source": source, "item": item, "scope": scope,
        "path": file.relative_to(paths.data_dir()).as_posix(), "mb": round(len(data) / 2**20, 4),
        "sha256": hashlib.sha256(data).hexdigest(), "format": fmt,
        "ingest": {"date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "origin": "on_demand", "url": url},
        "license": license, **{key: value for key, value in optional.items() if value is not None},
    }
    errors = [error.message for error in _validator().iter_errors(line)]
    if errors:
        raise ValueError(f"manifest line for {line['path']} is not valid: {errors[0][:200]}")
    text = json.dumps(line, ensure_ascii=False) + "\n"
    if settings.load().split_appends:
        _write_part(text)
        return line
    with _lock, paths.manifest().open("a", encoding="utf-8", newline="\n") as manifest:
        manifest.write(text)
    return line
