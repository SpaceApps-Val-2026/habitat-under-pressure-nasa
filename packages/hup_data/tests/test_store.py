"""Package A1: fingerprints, derived store and coverage index."""
import copy
import json
import time

import pytest

from hup_data import coverage, fingerprint, paths, store

EXAMPLES = paths.CONTRACTS_DIR / "examples"
FP = "ab" + "0" * 62


def example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def data(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- fingerprints
def test_canonical_has_no_spaces_and_keeps_accents():
    assert fingerprint.canonical({"b": "Júcar", "a": [1, 2]}) == '{"a":[1,2],"b":"Júcar"}'


def test_plan_fingerprint_reproduces_the_contract_example():
    analysis = example("analysis.json")
    assert fingerprint.plan_fingerprint(analysis["plan"]) == analysis["plan_fingerprint"]


def test_step_fingerprint_reproduces_the_contract_examples():
    plan, results, catalog = example("plan.json"), example("results.json"), example("catalog.json")
    steps = {step["id"]: step for step in plan["steps"]}
    versions = {source["id"]: source["version"] for source in catalog["sources"] if "version" in source}

    def of(step_id):
        """A step reads the versions of its sources and the fingerprints of the steps it depends on."""
        step = steps[step_id]
        inputs = {source: versions[source] for source in step.get("sources", []) if source in versions}
        inputs.update({dep: of(dep) for dep in step["depends_on"]})
        return fingerprint.step_fingerprint(
            step["operation"], step["version"], step["params"], fingerprint.zone_key(plan["zone"]), inputs,
            plan["period"])

    with_data = [result for result in results if "fingerprint" in result]
    assert len(with_data) == 2
    for result in with_data:
        assert of(result["step"]) == result["fingerprint"]
    assert of("s4") != of("s5")


def test_the_same_reading_over_another_period_is_another_step():
    """Found with real data: an episode reused the rain series that another analysis had read for one year."""
    args = ("read_series", "1.0", {"variable": "precipitation"}, "some-zone", {"imerg-monthly": "07"})
    year = fingerprint.step_fingerprint(*args, {"start": "2024-01-01", "end": "2024-12-31"})
    record = fingerprint.step_fingerprint(*args, {"start": "1998-01-01", "end": "2024-10-31", "months": [10]})
    again = fingerprint.step_fingerprint(*args, {"end": "2024-12-31", "start": "2024-01-01"})
    assert year != record and year == again


def test_plan_fingerprint_ignores_wording_and_changes_with_params():
    plan = example("plan.json")
    reworded = copy.deepcopy(plan)
    reworded["id"] = "another-id"
    reworded["request"] = {"text": "What changed near the Sagunt gigafactory?", "language": "en"}
    reworded["answer"] = {"level": "complete"}
    for step in reworded["steps"]:
        step.pop("data", None)
    assert fingerprint.plan_fingerprint(reworded) == fingerprint.plan_fingerprint(plan)

    changed = copy.deepcopy(plan)
    changed["steps"][3]["params"]["reference_km"] = 5
    assert fingerprint.plan_fingerprint(changed) != fingerprint.plan_fingerprint(plan)
    moved = copy.deepcopy(plan)
    moved["period"]["start"] = "2014-06-01"
    assert fingerprint.plan_fingerprint(moved) != fingerprint.plan_fingerprint(plan)


def test_zone_without_id_is_identified_by_its_shape():
    a = {"name": "Somewhere", "bbox": [0, 0, 1, 1]}
    b = {"name": "Another name, same place", "bbox": [0, 0, 1, 1]}
    c = {"name": "Somewhere", "bbox": [0, 0, 1, 2]}
    assert fingerprint.zone_key(a) == fingerprint.zone_key(b) != fingerprint.zone_key(c)


# ---------------------------------------------------------------- derived store
def test_put_then_get_returns_the_same(data):
    result = {"value": 1.5, "unit": "°C", "limits": [{"en": "Surface, not air"}]}
    assert store.get_derived(FP) is None
    store.put_derived(FP, result, {".parquet": b"rows"})
    assert store.get_derived(FP) == result
    assert store.derived_path(FP, ".parquet").read_bytes() == b"rows"
    assert store.derived_path(FP, ".json").parent == data / "derived" / "ab"


def test_an_interrupted_write_leaves_nothing_behind(data, monkeypatch):
    def fail(source, target):
        raise OSError("disk unplugged")

    monkeypatch.setattr(store.os, "replace", fail)
    with pytest.raises(OSError):
        store.put_derived(FP, {"value": 1})
    monkeypatch.undo()
    monkeypatch.setenv("HUP_DATA_DIR", str(data))
    assert store.get_derived(FP) is None
    assert list((data / "derived" / "ab").iterdir()) == []


def test_a_file_being_read_delays_the_write_but_does_not_lose_it(data, monkeypatch):
    """Windows refuses to rename over a file that someone is reading; the write waits and tries again."""
    real, calls = store.os.replace, []

    def busy_twice(source, target):
        calls.append(target)
        if len(calls) <= 2:
            raise PermissionError("the file is open in another thread")
        real(source, target)

    monkeypatch.setattr(store.os, "replace", busy_twice)
    store.put_derived(FP, {"value": 2})
    assert store.get_derived(FP) == {"value": 2} and len(calls) == 3


def test_a_file_being_replaced_delays_the_read_but_does_not_fail_it(data, monkeypatch):
    """The mirror case: reading at the instant the file is renamed over is refused once or twice on Windows."""
    store.put_derived(FP, {"value": 3})
    real, calls = store.Path.read_text, []

    def busy_twice(self, *args, **kwargs):
        calls.append(self)
        if len(calls) <= 2:
            raise PermissionError("the file is being replaced")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(store.Path, "read_text", busy_twice)
    assert store.get_derived(FP) == {"value": 3} and len(calls) == 3


def test_only_fingerprints_and_suffixes_make_paths(data):
    for bad in ("../../etc/passwd", "AB" + "0" * 62, "abc", ""):
        with pytest.raises(ValueError):
            store.derived_path(bad, ".json")
    for bad in ("json", "/../x.json", ".JSON", ""):
        with pytest.raises(ValueError):
            store.derived_path(FP, bad)
    with pytest.raises(ValueError):
        store.put_derived(FP, {}, {".json": b"clash"})


# ---------------------------------------------------------------- coverage index
def manifest_line(source, path, **extra):
    return {"source": source, "item": path, "scope": "global", "path": path, "mb": 1, "sha256": "0" * 64,
            "format": "hdf5", "ingest": {"date": "2026-11-12T00:00:00Z", "origin": "predownload", "url": "https://x"},
            "license": "x", **extra}


def write_manifest(data, lines):
    (data / "manifest.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")


def test_coverage_finds_by_variable_place_and_period(data):
    write_manifest(data, [
        manifest_line("rain", "packages/rain/2001.h5", period={"start": "2001-01-01", "end": "2001-01-31"}),
        manifest_line("rain", "packages/rain/2030.h5", period={"start": "2030-01-01", "end": "2030-01-31"}),
        manifest_line("tile", "packages/tile/a.h5", bbox=[-10, 30, 0, 40]),
        manifest_line("scene", "cache/scene/z/a.tif", variables=["greenness"], tier="ready", bbox=[-1, 39, 0, 40]),
        manifest_line("unknown", "packages/unknown/a.zip"),
    ])
    assert coverage.rebuild({"rain": ["precipitation"], "tile": ["night_lights"]}) == 5
    sagunt, period = [-0.35, 39.60, -0.15, 39.75], {"start": "2000-01-01", "end": "2024-12-31"}

    rain = coverage.query("precipitation", sagunt, period)
    assert [row["path"] for row in rain] == ["packages/rain/2001.h5"]
    assert rain[0]["origin"] == "package" and rain[0]["tier"] == "raw"
    assert rain[0]["period"] == {"start": "2001-01-01", "end": "2001-01-31"}

    assert len(coverage.query("night_lights", sagunt, period)) == 1
    assert coverage.query("night_lights", [10, 30, 11, 31], period) == []
    scene = coverage.query("greenness", sagunt, period)
    assert scene[0]["origin"] == "cache" and scene[0]["tier"] == "ready" and scene[0]["period"] is None
    assert coverage.query("not_in_any_file", sagunt, period) == []


def test_coverage_a_case_package_without_bbox_covers_nothing(data):
    """Without a bbox only a `global` scope means the whole world; a case package of unknown extent covers nothing,
    so a far place gets the missing-package limit instead of a package it does not have."""
    write_manifest(data, [
        manifest_line("ems", "packages/ems/aoi.geojson", scope="cases", variables=["flood_extent"]),
        manifest_line("grid", "packages/grid/a.nc", variables=["precipitation"]),
    ])
    coverage.rebuild({})
    nairobi, always = [36.65, -1.45, 37.10, -1.15], {"start": "1900-01-01", "end": "2100-01-01"}
    assert coverage.query("flood_extent", nairobi, always) == []
    assert len(coverage.query("precipitation", nairobi, always)) == 1


def test_coverage_follows_the_manifest_when_it_changes(data):
    write_manifest(data, [manifest_line("rain", "packages/rain/a.h5", variables=["precipitation"])])
    everywhere, always = [-180, -90, 180, 90], {"start": "1900-01-01", "end": "2100-01-01"}
    assert len(coverage.query("precipitation", everywhere, always)) == 1
    time.sleep(0.01)
    write_manifest(data, [manifest_line("rain", f"packages/rain/{n}.h5", variables=["precipitation"]) for n in "ab"])
    assert len(coverage.query("precipitation", everywhere, always)) == 2


def test_coverage_without_a_manifest_is_empty_not_an_error(data):
    assert coverage.query("precipitation", [0, 0, 1, 1], {"start": "2000-01-01", "end": "2001-01-01"}) == []


@pytest.mark.data
def test_real_manifest_answers_in_milliseconds():
    """The real manifest: monthly rain files for a known point, in under 100 ms once the index is built."""
    if not paths.manifest().is_file():
        pytest.skip("no manifest under HUP_DATA_DIR")
    rows = coverage.rebuild({"imerg-monthly": ["precipitation"]})
    timings = []
    for _ in range(3):  # the best of three: a busy computer must not fail a test about the design
        start = time.perf_counter()
        found = coverage.query("precipitation", [-0.28, 39.67, -0.26, 39.69],
                               {"start": "2000-01-01", "end": "2024-12-31"})
        timings.append(time.perf_counter() - start)
    elapsed = min(timings)
    if not found:
        pytest.skip("IMERG is not in this manifest")
    assert rows > 1000
    assert {row["source"] for row in found} == {"imerg-monthly"}
    assert len(found) == 300 and all(row["origin"] == "package" for row in found)
    assert elapsed < 0.1, f"{elapsed * 1000:.0f} ms"
