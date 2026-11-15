"""Licence and citation of each catalog source: a plain string or text by language, served as written."""
import copy
import json

import pytest
from fastapi.testclient import TestClient

from hup_api import catalog, contracts, main
from hup_data import paths

# Sources whose licence and citation the data front wrote in en and es (.dev/notes/2026-11-14-licencias-traducidas.md)
BILINGUAL = ["landsat-c2-l2", "nasa-power", "smap-l3", "chj-phj-2022-2027", "imerg-monthly", "nex-gddp-cmip6", "ghsl",
             "copernicus-ems-emsr773", "patricova", "ine", "ine-population", "sedac-ssp", "jrc-gsw", "boe-rd-35-2023",
             "ghsl-built-s", "snczi", "sentinel-1-rtc"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return catalog.build(out=tmp_path_factory.mktemp("catalog") / "catalog.json")


def sources(built):
    return {s["id"]: s for s in built["sources"]}


def example():
    return json.loads((paths.CONTRACTS_DIR / "examples" / "catalog.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("value", ["Public domain", {"en": "Public domain"}, {"en": "Public domain", "es": "Dominio"}])
def test_license_and_citation_take_either_shape(value):
    data = example()
    data["sources"][1].update(license=value, citation=value)
    assert contracts.errors("catalog", data) == []


@pytest.mark.parametrize("value", [{"es": "Dominio público"}, {"en": 1}, ["Public domain"], None, 3])
def test_license_and_citation_reject_anything_else(value):
    for field in ("license", "citation"):
        data = copy.deepcopy(example())
        data["sources"][1][field] = value
        assert contracts.errors("catalog", data), (field, value)


def test_the_example_shows_both_shapes():
    shapes = {type(s[f]).__name__ for s in example()["sources"] for f in ("license", "citation")}
    assert shapes == {"str", "dict"}


@pytest.mark.parametrize("source_id", BILINGUAL)
def test_covered_sources_carry_licence_and_citation_in_en_and_es(built, source_id):
    source = sources(built)[source_id]
    for field in ("license", "citation"):
        assert set(source[field]) == {"en", "es"}, field
        assert all(text.strip() for text in source[field].values()), field


def test_every_source_keeps_a_licence_and_a_citation(built):
    """Nothing lost: every source still says something, in English at least, and the order is unchanged."""
    assert built["sources"][0]["id"] == "landsat-c2-l2"
    for source in built["sources"]:
        for field in ("license", "citation"):
            value = source[field]
            english = value if isinstance(value, str) else value["en"]
            assert english.strip(), (source["id"], field)
    # mur-sst got its licence in both languages from Sara later; its citation is a bibliographic reference,
    # the same in both, and stays a plain string
    assert set(sources(built)["mur-sst"]["license"]) == {"en", "es"}
    assert isinstance(sources(built)["mur-sst"]["citation"], str)


MARKERS = [
    ("sentinel-1-rtc", "en", "Contains modified Copernicus Sentinel data [year]."),
    ("sentinel-1-rtc", "es", "Contains modified Copernicus Sentinel data [year]."),
    ("copernicus-ems-emsr773", "es", "(© 2024 European Union), [EMSR773] [AOI]: Delineation"),
    ("patricova", "es", "PATRICOVA [year] CC BY 4.0, Generalitat"),
    ("boe-rd-35-2023", "en", ", consulted [date]"),
    ("boe-rd-35-2023", "es", ", consultado el [fecha]"),
    ("chj-phj-2022-2027", "en", "[document], p. [page], updated [date]"),
    ("chj-phj-2022-2027", "es", "[documento], p. [página], actualizado el [fecha]"),
    ("ine", "en", "Seccionado censal 2026 ([update date])"),
    ("ine", "es", "Seccionado censal 2026 ([fecha de actualización])"),
    ("imerg-monthly", "es", "Accessed: [date], doi:10.5067/GPM/IMERG/3B-MONTH/07"),
    ("nex-gddp-cmip6", "es", "was accessed on [date] from https://registry.opendata.aws/nex-gddp-cmip6"),
    ("smap-l3", "en", "SPL3SMP_E version 006, doi:[to be confirmed]"),
    ("smap-l3", "es", "SPL3SMP_E versión 006, doi:[por confirmar]"),
]


@pytest.mark.parametrize(("source_id", "language", "literal"), MARKERS)
def test_citation_markers_are_kept_literally(built, source_id, language, literal):
    assert literal in sources(built)[source_id]["citation"][language]


def test_literal_footers_stay_in_the_holder_language(built):
    """A footer the holder gives in one language is the same text in en and es."""
    for source_id in ("landsat-c2-l2", "nasa-power", "copernicus-ems-emsr773", "sentinel-1-rtc", "snczi", "jrc-gsw",
                      "ine-population", "ghsl", "ghsl-built-s"):
        citation = sources(built)[source_id]["citation"]
        assert citation["en"] == citation["es"], source_id


def test_api_serves_the_texts_as_written(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path / "data"))
    served = TestClient(main.app).get("/api/catalog").json()
    assert contracts.errors("catalog", served) == []
    assert served["sources"] == catalog.load()["sources"]
    ghsl = next(s for s in served["sources"] if s["id"] == "ghsl")
    assert ghsl["license"]["es"].startswith("CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Atribución")
