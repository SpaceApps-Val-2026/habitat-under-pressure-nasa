"""Shared by the preparation tasks: the manifest lines to work on, and an index that makes each task repeatable.

Every task keeps `<ready>/<task>/index.json`: one entry per prepared artefact, with the sha256 of the raw file it
came from. An artefact is rebuilt only when that file, or the layout version of the task, has changed.
The prepared tier is not written to manifest.jsonl: that file lists what is shared between computers, file by
file, and the prepared tier is rebuilt on each computer from it.
"""
import json
from pathlib import Path

from hup_data import paths, store


def manifest_lines(formats: set[str] | None = None, sources: set[str] | None = None) -> list[dict]:
    """Manifest lines whose file is really on disk, sorted by path."""
    manifest = paths.manifest()
    if not manifest.is_file():
        return []
    found = []
    for text in manifest.read_text(encoding="utf-8").splitlines():
        if not text.strip():
            continue
        line = json.loads(text)
        if formats and line["format"] not in formats:
            continue
        if sources and line["source"] not in sources:
            continue
        if (paths.data_dir() / line["path"]).is_file():
            found.append(line)
    return sorted(found, key=lambda line: line["path"])


class Index:
    """The index of one task. `fresh` says whether an artefact is still valid; `save` writes atomically."""

    def __init__(self, task: str, layout: str):
        self.folder = paths.ready() / task
        self.file = self.folder / "index.json"
        self.layout = layout
        loaded = json.loads(self.file.read_text(encoding="utf-8")) if self.file.is_file() else {}
        self.entries: dict[str, dict] = loaded.get("entries", {}) if loaded.get("layout") == layout else {}

    def fresh(self, name: str, sha256: str, *outputs: Path) -> bool:
        entry = self.entries.get(name)
        return bool(entry and entry["input_sha256"] == sha256 and all(path.exists() for path in outputs))

    def put(self, name: str, entry: dict) -> None:
        self.entries[name] = entry
        self.save()

    def save(self) -> None:
        content = {"layout": self.layout, "entries": self.entries}
        text = json.dumps(content, ensure_ascii=False, indent=1, sort_keys=True)
        store.write_atomic(self.file, (text + "\n").encode("utf-8"))


def read_index(task: str) -> dict[str, dict]:
    """Entries of a finished task, for readers. Empty when the task has not run on this computer."""
    file = paths.ready() / task / "index.json"
    return json.loads(file.read_text(encoding="utf-8")).get("entries", {}) if file.is_file() else {}
