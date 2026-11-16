"""The opening answers the question in its first sentence, for every design, from the results alone (phase 1)."""

import re

import pytest
import yaml

from hup_api.reviewer.text import CAUSAL, LEVELS
from hup_api.writer import _phrases, compose_fallback, fill
from hup_api.writer.opening import answer

SOURCES = [{"source": "test", "url": "urn:test"}]
CATALOG = {"variables": [
    {"id": "air_temperature_max", "name": {"en": "Air temperature, monthly mean of daily maxima",
                                           "es": "Temperatura del aire, media mensual de las máximas diarias"}},
    {"id": "air_temperature", "name": {"en": "Mean air temperature", "es": "Temperatura media del aire"}},
    {"id": "precipitation", "name": {"en": "Precipitation, monthly total", "es": "Precipitación, total mensual"}},
    {"id": "pr_max_1day_observed", "name": {"en": "Wettest day of the year, observed",
                                            "es": "Día más lluvioso del año, observado"}},
    {"id": "tas_mean", "name": {"en": "Projected mean temperature", "es": "Temperatura media proyectada"}},
    {"id": "lst", "name": {"en": "Land surface temperature", "es": "Temperatura de superficie"}},
    {"id": "population_in_flood_area", "name": {"en": "Population in the flooded area",
                                                "es": "Población en la zona inundada"}},
    {"id": "water", "name": {"en": "Water", "es": "Agua"}},
]}
GENERIC = {"headline": {"en": "What the data show about this question",
                        "es": "Qué dicen los datos sobre esta pregunta"},
           "lead": {"en": "The report brings together the evidence available for this question.",
                    "es": "El informe reúne la evidencia disponible para esta pregunta."},
           "meaning": {"en": "The period, resolution and sources describe where the result applies.",
                       "es": "El periodo, la resolución y las fuentes delimitan el alcance del resultado."},
           "cannot_say": {"en": "Kept.", "es": "Se mantiene."}, "results": []}


def result(rid, operation, variable, **fields):
    return {"id": rid, "type": "value", "variable": variable, "evidence_level": "E0", "data_class": "measured",
            "method": {"operation": operation, "params": fields.pop("params", {})}, "sources": SOURCES, **fields}


def plan(design, place="Alzira", **extra):
    return {"design": design, "zone": {"name": place}, "request": {"text": ""}, "answer": {"decided": []}, **extra}


def opening(design_plan, results, piece=GENERIC):
    piece = answer(design_plan, results, piece, CATALOG)
    assert piece is not None
    return {lang: {field: fill(piece[field][lang], results, lang) for field in ("headline", "lead", "cannot_say")}
            for lang in ("en", "es")}, piece


PERIOD = {"start": "1981-01-01", "end": "2025-12-31"}
INTERVAL = {"level": 0.95, "low": 0.025, "high": 0.055, "method": "Sen slope, Gilbert interval"}
TREND = result("r-s2-trend", "trend", "air_temperature_max", value=0.04, unit="°C/year", interval=INTERVAL,
               period=PERIOD, evidence_level="E1", direction_assertable=True)
ESTIMATE = result(
    "r-s9-estimate", "resource_estimate", "water", value=74100.0, unit="m3/year",
    params={"archetype": {"name": {"en": "Large data center water intake",
                                   "es": "Agua de entrada de un centro de datos grande"}}},
    table={"columns": ["resource", "unit", "low", "high", "reference", "quantity", "per", "basis", "quantity_low",
                       "quantity_high", "quantity_reference"],
           "rows": [["water", "m3/year", 35850.0, 168200.0, "EU 2025", 25.0, "MW IT", "typical", 10.0, 100.0, "IEA"]]})
LAND = result("r-s10-estimate", "resource_estimate", "water",
              params={"archetype": {"name": {"en": "Land of a large data center",
                                             "es": "Suelo de un centro de datos grande"}}},
              absence={"reason": "not_computed"})
EVENT = result("r-s3-event", "return_level", "pr_max_1day_observed", value=2.29, unit="years",
               interval={"level": 0.95, "low": 1.65, "high": 3.83, "method": "Parametric bootstrap"},
               support={"resolution_m": 11100},
               diagnostics={"n_years": 26, "event_year": 2024, "event_value": 57.16, "event_day": "2024-10-29"})
SERIES = [{"t": horizon, "scenario": scenario, "v": v, "low": v - 0.6, "high": v + 0.9}
          for horizon in ("2030", "2050") for scenario, v in (("ssp126", 18.4), ("ssp585", 19.6))]
DELTA = result("r-s3-delta", "climate_delta", "tas_mean", type="series", unit="°C", series=SERIES,
               params={"windows": {"2030": [2025, 2034], "2050": [2045, 2054]}, "reference": [1995, 2014]})
CONTRAST = result("r-s3-contrast", "baci_contrast", "lst", value=0.447, unit="°C", evidence_level="E2",
                  direction_assertable=True, interval={"level": 0.95, "low": 0.18, "high": 0.82},
                  params={"before": [2019, 2021], "after": [2023, 2025]})


def test_trend_answers_with_its_rate_place_period_and_test():
    text, piece = opening(plan("trend"), [TREND])
    assert text["es"]["lead"].startswith(
        "Temperatura del aire, media mensual de las máximas diarias en Alzira: aumenta a un ritmo de 0,40 °C por "
        "década (intervalo del 95 %: de 0,25 a 0,55), de 1981 a 2025.")
    assert text["en"]["lead"].startswith("Air temperature, monthly mean of daily maxima in Alzira: it increases at")
    assert text["es"]["cannot_say"].startswith("Se mantiene. El ritmo es la media de todo el periodo")
    assert piece["results"][0] == "r-s2-trend"
    assert text["es"]["headline"] == ("Temperatura del aire, media mensual de las máximas diarias en Alzira: "
                                      "ritmo de cambio")


def test_trend_without_a_direction_says_so_and_keeps_its_figure():
    unclear = {**TREND, "value": 0.01, "interval": {**INTERVAL, "low": -0.01, "high": 0.03},
               "direction_assertable": False}
    text, _ = opening(plan("trend"), [unclear])
    assert "no permiten afirmar una dirección; el ritmo estimado es de 0,1 °C por década" in text["es"]["lead"]
    assert "an interval that includes zero" in text["en"]["lead"]
    assert "aumenta" not in text["es"]["lead"]


def test_whatif_answers_with_the_estimate_its_size_and_range_and_names_what_was_not_computed():
    text, piece = opening(plan("whatif", "Sagunt"), [ESTIMATE, LAND])
    assert text["es"]["lead"].startswith(
        "Agua de entrada de un centro de datos grande en Sagunt: unos 74.100 m³/año para un tamaño de ejemplo "
        "publicado (25 MW IT), dentro de un rango de 35.800 a 168.000 m³/año.")
    assert "Suelo de un centro de datos grande: no se ha calculado; el cálculo no se pudo" in text["es"]["lead"]
    assert text["en"]["lead"].startswith("Large data center water intake in Sagunt: about 74,100 m³/year")
    assert "not measurements at the site" in text["en"]["cannot_say"]
    assert piece["results"][:2] == ["r-s9-estimate", "r-s10-estimate"]


def test_event_answers_with_the_wettest_day_and_its_return_period():
    text, _ = opening(plan("event", "Paiporta"), [EVENT])
    assert text["es"]["lead"].startswith(
        "En Paiporta, el día más lluvioso del episodio, el 29 de octubre de 2024, dejó 57,2 mm de media en la zona; "
        "un día así es el máximo de un año, de media, una vez cada 2,3 años")
    assert text["en"]["lead"].startswith("In Paiporta, the wettest day of the episode, 29 October 2024, brought 57.2")
    assert "11,1 km" in text["es"]["cannot_say"]  # the grid limit of the result


def test_event_rarer_than_its_record_gives_no_return_period():
    rare = {key: value for key, value in EVENT.items() if key not in ("value", "interval")}
    rare = {**rare, "absence": {"reason": "not_computed"},
            "diagnostics": {**EVENT["diagnostics"], "event_value": 149.4}}
    text, _ = opening(plan("event", "Paiporta"), [rare])
    assert "149 mm" in text["es"]["lead"] and "más raro de lo que permiten estimar 26 años" in text["es"]["lead"]
    assert "no return period is given" in text["en"]["lead"]


def test_monthly_event_answers_with_its_percentile():
    extreme = result("r-s2-extreme", "rain_extreme", "precipitation", value=84.6, unit="percentile",
                     params={"year": 2024, "months": [10]})
    anomaly = result("r-s3-anomaly", "same_date_anomaly", "precipitation", value=42.0, unit="mm")
    text, _ = opening(plan("event"), [extreme, anomaly])
    assert text["es"]["lead"] == ("En Alzira, la lluvia de octubre de 2024 quedó en el percentil 84,6 de las ventanas "
                                  "comparables del registro. La diferencia frente a ventanas equivalentes es de 42 mm.")


def test_scenario_answers_with_the_latest_horizon_across_scenarios():
    text, _ = opening(plan("scenario"), [DELTA])
    assert text["es"]["lead"].startswith(
        "Hacia 2050 (de 2045 a 2054), la mediana de los modelos sitúa temperatura media proyectada en Alzira entre "
        "18,4 °C (SSP1-2.6) y 19,6 °C (SSP5-8.5), según el escenario; entre modelos, de 17,8 a 20,5 °C.")
    assert "not a forecast for a given year" in text["en"]["cannot_say"]
    change = {**DELTA, "method": {**DELTA["method"], "params": {**DELTA["method"]["params"], "output": "change"}},
              "series": [{**p, "v": p["v"] - 17, "low": p["low"] - 17, "high": p["high"] - 17} for p in SERIES]}
    text, _ = opening(plan("scenario"), [change])
    assert "un cambio de +1,4 °C (SSP1-2.6) a +2,6 °C (SSP5-8.5) respecto a 1995–2014" in text["es"]["lead"]


def test_impact_answers_with_the_contrast_and_its_windows():
    text, _ = opening(plan("impact"), [CONTRAST])
    assert text["es"]["lead"].startswith("En Alzira, el cambio de la temperatura de superficie es mayor que en su "
                                         "entorno, de 0,4 °C (intervalo del 95 %: de 0,2 a 0,8), al comparar de "
                                         "2019 a 2021 / de 2023 a 2025.")
    assert "origin of the difference" in text["en"]["cannot_say"]
    unclear = {**CONTRAST, "direction_assertable": False, "interval": {"level": 0.95, "low": -0.2, "high": 0.9}}
    text, _ = opening(plan("impact"), [unclear])
    assert "compatible con un cambio parecido en los dos" in text["es"]["lead"]


def test_exposure_answers_with_the_people_counted():
    counted = result("r-s2-flooded-area", "exposure", "population_in_flood_area", value=12340.0, unit="people",
                     params={"category": "flooded_area"})
    text, _ = opening(plan("exposure", "Paiporta"), [counted])
    assert text["es"]["lead"].startswith("En Paiporta, población dentro del área inundada cartografiada: se estiman "
                                         "12.300 personas.")


def test_state_answers_with_its_summary_and_latest_reading():
    rain = result("r-s1-series", "read_series", "precipitation", type="series", unit="mm/month",
                  period={"start": "2024-01-01", "end": "2024-12-31"},
                  series=[{"t": f"2024-{m:02d}", "v": v} for m, v in ((1, 12.0), (10, 210.0), (12, 8.5))])
    text, _ = opening(plan("state", variables=["precipitation"]), [rain])
    assert text["es"]["lead"].startswith("Precipitación, total mensual en Alzira, de 2024 a 2024: total de los datos")
    assert text["es"]["lead"].endswith("Última lectura (diciembre de 2024): 8,5 mm.")
    assert text["en"]["lead"].endswith("Latest reading (December 2024): 8.5 mm.")


def test_an_absent_result_says_what_is_missing_why_and_what_the_report_shows():
    missing = {key: value for key, value in DELTA.items() if key != "series"}
    missing["absence"] = {"reason": "insufficient_qa", "detail": {
        "en": "A compatible series with model and scenario is missing.",
        "es": "Falta una serie compatible con modelo y escenario."}}
    observed = result("r-s2-series", "read_series", "precipitation", type="series", unit="mm/year",
                      series=[{"t": str(year), "v": 450.0} for year in range(1998, 2025)])
    text, piece = opening(plan("scenario"), [observed, missing])
    assert text["es"]["lead"] == (
        "Delfos no ha podido obtener la proyección de temperatura media proyectada en Alzira: falta una serie "
        "compatible con modelo y escenario. El informe sí muestra precipitación, total mensual (de 1998 a 2024).")
    assert text["en"]["lead"].startswith("Delfos could not obtain the projection of projected mean temperature for "
                                         "Alzira: a compatible series")
    assert text["es"]["headline"] == "Falta el dato principal para responder sobre Alzira"
    assert text["es"]["cannot_say"].endswith("no significa que su valor sea cero ni que no haya pasado nada.")
    assert piece["results"] == ["r-s3-delta", "r-s2-series"]
    no_detail = {**missing, "absence": {"reason": "no_coverage"}}
    text, _ = opening(plan("trend"), [{**TREND, **{k: no_detail[k] for k in ("absence",)}}])
    assert "las fuentes del catálogo no cubren este lugar o este periodo" in text["es"]["lead"]


def test_earlier_prose_stays_only_when_it_says_something_else():
    written = {**GENERIC, "headline": {"en": "Alzira warms", "es": "Alzira se calienta"},
               "lead": {"en": "It warms by {r-s2-trend.value}.", "es": "Se calienta {r-s2-trend.value}."}}
    _, piece = opening(plan("trend"), [TREND], written)
    assert "Se calienta" not in piece["lead"]["es"] and piece["headline"]["es"] == "Alzira se calienta"
    other = {**written, "lead": {"en": "Summers are long here.", "es": "Aquí los veranos son largos."}}
    _, piece = opening(plan("trend"), [TREND], other)
    assert piece["lead"]["es"].endswith("Aquí los veranos son largos.")
    assert piece["lead"]["es"].startswith("Temperatura del aire")


def test_recurrence_and_milestone_openings_are_left_alone():
    decided = {"decided": [{"field": "recurrence", "text": {"en": "x", "es": "x"}}]}
    assert answer({**plan("event"), "answer": decided}, [EVENT], GENERIC, CATALOG) is None
    decided = {"decided": [{"field": "milestone", "text": {"en": "x", "es": "x"}}]}
    assert answer({**plan("event"), "answer": decided}, [EVENT], GENERIC, CATALOG) is None


def test_the_fallback_report_opens_with_the_answer():
    story = compose_fallback({**plan("trend"), "period": PERIOD}, [TREND], CATALOG)
    lead = fill(story["editorial"]["journalistic"]["lead"]["es"], [TREND], "es")
    assert lead.startswith("Temperatura del aire, media mensual de las máximas diarias en Alzira: aumenta")


@pytest.mark.parametrize("language", ["en", "es"])
def test_answer_phrases_have_no_causal_words_or_unsupported_verbs(language):
    phrases = _phrases(language)["answer"]

    def texts(node, path=()):
        if isinstance(node, dict):
            for key, value in node.items():
                yield from texts(value, (*path, key))
        else:
            yield path, node

    for path, text in texts(phrases):
        assert not CAUSAL.search(text), (path, text)
        if path[:2] != ("trend", "detected"):  # the only phrase said at E1, with the tendency verb
            for _, pattern in LEVELS:
                assert not re.search(pattern, text, re.IGNORECASE), (path, text)
    english = yaml.safe_load(open(_phrase_file("en"), encoding="utf8"))["answer"]
    spanish = yaml.safe_load(open(_phrase_file("es"), encoding="utf8"))["answer"]
    assert set(map(tuple, (p for p, _ in texts(english)))) == set(map(tuple, (p for p, _ in texts(spanish))))


def _phrase_file(language):
    from pathlib import Path

    import hup_api.writer

    return Path(hup_api.writer.__file__).parent / "phrases" / f"{language}.yaml"
