"""Cloud Run (R2): with HUP_SPLIT_APPENDS=1 each new manifest line is a small file in manifest.d/.

Readers take manifest.jsonl and then manifest.d/ in both modes, and the coverage index notices a new file there.
Unset, record() appends to manifest.jsonl as before.
"""
import json
import threading

import pytest

from hup_data import coverage, manifest, paths

BASE = {"source": "nasa-power", "item": "base", "scope": "global", "path": "cache/nasa-power/base.csv", "mb": 0.001,
        "sha256": "0" * 64, "format": "csv", "license": "public",
        "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "on_demand", "url": "https://power.larc.nasa.gov/"},
        "variables": ["air_temperature"], "bbox": [0.0, 0.0, 1.0, 1.0],
        "period": {"start": "2000-01-01", "end": "2000-12-31"}}


@pytest.fixture(autouse=True)
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HUP_SPLIT_APPENDS", raising=False)
    (tmp_path / "manifest.jsonl").write_text(json.dumps(BASE) + "\n", encoding="utf-8")
    return tmp_path


def new_file(data, name: str):
    file = data / "cache" / "nasa-power" / f"{name}.csv"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text("year,value\n2000,1\n", encoding="utf-8")
    return file


def record(data, name: str, bbox=(10.0, 10.0, 11.0, 11.0)):
    return manifest.record(new_file(data, name), source="nasa-power", item=name, scope="global", fmt="csv",
                           url="https://power.larc.nasa.gov/", license="public", variables=["air_temperature"],
                           bbox=list(bbox), period={"start": "2000-01-01", "end": "2000-12-31"})


def test_unset_appends_to_the_base_file(data):
    record(data, "one")
    assert not paths.manifest_parts().exists()
    lines = (data / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["item"] for line in lines] == ["base", "one"]


def test_split_mode_writes_one_file_per_line_and_never_touches_the_base(data, monkeypatch):
    monkeypatch.setenv("HUP_SPLIT_APPENDS", "1")
    before = (data / "manifest.jsonl").read_bytes()
    threads = [threading.Thread(target=record, args=(data, f"t{i}")) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert (data / "manifest.jsonl").read_bytes() == before
    parts = manifest.part_files()
    assert len(parts) == 8 and len({p.name for p in parts}) == 8
    assert not [p for p in paths.manifest_parts().iterdir() if p.suffix != ".jsonl"]  # no temporary left
    for part in parts:
        text = part.read_text(encoding="utf-8")
        assert text.endswith("\n") and len(text.splitlines()) == 1
    items = [line["item"] for line in manifest.lines()]
    assert items[0] == "base" and sorted(items[1:]) == [f"t{i}" for i in range(8)]


def test_coverage_reads_the_parts_and_rebuilds_when_one_appears(data, monkeypatch):
    mapping = {"nasa-power": ["air_temperature"]}
    monkeypatch.setattr(coverage, "_source_variables", lambda: mapping)
    period = {"start": "2000-01-01", "end": "2000-12-31"}
    assert [r["path"] for r in coverage.query("air_temperature", [0.2, 0.2, 0.4, 0.4], period)] == [BASE["path"]]
    assert coverage.query("air_temperature", [10.2, 10.2, 10.4, 10.4], period) == []
    monkeypatch.setenv("HUP_SPLIT_APPENDS", "1")
    line = record(data, "far")
    found = coverage.query("air_temperature", [10.2, 10.2, 10.4, 10.4], period)
    assert [r["path"] for r in found] == [line["path"]]  # noticed without touching manifest.jsonl
    stamp = manifest.stamp()
    record(data, "farther", bbox=(20.0, 20.0, 21.0, 21.0))
    assert manifest.stamp() != stamp
    assert len(coverage.query("air_temperature", [20.2, 20.2, 20.4, 20.4], period)) == 1


def test_parts_are_read_even_when_the_mode_is_off(data, monkeypatch):
    """A data folder written on Cloud Run reads the same on a laptop."""
    monkeypatch.setenv("HUP_SPLIT_APPENDS", "1")
    record(data, "cloud")
    monkeypatch.delenv("HUP_SPLIT_APPENDS")
    assert [line["item"] for line in manifest.lines()] == ["base", "cloud"]
