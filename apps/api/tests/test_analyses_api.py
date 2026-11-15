"""B1: a saved analysis, its results, its event stream and its files, read from <data>/analyses/<id>/."""
import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from hup_api import contracts, main, seed_example
from hup_api.library import folder
from hup_data import paths

EXAMPLE = "sagunt-impact-example"


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data"))
    assert seed_example.seed() == EXAMPLE
    return TestClient(main.app)


def parse_sse(text: str) -> list[dict]:
    """Each SSE message as {"id", "event", "data"} with data decoded from JSON."""
    messages = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        fields = {}
        for line in block.split("\n"):
            if line and not line.startswith(":") and ":" in line:
                key, value = line.split(":", 1)
                fields[key] = fields.get(key, "") + value.removeprefix(" ")
        if "data" in fields:
            messages.append({"id": fields.get("id"), "event": fields.get("event"), "data": json.loads(fields["data"])})
    return messages


def stream(client, url, **headers) -> list[dict]:
    with client.stream("GET", url, headers=headers) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        return parse_sse(response.read().decode("utf-8"))


def test_seed_copies_the_examples(client, tmp_path):
    target = tmp_path / "data" / "analyses" / EXAMPLE
    assert sorted(p.name for p in target.iterdir()) == ["analysis.json", "events.jsonl", "plan.json", "results.json"]


def test_seed_command(tmp_path):
    env = {**os.environ, "HUP_DATA_DIR": str(tmp_path)}
    run = subprocess.run([sys.executable, "-m", "hup_api.seed_example"], capture_output=True, text=True, env=env)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / "analyses" / EXAMPLE / "events.jsonl").is_file()


def test_catalog_route_validates(client):
    response = client.get("/api/catalog")
    assert response.status_code == 200
    assert contracts.errors("catalog", response.json()) == []


def test_get_analysis_validates(client):
    response = client.get(f"/api/analyses/{EXAMPLE}")
    assert response.status_code == 200
    assert response.json()["id"] == EXAMPLE
    assert contracts.errors("analysis", response.json()) == []


def test_editorial_styles_are_written_and_cached_only_when_requested(client, monkeypatch):
    from hup_api.writer import llm

    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "openai")
    monkeypatch.setenv("HUP_LLM_MODEL_TEXT", "style-test")
    calls = []

    def submit(payload, _):
        request = json.loads(payload["request"])
        calls.append(request["style"])
        headline = {"en": "Reviewed evidence for Sagunt", "es": "Evidencia revisada para Sagunt"}
        summary = {"en": "The report examines the area.", "es": "El informe examina la zona."}
        return {"output": dict(headline=headline, lead=summary, meaning=summary, cannot_say=summary)}

    monkeypatch.setattr(llm, "_submit", submit)
    url = f"/api/analyses/{EXAMPLE}"
    plain = client.get(url).json()
    assert not calls
    for style in ("institutional", "scientific"):
        reply = client.get(url, params={"editorial_style": style})
        assert reply.status_code == 200
        saved = reply.json()
        assert contracts.errors("analysis", saved) == []
        editorial = saved["story"]["editorial"]
        assert editorial[style]["headline"]["es"] == "Evidencia revisada para Sagunt"
        assert set(editorial) == set(plain["story"].get("editorial", {})) | {style}
        assert client.get(url, params={"editorial_style": style}).json() == saved
    assert calls == ["institutional", "scientific"]
    assert client.get(url).json() == plain


def test_get_results_validate(client):
    response = client.get(f"/api/analyses/{EXAMPLE}/results")
    assert response.status_code == 200
    results = response.json()
    assert [r["id"] for r in results] == client.get(f"/api/analyses/{EXAMPLE}").json()["results"]
    for result in results:
        assert contracts.errors("result", result) == []


def test_unknown_analysis_is_a_contract_error(client):
    for url in ("/api/analyses/nope", "/api/analyses/nope/results", "/api/analyses/nope/events",
                "/api/analyses/Bad%20Id", "/api/analyses/nope/files/a.png"):
        response = client.get(url)
        assert response.status_code == 404, url
        assert response.json()["error"]["code"] == "not_found"
        assert "en" in response.json()["error"]["text"]


def test_replay_emits_the_22_events_in_order(client):
    start = time.monotonic()
    messages = stream(client, f"/api/analyses/{EXAMPLE}/events?replay=1&speed=100")
    elapsed = time.monotonic() - start
    assert [m["data"]["seq"] for m in messages] == list(range(1, 23))
    assert [m["id"] for m in messages] == [str(i) for i in range(1, 23)]
    assert all(m["event"] == m["data"]["type"] for m in messages)
    assert messages[-1]["event"] == "analysis_finished"
    for m in messages:
        assert contracts.errors("event", m["data"]) == []
    # The example spans 55 s of timestamps: at speed 100 the pauses add up to about 0.55 s.
    assert elapsed > 0.3


def test_reconnect_with_last_event_id(client):
    replayed = stream(client, f"/api/analyses/{EXAMPLE}/events?replay=1&speed=100", **{"Last-Event-ID": "10"})
    assert [m["data"]["seq"] for m in replayed] == list(range(11, 23))
    live = stream(client, f"/api/analyses/{EXAMPLE}/events", **{"Last-Event-ID": "10"})
    assert [m["data"]["seq"] for m in live] == list(range(11, 23))
    assert stream(client, f"/api/analyses/{EXAMPLE}/events", **{"Last-Event-ID": "22"}) == []


def test_live_stream_follows_the_file_while_it_grows(client):
    """The stream reads events.jsonl, not process memory: lines written after it starts still arrive."""
    events = folder.read_events(EXAMPLE)
    target = paths.analysis(EXAMPLE) / "events.jsonl"
    target.write_text("".join(json.dumps(e) + "\n" for e in events[:5]), encoding="utf-8")
    folder.write_analysis(EXAMPLE, {**folder.read_analysis(EXAMPLE), "status": "running"})

    async def scenario():
        received = []

        async def consume():
            async for event in folder.follow_events(EXAMPLE, after=0, poll=0.02):
                received.append(event["seq"])

        async def write_rest():
            await asyncio.sleep(0.1)
            for event in events[5:]:
                folder.append_event(EXAMPLE, event)
                await asyncio.sleep(0.005)

        await asyncio.wait_for(asyncio.gather(consume(), write_rest()), timeout=10)
        return received

    assert asyncio.run(scenario()) == list(range(1, 23))


def test_files_are_served_with_ranges(client):
    layer = paths.analysis(EXAMPLE) / "layers" / "lst.png"
    layer.parent.mkdir()
    layer.write_bytes(bytes(range(256)))
    whole = client.get(f"/api/analyses/{EXAMPLE}/files/layers/lst.png")
    assert whole.status_code == 200 and whole.content == bytes(range(256))
    part = client.get(f"/api/analyses/{EXAMPLE}/files/layers/lst.png", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.content == bytes(range(10, 20))


@pytest.mark.parametrize("path", [
    "../other/analysis.json", "%2e%2e/%2e%2e/secret.txt", "..%2F..%2Fsecret.txt", "..%5C..%5Csecret.txt",
    "layers/../../../secret.txt", "%2Fetc%2Fpasswd", "C:%5CWindows%5Cwin.ini", "C:/Windows/win.ini",
])
def test_files_never_leave_the_analysis_folder(client, tmp_path, path):
    (tmp_path / "data" / "secret.txt").write_text("outside", encoding="utf-8")
    (tmp_path / "data" / "analyses" / "other").mkdir()
    (tmp_path / "data" / "analyses" / "other" / "analysis.json").write_text("{}", encoding="utf-8")
    response = client.get(f"/api/analyses/{EXAMPLE}/files/{path}")
    assert response.status_code == 404
    assert "outside" not in response.text and str(tmp_path) not in response.text
    assert response.json()["error"]["code"] == "not_found"


def test_errors_never_show_a_trace_or_a_disk_path(client, tmp_path):
    (paths.analysis(EXAMPLE) / "analysis.json").write_text("{not json", encoding="utf-8")
    response = TestClient(main.app, raise_server_exceptions=False).get(f"/api/analyses/{EXAMPLE}")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal"
    assert "Traceback" not in response.text and str(tmp_path) not in response.text
