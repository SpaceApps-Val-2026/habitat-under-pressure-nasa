"""B9: the plain-language cards of catalog/legislation.yaml joined with the Cellar list. A fake Cellar, no network."""
import copy

import pytest
import requests
import yaml
from fastapi.testclient import TestClient

from hup_api import catalog as catalog_module
from hup_api import contracts, legislation, main
from hup_data import paths

REAL = catalog_module.load()
YAML = yaml.safe_load((paths.CATALOG_DIR / "legislation.yaml").read_text(encoding="utf-8"))


def sparql(*rows):
    """A Cellar answer with these (celex, title, date) rows."""
    return {"results": {"bindings": [{"celex": {"value": c}, "title": {"value": t}, "date": {"value": d}}
                                     for c, t, d in rows]}}


def catalog_with(cards=None):
    catalog = copy.deepcopy(REAL)
    if cards is not None:
        catalog["legislation"] = cards
    tagged = {**copy.deepcopy(catalog["variables"][0]), "id": "test_tagged", "eurovoc": ["1440"]}
    bare = {k: v for k, v in copy.deepcopy(catalog["variables"][0]).items() if k != "eurovoc"}
    catalog["variables"] += [tagged, {**bare, "id": "test_untagged"}]
    return catalog


@pytest.fixture
def cellar(monkeypatch, tmp_path):
    """Set the rows the fake Cellar answers with; it records every query."""
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    monkeypatch.setattr(catalog_module, "load", lambda *a, **k: catalog_with())
    state = {"rows": [], "queries": []}

    def ask(query):
        state["queries"].append(query)
        return sparql(*state["rows"])

    monkeypatch.setattr(legislation, "_ask", ask)
    return state


@pytest.fixture
def client():
    return TestClient(main.app)


def get(client, language="es", variable="test_tagged"):
    response = client.get("/api/legislation", params={"variable": variable, "language": language})
    assert response.status_code == 200
    return response.json()


def by_celex(answer):
    return {item["celex"]: item for item in answer["items"]}


def test_the_query_keeps_only_parliament_and_council_directives_and_regulations(client, cellar):
    get(client)
    (query,) = cellar["queries"]
    assert "cdm:work_created_by_agent ?author" in query
    assert "corporate-body/EP>" in query and "corporate-body/CONSIL>" in query
    assert "resource-type/DIR>" in query and "resource-type/REG>" in query
    assert not any(t in query for t in ("_IMPL>", "_DEL>"))


def test_an_act_with_a_card_is_explained_in_the_language_asked(client, cellar):
    cellar["rows"] = [("32007L0060", "Directiva 2007/60/CE ...", "2007-10-23")]
    answer = get(client, "es")
    (item,) = answer["items"]
    card = next(a for a in YAML["acts"] if a["celex"] == "32007L0060")
    assert item["visibility"] == "card"
    explained = item["explained"]
    assert explained["celex"] == "32007L0060" and explained["reviewed_by"] == card["reviewed_by"]
    assert explained["short_name"] == card["short_name"]["es"] and explained["plain"] == card["plain"]["es"]
    assert explained["in_spain"] == card["in_spain"]["es"]
    first = explained["citizen_options"][0]
    assert first["action"] == card["citizen_options"][0]["action"]["es"]
    assert first["who"] == "anyone" and first["who_label"] == YAML["who_labels"]["anyone"]["es"]
    assert first["legal_basis"] == card["citizen_options"][0]["legal_basis"]
    assert first["url"] == card["citizen_options"][0]["url"]
    assert answer["disclaimer"] == YAML["disclaimer"]["es"]
    assert [g["id"] for g in answer["general_options"]] == [g["id"] for g in YAML["general_options"]
                                                            if g["id"] in card["general"]]
    assert get(client, "en")["items"][0]["explained"]["plain"] == card["plain"]["en"]


def test_a_route_not_yet_in_force_carries_its_status_and_the_affected_label(client, cellar):
    cellar["rows"] = [("32024L2881", "Directiva (UE) 2024/2881 ...", "2024-10-23")]
    routes = get(client, "en")["items"][0]["explained"]["citizen_options"]
    affected = next(r for r in routes if r["who"] == "affected")
    assert affected["who_label"] == YAML["who_labels"]["affected"]["en"]
    assert affected["status"] == "Provided for by Directive (EU) 2024/2881; pending Spanish law."


def test_general_routes_go_once_even_when_several_cards_link_them(client, cellar):
    cellar["rows"] = [("32007L0060", "a", "2007-10-23"), ("32000L0060", "b", "2000-10-23")]
    ids = [g["id"] for g in get(client)["general_options"]]
    assert len(ids) == len(set(ids)) and "access_info" in ids


def test_an_also_for_act_brings_its_card(client, cellar):
    cellar["rows"] = [("32026R0667", "Reglamento (UE) 2026/667 ...", "2026-03-01")]
    (item,) = get(client)["items"]
    assert item["celex"] == "32026R0667" and item["visibility"] == "card"
    assert item["explained"]["celex"] == "32021R1119"


def test_the_card_goes_once_when_both_the_act_and_its_alias_are_listed(client, cellar):
    cellar["rows"] = [("32026R0667", "Reglamento (UE) 2026/667", "2026-03-01"),
                      ("32021R1119", "Reglamento (UE) 2021/1119", "2021-06-30")]
    items = by_celex(get(client))
    assert items["32021R1119"]["visibility"] == "card"
    assert items["32026R0667"]["visibility"] == "hidden" and "explained" not in items["32026R0667"]


def test_acts_without_a_card_follow_the_visibility_lists(client, cellar):
    cellar["rows"] = [("32026L0805", "Directiva (UE) 2026/805", "2026-04-01"),
                      ("32013L0064", "Directiva 2013/64/UE", "2013-12-17"),
                      ("32099L9999", "An act nobody has read", "2099-01-01")]
    items = by_celex(get(client))
    assert items["32026L0805"]["visibility"] == "title" and items["32026L0805"]["under"] == ["32000L0060",
                                                                                              "32006L0118"]
    assert items["32013L0064"]["visibility"] == "hidden"
    unknown = items["32099L9999"]
    assert unknown["visibility"] == "title" and "explained" not in unknown and "under" not in unknown
    assert unknown["title"] == "An act nobody has read"


def test_a_card_without_reviewed_by_falls_back_to_its_title(client, cellar, monkeypatch):
    cards = copy.deepcopy(REAL["legislation"])
    next(a for a in cards["acts"] if a["celex"] == "32007L0060")["reviewed_by"] = None
    monkeypatch.setattr(catalog_module, "load", lambda *a, **k: catalog_with(cards))
    cellar["rows"] = [("32007L0060", "Directiva 2007/60/CE", "2007-10-23")]
    answer = get(client)
    assert answer["items"][0]["visibility"] == "title" and "explained" not in answer["items"][0]
    assert answer["general_options"] == []


def test_without_cards_in_the_catalog_every_act_is_a_title(client, cellar, monkeypatch):
    catalog = catalog_with()
    del catalog["legislation"]
    monkeypatch.setattr(catalog_module, "load", lambda *a, **k: catalog)
    cellar["rows"] = [("32007L0060", "Directiva 2007/60/CE", "2007-10-23")]
    answer = get(client)
    assert answer["items"][0]["visibility"] == "title" and "disclaimer" not in answer


def test_every_card_of_the_yaml_is_reachable_through_the_route(client, cellar):
    # Cellar lists the Climate Law through its amendment 2026/667 (also_for), as it does on live data.
    listed = [a.get("also_for", [a["celex"]])[0] if a["celex"] == "32021R1119" else a["celex"] for a in YAML["acts"]]
    cellar["rows"] = [(celex, f"Act {celex}", "2020-01-01") for celex in listed]
    answer = get(client)
    explained = {i["explained"]["celex"] for i in answer["items"] if i["visibility"] == "card"}
    assert explained == {a["celex"] for a in YAML["acts"]} and len(explained) == 10
    linked = {x for a in YAML["acts"] for x in a["general"]}
    assert {g["id"] for g in answer["general_options"]} == linked


@pytest.mark.parametrize("offline", [True, False])
def test_with_no_cellar_the_cards_we_hold_still_come_and_never_an_error(client, cellar, monkeypatch, offline):
    if offline:
        monkeypatch.setenv("HUP_OFFLINE", "1")
    else:
        def timeout(query):
            raise requests.ConnectionError("no network")
        monkeypatch.setattr(legislation, "_ask", timeout)
    answer = get(client, "es")
    # Sara's decision of 14 Nov: without the live list, the cards of the variable's EuroVoc concepts still come
    # (the test variable carries 1440, floods), with the same absence saying the list could not be checked.
    held = {a["celex"] for a in YAML["acts"] if "1440" in a.get("eurovoc", [])}
    assert held == {"32007L0060"}
    assert {i["celex"] for i in answer["items"]} == held
    assert all(i["visibility"] == "card" and i["explained"]["plain"] and i["url"] for i in answer["items"])
    assert answer["absence"]["reason"] == "source_unreachable"
    assert "falta la lista" in answer["absence"]["detail"]["es"]
    assert answer["disclaimer"] == YAML["disclaimer"]["es"]
    assert [g["id"] for g in answer["general_options"]] == [g["id"] for g in YAML["general_options"]]


def test_a_cached_list_is_joined_with_the_cards_when_cellar_is_down(client, cellar, monkeypatch):
    cellar["rows"] = [("32007L0060", "Directiva 2007/60/CE", "2007-10-23")]
    first = get(client)
    monkeypatch.setenv("HUP_OFFLINE", "1")
    assert get(client) == first and first["items"][0]["visibility"] == "card"


def test_the_built_catalog_carries_the_yaml_as_written_and_validates():
    assert REAL["legislation"] == YAML
    assert contracts.errors("catalog", REAL) == []
    assert catalog_module.check(copy.deepcopy(REAL)) == []


@pytest.mark.parametrize("damage, expected", [
    (lambda c: c["acts"][0]["citizen_options"][0].update(who="everyone"), "who"),
    (lambda c: c["acts"][0].update(celex="2007/60"), "celex"),
    (lambda c: c["acts"][0]["plain"].pop("es"), "plain"),
    (lambda c: c["acts"][0].update(general=["no_such_route"]), "general route outside general_options"),
    (lambda c: c["acts"].append(copy.deepcopy(c["acts"][0])), "duplicate celex"),
    (lambda c: c["visibility"]["hidden"].append(c["acts"][0]["celex"]), "has a card"),
    (lambda c: c["visibility"]["title"].append({"celex": "32099L0001", "under": ["32099L0002"]}),
     "neither a card nor a title"),
    (lambda c: c["general_options"][0].update(url="ftp://example.org"), "url"),
])
def test_the_checks_catch_a_broken_legislation_yaml(damage, expected):
    catalog = copy.deepcopy(REAL)
    damage(catalog["legislation"])
    problems = catalog_module.check(catalog)
    assert problems and any(p.startswith("legislation.yaml") for p in problems)
    assert any(expected in p for p in problems), problems
