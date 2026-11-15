"""Every route against malformed input: wrong types, broken JSON, huge bodies and path tricks.

Each bad request gets a 4xx with the error shape of interfaces.md ({"error": {"code", "text": {"en"}}}), never a 500,
never a trace and never a disk path.
"""
import json

import pytest
from fastapi.testclient import TestClient

from hup_api import main, seed_example
from hup_data import paths, places

EXAMPLE = "sagunt-impact-example"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    monkeypatch.setattr(places, "resolve", lambda text, language="en": {"status": "not_found"})
    seed_example.seed()
    return TestClient(main.app, raise_server_exceptions=False)


def is_error(response, status=None, code=None):
    assert response.status_code < 500, response.text
    if status is not None:
        assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"} and body["error"]["code"] and body["error"]["text"]["en"]
    if code is not None:
        assert body["error"]["code"] == code
    assert "Traceback" not in response.text and str(paths.data_dir()) not in response.text
    return body


@pytest.mark.parametrize(("method", "url"), [
    ("post", "/api/plans"), ("post", "/api/plans/validate"), ("post", "/api/analyses"),
    ("post", "/api/places/resolve"),
])
@pytest.mark.parametrize("raw", [b"", b"not json", b"{\"plan\": ", b"[]", b"42", b"null", b"\xff\xfe"])
def test_broken_bodies_are_invalid_requests(client, method, url, raw):
    response = getattr(client, method)(url, content=raw, headers={"Content-Type": "application/json"})
    is_error(response, 422, "invalid_request")


@pytest.mark.parametrize("body", [
    {"plan": "x"}, {"plan": []}, {"plan": None}, {"plan": 1}, {"other": {}},
])
def test_plan_bodies_of_the_wrong_type(client, body):
    for url in ("/api/plans/validate", "/api/analyses"):
        is_error(client.post(url, json=body), 422)


@pytest.mark.parametrize("plan", [
    {}, {"steps": "x"}, {"steps": [1, 2]}, {"steps": [{"params": "x", "operation": 3}]}, {"period": {"start": 1}},
    {"period": "2020"}, {"zone": {"bbox": "x"}}, {"steps": [{"id": "s1", "depends_on": ["s1"]}]},
])
def test_a_broken_plan_is_reported_not_crashed(client, plan):
    response = client.post("/api/plans/validate", json={"plan": plan})
    assert response.status_code == 200, response.text
    assert response.json()["valid"] is False and response.json()["errors"]
    is_error(client.post("/api/analyses", json={"plan": plan}), 422, "invalid_plan")


@pytest.mark.parametrize("body", [
    {"text": "a", "design": "trend", "place": "x", "variables": ["ndvi"], "period": {"start": [], "end": {}}},
    {"text": "a", "design": "trend", "place": {"bbox": "x"}, "variables": ["ndvi"]},
    {"text": "a", "design": "trend", "place": "x", "variables": ["ndvi"] * 51},
    {"text": "a", "design": "impact", "place": "x", "variables": ["ndvi"], "before": [1, 2, 3]},
    {"text": "a", "level": "expert"},
    {"text": "a", "language": "EN"},
])
def test_plan_requests_with_wrong_types(client, body):
    response = client.post("/api/plans", json=body)
    if response.status_code == 200:  # a well formed request whose content is incomplete gets a question
        assert response.json()["kind"] == "clarification"
    else:
        is_error(response, 422)


def test_a_huge_body_is_refused_before_it_is_read(client):
    big = json.dumps({"text": "x" * (main.MAX_BODY + 10)})
    is_error(client.post("/api/plans", content=big, headers={"Content-Type": "application/json"}), 413, "too_large")


def test_a_huge_body_without_a_length_is_refused_too(client):
    def chunks():
        for _ in range(main.MAX_BODY // 65536 + 2):
            yield b"x" * 65536

    response = client.post("/api/plans/validate", content=chunks(), headers={"Content-Type": "application/json"})
    is_error(response, 413, "too_large")


@pytest.mark.parametrize("analysis_id", [
    "..", "...", "%2e%2e", "UPPER", "a b", "a%00b", "-leading", "trailing.", "x" * 300, "%5c..%5cplan.json",
])
def test_analysis_ids_that_are_not_ids_are_not_found(client, analysis_id):
    for suffix in ("", "/results", "/events", "/files/plan.json"):
        response = client.get(f"/api/analyses/{analysis_id}{suffix}")
        assert response.status_code in (404, 422), (analysis_id, suffix, response.status_code)
        assert "Traceback" not in response.text


@pytest.mark.parametrize("path", [
    "../analysis.json", "..%2fanalysis.json", "%2e%2e/%2e%2e/manifest.jsonl", "/etc/passwd", "C:/Windows/win.ini",
    "..\\..\\plan.json", "a/../../plan.json", "plan.json%00.png", "",
])
def test_file_paths_cannot_leave_the_analysis_folder(client, path):
    response = client.get(f"/api/analyses/{EXAMPLE}/files/{path}")
    assert response.status_code == 404, (path, response.status_code)


@pytest.mark.parametrize("query", [
    "limit=abc", "limit=0", "limit=100000", "design=made_up", "status=gone", "featured=maybe", "q=" + "x" * 600,
])
def test_library_queries_of_the_wrong_type(client, query):
    is_error(client.get(f"/api/analyses?{query}"), 422)


@pytest.mark.parametrize("query", ["replay=1&speed=0", "replay=1&speed=-1", "replay=maybe", "speed=abc"])
def test_event_stream_queries_of_the_wrong_type(client, query):
    is_error(client.get(f"/api/analyses/{EXAMPLE}/events?{query}"), 422)


def test_a_last_event_id_that_is_not_a_number_starts_from_the_beginning(client):
    response = client.get(f"/api/analyses/{EXAMPLE}/events?replay=1&speed=10000",
                          headers={"Last-Event-ID": "'; DROP TABLE"})
    assert response.status_code == 200 and "analysis_finished" in response.text


@pytest.mark.parametrize("query", ["", "variable=", "variable=x&language=xx-YY", "variable=" + "x" * 300])
def test_legislation_queries_of_the_wrong_type(client, query):
    is_error(client.get(f"/api/legislation?{query}"), 422)
