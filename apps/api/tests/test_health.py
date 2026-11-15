from fastapi.testclient import TestClient

from hup_api import main


def test_health():
    response = TestClient(main.app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_unknown_api_route_is_not_the_web_shell():
    assert TestClient(main.app).get("/api/does-not-exist").status_code == 404


def test_web_shell_and_no_escape_from_dist(monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>shell</title>", encoding="utf-8")
    (dist / "asset.txt").write_text("asset", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    monkeypatch.setattr(main, "WEB_DIST", dist)
    client = TestClient(main.app)
    assert client.get("/asset.txt").text == "asset"
    assert "shell" in client.get("/a/some-analysis/report/scientific").text
    assert "outside" not in client.get("/%2e%2e/secret.txt").text
