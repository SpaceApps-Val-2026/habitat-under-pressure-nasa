"""A9 T5 (Guillem, «Sí, arregla-ho»): what keeps the estimate on screen. In whatif the estimate is the first finding
(the report's headline) and the title names what was asked about; a per-day estimate is also read as a year; the
range is said in words with its figures in a limit (findings carry placeholders only). No network, no LLM: the
stack of test_whatif."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # the tests folder is no package: reuse test_whatif's stack
from test_whatif import build, estimates, run, setup  # noqa: E402, F401  (setup: its autouse fixture)


def test_whatif_puts_its_estimate_first_and_names_it_in_the_title():
    plan = build(archetypes=["test-household-water"], quantity=10000)
    analysis, results = run(plan)
    estimate, = estimates(results)
    first = min(analysis["findings"], key=lambda f: f["order"])
    assert first["results"] == [estimate["id"]]
    assert analysis["title"]["es"] == "Test zone: Consumo doméstico de agua"
    assert analysis["title"]["en"] == "Test zone: Household water use"


def test_a_per_day_estimate_is_also_read_as_a_year_and_its_range_said_in_words():
    analysis, results = run(build(archetypes=["test-household-water"], quantity=10000))
    estimate, = estimates(results)
    row = dict(zip(estimate["table"]["columns"], estimate["table"]["rows"][0], strict=True))
    assert row["annual_unit"] == "m3/year"
    assert (row["annual"], row["annual_low"], row["annual_high"]) == (
        10000 * 157 * 365 / 1000, 10000 * 133 * 365 / 1000, 10000 * 165 * 365 / 1000)
    said = next(limit for limit in estimate["limits"] if limit["es"].startswith("Estimación:"))
    assert said["es"] == ("Estimación: 1.570.000 L/día (rango: de 1.330.000 a 1.650.000), es decir, 573.000 m³ al "
                          "año (rango: de 485.400 a 602.200). El rango no tiene nivel de probabilidad.")
    assert "intervalo" not in said["es"] and "interval" not in said["en"]
    assert not any(".." in limit["es"] or ".." in limit["en"] for limit in estimate["limits"])


def test_a_yearly_unit_is_not_converted():
    analysis, results = run(build(archetypes=["test-per-mw"], quantity=30))
    estimate, = estimates(results)
    assert "annual" not in estimate["table"]["columns"]
    assert any(limit["en"].startswith("Estimate: 60 m³ a year (range: 30 to 90)") for limit in estimate["limits"])
