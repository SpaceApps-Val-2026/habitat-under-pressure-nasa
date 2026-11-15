"""Regression questions across the public assistant and its editorial route."""
import pytest
from fastapi.testclient import TestClient

from hup_api import api, main
from hup_api.assistant import question_article, service
from hup_api.llm.adapter import LLMUnavailable
from hup_data import coverage, places

CITY = {"id": "valencia-city", "name": "Valencia, Comunitat Valenciana, Espanya",
        "bbox": [-0.44, 39.27, -0.27, 39.57]}
PROVINCE = {"id": "valencia-province", "name": "Valencia, Comunitat Valenciana, Espanya (província)",
            "bbox": [-1.53, 38.68, -0.02, 40.22]}
VENEZUELA = {"id": "valencia-ve", "name": "Valencia, Carabobo, Venezuela", "bbox": [-68.1, 10.1, -68.0, 10.3]}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "1")
    monkeypatch.setenv("HUP_LLM_PROVIDER", "none")
    monkeypatch.setenv("HUP_QUESTION_CACHE", "off")
    monkeypatch.setattr(places, "resolve", lambda text, language: {
        "status": "ambiguous", "candidates": [VENEZUELA, PROVINCE, CITY],
    } if service.understand.norm(text) == "valencia" else {"status": "not_found"})
    return TestClient(main.app)


@pytest.mark.parametrize(("text", "language"), [
    ("Cuando volverá a ocurrir una DANA en Valencia?", "es"),
    ("Quan es repetirà una DANA a València?", "ca"),
    ("When could another DANA happen in Valencia?", "en"),
    ("quan tornarà una DANA a valència?", "ca"),
])
def test_local_rainfall_context_without_promising_recurrence(client, monkeypatch, text, language):
    rows = [{"source": "imerg-monthly", "origin": "package",
             "period": {"start": f"{year}-10-01", "end": f"{year}-10-31"}} for year in range(1998, 2025)]
    monkeypatch.setattr(coverage, "query", lambda variable, *_: rows if variable == "precipitation" else [])
    reply = client.post("/api/plans", json={"text": text, "language": language}).json()
    plan = reply["plan"]
    assert plan["request"]["text"] == text
    assert plan["zone"] == CITY
    assert plan["design"] == "event" and plan["variables"] == ["precipitation"]
    assert plan["period"] == {"start": "1998-01-01", "end": "2024-10-31", "months": [10]}
    extreme = next(s for s in plan["steps"] if s["operation"] == "rain_extreme")
    assert extreme["params"]["year"] == 2024 and extreme["params"]["months"] == [10]
    assert plan["answer"]["level"] == "with_limits"
    assert any("cannot date the next" in limit["en"] for limit in plan["answer"]["limits"])


def test_missing_local_series_still_publishes_a_reloadable_article(client):
    prompt = "¿Cuándo volverá a ocurrir una DANA en Valencia?"
    result = client.post("/api/plans", json={"text": prompt, "language": "es"}).json()
    assert result["kind"] == "article" and "plan" not in result
    first = client.get(result["article_url"])
    second = client.get(result["article_url"])
    assert first.status_code == 200 and first.content == second.content
    assert prompt in first.text and "próxima DANA" in first.text and "aemet.es" in first.text
    assert 'class="lead"' in first.text


@pytest.mark.parametrize(("text", "lang", "target"), [
    ("¿Cómo afecta la guerra del Golfo con Irán a Dubai?", "es", "Dubai"),
    ("How is the Gulf war with Iran affecting Dubai?", "en", "Dubai"),
    ("What effect has the war in the Gulf had on Dubai?", "en", "Dubai"),
    ("Quin impacte té a Dubai la guerra amb l'Iran?", "ca", "Dubai"),
])
def test_conflict_without_data_still_has_a_reloadable_article(client, text, lang, target):
    zone = {"id": "dubai", "name": target, "bbox": [55.1, 25.0, 55.4, 25.3]}
    reply = client.post("/api/plans", json={"text": text, "language": lang, "place": zone}).json()
    assert reply["kind"] == "article"
    page = client.get(reply["article_url"])
    assert page.status_code == 200 and f'<p class="place">{target}</p>' in page.text and "GHSL" not in page.text
    assert "Built-up" not in page.text and "superficie construida" not in page.text


def test_clarification_remains_a_page_with_real_choices(client, monkeypatch):
    def unavailable(*_):
        raise LLMUnavailable("test provider unavailable")

    monkeypatch.setattr(service, "llm_complete", unavailable)
    reply = client.post("/api/plans", json={"text": "How warm is it in Valencia?", "language": "en"}).json()
    assert reply["kind"] == "clarification"
    page = client.get(reply["article_url"])
    assert page.status_code == 200 and "How warm is it in Valencia?" in page.text
    assert all(place["name"] in page.text for place in reply["candidates"])
    assert "<form" in page.text
    monkeypatch.setattr(api, "post_analysis", lambda payload: {"analysis_id": payload["plan"]["zone"]["id"]})
    again = client.post(reply["article_url"], data={"candidate": "2"}, follow_redirects=False)
    assert again.status_code == 303 and again.headers["location"] == f"/run/{CITY['id']}"


def test_named_province_and_a_manually_chosen_place_are_not_replaced_by_city(client):
    text = "¿Cuándo volverá a ocurrir una DANA en la provincia de Valencia?"
    reply = client.post("/api/plans", json={"text": text, "language": "es"}).json()
    page = client.get(reply["article_url"])
    assert page.status_code == 200 and PROVINCE["name"] in page.text
    reply = client.post("/api/plans", json={"text": text, "place": VENEZUELA, "language": "es"}).json()
    page = client.get(reply["article_url"])
    assert page.status_code == 200 and VENEZUELA["name"] in page.text
    assert "aemet.es" not in page.text


def test_an_unrelated_question_with_no_model_does_not_default_to_temperature(client, monkeypatch):
    def unavailable(*_):
        raise LLMUnavailable("test provider unavailable")

    monkeypatch.setattr(service, "llm_complete", unavailable)
    reply = client.post("/api/plans", json={"text": "Who won the chess tournament?", "language": "en"}).json()
    assert reply["kind"] == "article" and "plan" not in reply
    assert "chess tournament" in client.get(reply["article_url"]).text


@pytest.mark.parametrize("text", [
    "When will the next flood hit Valencia?",
    "Will there be a heat wave tomorrow in Valencia?",
    "¿Habrá un terremoto mañana en Valencia?",
])
def test_an_individual_hazard_forecast_does_not_turn_into_a_climate_scenario(client, text):
    reply = client.post("/api/plans", json={"text": text}).json()
    assert reply["kind"] == "article" and "plan" not in reply
    page = client.get(reply["article_url"])
    assert page.status_code == 200 and "cannot date the next event" in page.text


def test_question_text_is_escaped_and_unknown_articles_are_404(client):
    reply = client.post("/api/plans", json={
        "text": "When will the next flood hit Dubai? <script>alert(1)</script>", "language": "en",
    }).json()
    page = client.get(reply["article_url"])
    assert "<script>alert(1)" not in page.text and "&lt;script&gt;" in page.text
    assert client.get("/q/" + "0" * 32).status_code == 404


def test_the_article_is_also_served_as_data_for_the_web_app(client):
    """The web app draws the article with its own header and styles; it reads it from here."""
    url = question_article.save({"text": "Why?", "language": "en"}, headline={"en": "H", "es": "T"},
                                lead={"en": "L", "es": "E"}, cannot_say={"en": "C", "es": "N"}, zone="Somewhere")
    article_id = url.rsplit("/", 1)[1]
    data = client.get(f"/api/questions/{article_id}").json()
    assert data["id"] == article_id and data["question"] == "Why?" and data["headline"]["es"] == "T"
    assert client.get("/api/questions/" + "0" * 32).status_code == 404
