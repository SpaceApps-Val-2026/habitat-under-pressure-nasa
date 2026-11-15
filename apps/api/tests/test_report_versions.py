"""Re-runs of one question are versions of one report: the list shows the newest finished one, nothing is deleted."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import main
from hup_api.library import folder, index, plan_cache
from hup_data import paths

EXAMPLES = paths.CONTRACTS_DIR / "examples"
CATALOG = json.loads((EXAMPLES / "catalog.json").read_text(encoding="utf-8"))
EXAMPLE = json.loads((EXAMPLES / "analysis.json").read_text(encoding="utf-8"))
QUESTION = "¿Cuándo volverá a ocurrir una DANA en Valencia?"


def save(analysis_id, created, status="finished", text=QUESTION, zone="valencia", design="trend",
         fingerprint=None) -> dict:
    a = copy.deepcopy(EXAMPLE)
    a.pop("featured", None)
    a.update(id=analysis_id, created=created, status=status)
    a["plan"]["request"]["text"] = text
    a["plan"]["zone"]["id"] = zone
    a["plan"]["design"] = design
    if fingerprint is not None:
        a["plan_fingerprint"] = fingerprint
    folder.directory(analysis_id).mkdir(parents=True, exist_ok=True)
    folder.write_analysis(analysis_id, a)
    return a


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(catalog_module, "load", lambda *args, **kwargs: copy.deepcopy(CATALOG))
    return tmp_path


@pytest.fixture
def client():
    save("dana-1", "2026-11-15T08:00:00Z")
    save("dana-2", "2026-11-15T09:00:00Z", text="  ¿cuando VOLVERA a ocurrir una dana en valencia ")
    save("dana-3", "2026-11-15T10:00:00Z")
    return TestClient(main.app)


def listing(client, **params) -> list[dict]:
    response = client.get("/api/analyses", params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def by_id(items) -> dict[str, dict]:
    return {i["id"]: i for i in items}


def test_three_runs_of_one_question_show_once_the_newest(client):
    [item] = listing(client)
    assert item["id"] == "dana-3" and item["versions"] == 3


def test_history_shows_every_run(client):
    items = listing(client, history=1)
    assert [i["id"] for i in items] == ["dana-3", "dana-2", "dana-1"]
    assert all(i["versions"] == 3 for i in items)


def test_a_single_analysis_has_no_versions_field(client):
    save("other", "2026-11-15T11:00:00Z", text="¿Cuánto llovió en Alzira?")
    assert "versions" not in by_id(listing(client))["other"]


def test_other_zone_or_design_is_another_report(client):
    save("dana-alzira", "2026-11-15T07:00:00Z", zone="alzira")
    save("dana-scan", "2026-11-15T07:00:00Z", design="scan")
    assert set(by_id(listing(client))) == {"dana-3", "dana-alzira", "dana-scan"}


def test_a_running_rerun_does_not_hide_the_finished_report(client):
    save("dana-4", "2026-11-15T12:00:00Z", status="running")
    items = by_id(listing(client))
    assert set(items) == {"dana-4", "dana-3"}
    assert items["dana-3"]["versions"] == 3 and "versions" not in items["dana-4"]


def test_a_failed_rerun_changes_nothing_and_shows_only_when_asked(client):
    save("dana-4", "2026-11-15T12:00:00Z", status="failed")
    assert [i["id"] for i in listing(client)] == ["dana-3"]
    assert [i["id"] for i in listing(client, status="failed")] == ["dana-4"]


def test_a_cancelled_rerun_changes_nothing(client):
    save("dana-4", "2026-11-15T12:00:00Z", status="cancelled")
    [item] = listing(client)
    assert item["id"] == "dana-3" and item["versions"] == 3


def test_a_newer_finished_rerun_takes_over(client):
    listing(client)
    index.record(save("dana-4", "2026-11-15T12:00:00Z"))
    [item] = listing(client)
    assert item["id"] == "dana-4" and item["versions"] == 4


def test_filters_never_bring_back_an_older_version(client):
    old = folder.read_analysis("dana-1")
    old["title"] = {"en": "Torrential rain in Valencia"}
    folder.write_analysis("dana-1", old)
    index.rebuild()
    assert listing(client, design="trend", q="torrential") == []  # only an older version says it
    assert [i["id"] for i in listing(client, design="trend", q="torrential", history=1)] == ["dana-1"]


def test_versions_lists_the_rest_of_the_group(client):
    save("dana-4", "2026-11-15T12:00:00Z", status="failed")
    save("elsewhere", "2026-11-15T12:00:00Z", zone="alzira")
    response = client.get("/api/analyses/dana-2/versions")
    assert response.status_code == 200
    assert response.json() == [
        {"id": "dana-4", "created": "2026-11-15T12:00:00Z", "status": "failed"},
        {"id": "dana-3", "created": "2026-11-15T10:00:00Z", "status": "finished"},
        {"id": "dana-1", "created": "2026-11-15T08:00:00Z", "status": "finished"},
    ]
    assert client.get("/api/analyses/elsewhere/versions").json() == []
    assert client.get("/api/analyses/missing/versions").status_code == 404


def test_versions_of_a_cancelled_run_is_its_group(client):
    save("dana-4", "2026-11-15T12:00:00Z", status="cancelled")
    assert [v["id"] for v in client.get("/api/analyses/dana-4/versions").json()] == ["dana-3", "dana-2", "dana-1"]


def test_without_question_text_the_plan_fingerprint_groups(client):
    save("manual-1", "2026-11-14T08:00:00Z", text="", fingerprint="a" * 64)
    save("manual-2", "2026-11-14T09:00:00Z", text="", fingerprint="a" * 64, zone="alzira")
    save("manual-3", "2026-11-14T09:00:00Z", text="", fingerprint="b" * 64)
    items = by_id(listing(client))
    assert set(items) == {"dana-3", "manual-2", "manual-3"}
    assert items["manual-2"]["versions"] == 2 and "versions" not in items["manual-3"]


def test_featured_analyses_are_never_hidden(client):
    a = save("dana-featured", "2026-11-14T08:00:00Z")
    a["featured"] = True
    folder.write_analysis("dana-featured", a)
    index.record(a)
    items = listing(client)
    assert [i["id"] for i in items] == ["dana-featured", "dana-3"]
    assert "versions" not in items[0] and items[1]["versions"] == 3


def test_the_existing_analysis_shortcut_points_at_the_newest_finished_run(client):
    plan = EXAMPLE["plan"]
    fp = plan_cache.fingerprint(plan, CATALOG)
    for analysis_id in ("dana-1", "dana-2", "dana-3"):
        a = folder.read_analysis(analysis_id)
        a["plan"], a["plan_fingerprint"] = copy.deepcopy(plan), fp
        folder.write_analysis(analysis_id, a)
    save("dana-4", "2026-11-15T12:00:00Z", status="running", fingerprint=fp)
    plan_cache.rebuild()
    assert plan_cache.existing(fp) == "dana-3"
    response = client.post("/api/plans/validate", json={"plan": plan})
    assert response.status_code == 200 and response.json()["existing_analysis"] == "dana-3"
    finished = save("dana-5", "2026-11-15T13:00:00Z", fingerprint=fp)
    finished["plan"] = copy.deepcopy(plan)
    folder.write_analysis("dana-5", finished)
    plan_cache.record(finished, CATALOG)
    assert plan_cache.existing(fp) == "dana-5"
