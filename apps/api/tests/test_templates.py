"""B4, the form path: a design, a zone, a period and variables become a valid plan built only from the catalog.

Every test uses the real catalog/catalog.json. Variables are chosen by what the catalog says they support, never
by name, so the tests keep their meaning when the catalog grows.
"""
import copy
import json

import pytest

from hup_api import catalog as catalog_module
from hup_api import executor
from hup_api.assistant import templates
from hup_api.assistant.templates import MissingInput, template
from hup_api.library import plan_cache
from hup_data import fingerprint, store

CATALOG = catalog_module.load()
ZONE = {"id": "test-zone", "name": "Test zone", "bbox": [10.0, 40.0, 10.1, 40.1]}
PERIOD = {"start": "2000-01-01", "end": "2024-12-31"}
EPISODE = {"start": "2024-10-01", "end": "2024-10-31"}
WINDOWS = {"before": [2014, 2019], "after": [2021, 2024]}


@pytest.fixture(autouse=True)
def data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("HUP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("HUP_OFFLINE", "0")
    return tmp_path


def supporting(design, catalog=CATALOG):
    return [v["id"] for v in catalog["variables"] if design in v.get("designs", [])]


def build(design, variables=None, period=None, catalog=CATALOG, **kwargs):
    variables = supporting(design, catalog) if variables is None else variables
    period = period or (EPISODE if design == "event" else PERIOD)
    return template(design, copy.deepcopy(ZONE), period, variables, catalog, **{**WINDOWS, **kwargs})


def runnable(plan, catalog=CATALOG):
    """Valid, already normalised, and numbered s1, s2... in order."""
    assert executor.validate_plan(plan, catalog) == []
    assert plan_cache.normalize(plan, catalog) == plan
    assert [s["id"] for s in plan["steps"]] == [f"s{i}" for i in range(1, len(plan["steps"]) + 1)]
    operations = {o["id"] for o in catalog["operations"]}
    assert {s["operation"] for s in plan["steps"]} <= operations
    for step in plan["steps"]:
        assert ("data" in step) == (step["type"] == "ingest")


DESIGNS_WITH_VARIABLES = [d for d in templates.DESIGNS if supporting(d)]


@pytest.mark.parametrize("design", templates.DESIGNS)
def test_every_design_gives_a_valid_plan(design):
    plan = build(design, supporting(design) or [CATALOG["variables"][0]["id"]])
    runnable(plan)
    if plan["answer"]["level"] != "not_possible":
        assert plan["design"] == design
        assert set(plan["variables"]) <= set(supporting(design))


@pytest.mark.parametrize("design", DESIGNS_WITH_VARIABLES)
def test_the_same_input_gives_the_same_plan_whatever_the_order(design):
    variables = supporting(design)
    first = build(design, variables)
    assert build(design, list(reversed(variables))) == first
    assert fingerprint.plan_fingerprint(first) == fingerprint.plan_fingerprint(build(design, variables))


def test_variables_that_do_not_support_the_design_are_left_out_with_a_limit():
    fit, other = supporting("trend")[0], next(v for v in CATALOG["variables"] if "trend" not in v["designs"])
    plan = build("trend", [fit, other["id"]])
    runnable(plan)
    assert plan["variables"] == [fit]
    assert plan["answer"]["level"] == "with_limits"
    assert any(other["name"]["en"] in limit["en"] for limit in plan["answer"]["limits"])


def test_scenario_without_observed_reference_uses_change_only():
    # A8 T3 option B: variables without a baseline give modelled change relative to 1995-2014, not an observed value.
    variables = {v["id"]: v for v in CATALOG["variables"]}
    unreferenced = ("tasmax_max", "days_above_35c", "pr_max_1day")
    for name in unreferenced:
        assert "scenario" in variables[name].get("designs", [])
        plan = build("scenario", [name])
        runnable(plan)
        assert plan["variables"] == [name]
        climate_steps = [s for s in plan["steps"] if s["operation"] == "climate_delta"]
        assert climate_steps and climate_steps[0]["params"].get("output") == "change"
        assert plan["answer"]["level"] == "with_limits"
        limits = " ".join(limit["es"] for limit in plan["answer"]["limits"])
        assert "cambio respecto a 1995" in limits and "no un valor observado" in limits


def test_a_variable_outside_the_catalog_is_a_limit_never_a_step():
    plan = build("trend", [supporting("trend")[0], "sealed_soil_made_up"])
    runnable(plan)
    assert "sealed_soil_made_up" not in json.dumps(plan["steps"])
    assert any("sealed_soil_made_up" in limit["en"] for limit in plan["answer"]["limits"])


def test_a_design_without_variables_is_not_possible_with_an_alternative():
    design = next((d for d in templates.DESIGNS if not supporting(d)), None)
    if design is None:
        pytest.skip("every design has variables in this catalog")
    plan = build(design, [CATALOG["variables"][0]["id"]])
    runnable(plan)
    assert plan["answer"]["level"] == "not_possible"
    assert plan["answer"]["alternative"]["en"] and plan["answer"]["alternative"]["es"]
    assert plan["design"] != design


def test_a_design_whose_operation_is_missing_is_not_possible():
    catalog = copy.deepcopy(CATALOG)
    catalog["operations"] = [o for o in catalog["operations"] if o["id"] != "trend"]
    plan = build("trend", supporting("trend"), catalog=catalog)
    runnable(plan, catalog)
    assert plan["answer"]["level"] == "not_possible"
    assert "trend" not in {s["operation"] for s in plan["steps"]}


def test_scan_with_catalogued_landsat_pair_adds_the_outer_control_and_chain():
    plan = build("scan", ["ndvi", "lst"])
    runnable(plan)
    chain, = [step for step in plan["steps"] if step["operation"] == "evaluate_chain"]
    assert chain["params"]["chain"]["id"] == "greenness_heat"
    assert chain["params"]["min_years"] == 10
    assert chain["params"]["chain"]["links"][0]["lag_years"] == [0, 0]
    assert chain["params"]["regional_ring"] == [3, 20]
    reads = [step for step in plan["steps"] if step["id"] in chain["depends_on"]]
    assert len(reads) == 3 and {step["params"]["grid_resolution_m"] for step in reads} == {100}
    assert {step["params"]["grid_min_years"] for step in reads} == {10}
    assert all(step["params"]["require_complete_months"] for step in reads)
    assert {step["params"]["season"] for step in reads} == {"summer"}
    control, = [step for step in reads if step["params"]["area"] == "ring"]
    assert control["params"]["ring_km"] == [3, 20] and control["params"]["ring_index"] == 1
    scan, = [step for step in plan["steps"] if step["operation"] == "scan_trends"]
    assert not set(scan["depends_on"]) & set(chain["depends_on"])
    assert not any(step["operation"] == "evaluate_chain" for step in build("scan", ["ndvi"])["steps"])
    catalog = copy.deepcopy(CATALOG)
    catalog["chains"][0]["id"] = "another-chain"
    other, = [step for step in build("scan", ["ndvi", "lst"], catalog=catalog)["steps"]
              if step["operation"] == "evaluate_chain"]
    assert other["params"]["min_years"] == 15


def test_declared_chains_do_not_duplicate_the_greenness_heat_scan():
    chains = {chain["id"]: chain for chain in CATALOG["chains"]}
    greenness = chains["greenness_heat"]
    assert greenness["links"][0] in chains["sealing_heat"]["links"]
    assert len(chains["flood_exposure"]["links"]) == 2
    variables = ["built_up_fraction", "ndvi", "lst"]
    baseline = build("scan", variables, catalog={**CATALOG, "chains": [greenness]})
    full = build("scan", variables, catalog={**CATALOG, "chains": list(reversed(CATALOG["chains"]))})
    assert full["steps"] == baseline["steps"]
    chain, = [step for step in full["steps"] if step["operation"] == "evaluate_chain"]
    assert chain["params"]["chain"]["id"] == "greenness_heat"


def test_trend_reads_by_year_and_monthly_products_need_full_years():
    plan = build("trend")
    reads = {s["params"]["variable"]: s for s in plan["steps"] if s["operation"] == "read_series"}
    variables = {v["id"]: v for v in CATALOG["variables"]}
    for variable_id, step in reads.items():
        assert step["params"]["aggregate"] == "year"
        regular = "revisit_days" not in (variables[variable_id].get("support") or {})
        summer = not regular and variable_id in {"lst", "ndvi"}
        # Satellites weigh every month the same and need as many scenes as months; Landsat heat and green are summer.
        assert step["params"]["min_count"] == (4 if summer else 12)
        assert step["params"].get("season") == ("summer" if summer else None)
        expected = "month_balanced" if not regular else (
            "sum" if variables[variable_id]["unit"].endswith("/month") else "mean")
        assert step["params"].get("statistic", "mean") == expected
    steps = {s["id"]: s for s in plan["steps"]}
    for step in plan["steps"]:
        if step["operation"] == "trend":
            assert len(step["depends_on"]) == 1 and steps[step["depends_on"][0]]["type"] == "ingest"


def test_impact_reads_the_zone_and_its_reference_and_contrasts_them():
    plan = build("impact")
    runnable(plan)
    steps = {s["id"]: s for s in plan["steps"]}
    contrasts = [s for s in plan["steps"] if s["operation"] == "baci_contrast"]
    assert len(contrasts) == len(plan["variables"])
    for contrast in contrasts:
        areas = sorted(steps[d]["params"]["area"] for d in contrast["depends_on"])
        assert areas == ["reference", "zone"]
        for read in (steps[d]["params"] for d in contrast["depends_on"]):
            ring = {"reference_inner_km": 3, "reference_km": 20, "land_only": True}
            assert ({k: read.get(k) for k in ring} == ring) is (read["area"] == "reference")
        assert {steps[d]["params"]["variable"] for d in contrast["depends_on"]} == {contrast["params"]["variable"]}
        assert contrast["params"]["before"] == WINDOWS["before"] and contrast["params"]["after"] == WINDOWS["after"]
    assert plan["comparison"] == {"type": "spatial_reference"}
    assert any("coincides in time and place" in limit["en"] for limit in plan["answer"]["limits"])


def test_impact_without_before_and_after_falls_back_to_a_design_without_a_date():
    plan = template("impact", ZONE, PERIOD, supporting("impact"), CATALOG)
    runnable(plan)
    assert plan["design"] == "trend" and plan["answer"]["level"] == "with_limits"
    assert plan["answer"]["alternative"]["es"]
    assert "date of the change" in plan["answer"]["limits"][0]["en"]


def test_an_event_reads_the_history_and_places_the_episode_in_it():
    if not supporting("event"):
        pytest.skip("no event variable in this catalog")
    plan = build("event")
    runnable(plan)
    # return_level places an annual maximum by its year alone (see the test of annual maxima below).
    episode = [s for s in plan["steps"] if s["type"] == "compute" and s["operation"] != "return_level"]
    assert episode and all(s["params"]["year"] == 2024 and s["params"]["months"] == [10] for s in episode)
    since = min(v["support"]["since"] for v in CATALOG["variables"] if v["id"] in plan["variables"])
    assert plan["period"] == {"start": f"{since}-01-01", "end": EPISODE["end"], "months": [10]}
    across = build("event", period={"start": "2023-10-01", "end": "2024-10-31"})
    runnable(across)
    assert across["design"] != "event" and "episode" in across["answer"]["limits"][0]["en"]


def test_write_and_review_enter_only_when_the_catalog_has_them():
    plan = build("trend")
    kinds = [s["type"] for s in plan["steps"]]
    assert kinds[-2:] == ["write", "review"]
    assert plan["steps"][-1]["depends_on"] == [plan["steps"][-2]["id"]]
    catalog = copy.deepcopy(CATALOG)
    catalog["operations"] = [o for o in catalog["operations"] if o["type"] not in ("write", "review")]
    plan = build("trend", catalog=catalog)
    runnable(plan, catalog)
    assert {s["type"] for s in plan["steps"]} == {"ingest", "compute"}


def test_a_missing_or_invalid_period_is_decided_and_said():
    # Only the place is asked (AGENTS.md, 6): the widest period the variables support, in answer.decided.
    for period in (None, {"start": "2020-01-01", "end": "2019-01-01"}):
        plan = template("trend", ZONE, period, supporting("trend"), CATALOG)
        runnable(plan)
        decided, = [d for d in plan["answer"]["decided"] if d["field"] == "period"]
        assert plan["period"]["start"][:4] in decided["text"]["en"] and decided["text"]["es"]
    with pytest.raises(MissingInput) as asked:
        template("trend", {}, PERIOD, supporting("trend"), CATALOG)
    assert asked.value.field == "place"


# read origins and the answer level --------------------------------------------------------------------------------

def variable_with_mode(mode):
    sources = {s["id"]: s for s in CATALOG["sources"]}
    return next(v for v in CATALOG["variables"] if "state" in v["designs"] and not v.get("limit")
                and all(sources[s]["access"]["mode"] == mode for s in v["sources"]))


def test_an_api_source_is_remote_and_complete_when_nothing_limits_it():
    variable = variable_with_mode("api")
    since = variable["support"]["since"]
    plan = build("state", [variable["id"]], period={"start": f"{since}-01-01", "end": f"{since + 1}-12-31"})
    runnable(plan)
    assert plan["steps"][0]["data"] == {"origin": "remote"}
    assert plan["answer"] == {"level": "complete"}


def test_offline_an_api_source_is_a_limit(monkeypatch):
    monkeypatch.setenv("HUP_OFFLINE", "1")
    variable = variable_with_mode("api")
    since = variable["support"]["since"]
    plan = build("state", [variable["id"]], period={"start": f"{since}-01-01", "end": f"{since + 1}-12-31"})
    assert plan["answer"]["level"] == "with_limits"


def manifest_line(source, path, bbox, period):
    return {"source": source, "product": "p", "version": "1", "item": "i", "scope": "global", "path": path,
            "mb": 1.0, "sha256": "0" * 64, "format": "netcdf", "bbox": bbox, "period": period,
            "ingest": {"date": "2026-11-14T00:00:00Z", "origin": "predownload", "url": "https://example.org/x"}}


def test_a_package_on_disk_gives_package_and_none_gives_a_limit(data_dir):
    variable = variable_with_mode("package")
    plan = build("state", [variable["id"]])
    assert plan["steps"][0]["data"]["origin"] == "package"
    assert any("downloaded package" in limit["en"] for limit in plan["answer"]["limits"])

    line = manifest_line(variable["sources"][0], f"packages/{variable['sources'][0]}/file.nc", [-180, -90, 180, 90],
                         {"start": "1900-01-01", "end": "2100-12-31"})
    (data_dir / "manifest.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    plan = build("state", [variable["id"]])
    assert plan["steps"][0]["data"]["origin"] == "package"
    assert not any("downloaded package" in limit["en"] for limit in plan["answer"].get("limits", []))

    line["path"] = f"cache/{variable['sources'][0]}/file.nc"
    (data_dir / "manifest.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    assert build("state", [variable["id"]])["steps"][0]["data"]["origin"] == "cache"


def test_a_step_already_in_the_store_is_derived():
    variable = variable_with_mode("api")
    plan = build("state", [variable["id"]])
    step = plan["steps"][0]
    sources = {s["id"]: s for s in CATALOG["sources"]}
    inputs = {s: sources[s]["version"] for s in step["sources"] if sources[s].get("version")}
    store.put_derived(fingerprint.step_fingerprint(step["operation"], step["version"], step["params"],
                                                   fingerprint.zone_key(plan["zone"]), inputs, plan["period"]),
                      {"results": {}})
    assert build("state", [variable["id"]])["steps"][0]["data"]["origin"] == "derived"


def test_a_period_partially_before_the_variable_starts_is_a_limit():
    variable = next(v for v in CATALOG["variables"] if "state" in v["designs"] and v.get("support", {}).get("since"))
    since = variable["support"]["since"]
    plan = build("state", [variable["id"]], period={"start": f"{since - 5}-01-01", "end": f"{since + 1}-12-31"})
    assert plan["answer"]["level"] == "with_limits"
    limits = " ".join(limit["en"] for limit in plan["answer"]["limits"])
    assert "2 years available" in limits and f"{since}" in limits and "not covered" in limits


def test_a_period_entirely_outside_variable_coverage_is_not_possible():
    variable = next(v for v in CATALOG["variables"] if "trend" in v["designs"] and v.get("support", {}).get("since"))
    since = variable["support"]["since"]
    # Entirely before the variable starts -> no overlap (Guillem 5.3).
    end = since - 1
    start = end - 5
    plan = build("trend", [variable["id"]], period={"start": f"{start}-01-01", "end": f"{end}-12-31"})
    runnable(plan)
    assert plan["answer"]["level"] == "not_possible"
    limits = " ".join(limit["en"] for limit in plan["answer"].get("limits", []))
    assert "no year" in limits and str(since) in limits
    assert plan["answer"]["alternative"]["en"] and plan["answer"]["alternative"]["es"]


def test_no_limit_or_alternative_uses_causal_words():
    words = ("cause", "caus", "provoc", "debido", "because", "due to", "culpa")
    for design in templates.DESIGNS:
        plan = build(design, supporting(design) or [CATALOG["variables"][0]["id"]])
        answer = json.dumps(plan["answer"], ensure_ascii=False).lower()
        assert not any(word in answer for word in words), (design, answer)


# spatial extent (5.3) -------------------------------------------------------------------------------------------

def test_spatial_extent_outside_excludes_variable():
    catalog = copy.deepcopy(CATALOG)
    target = next(v for v in catalog["variables"] if "trend" in v.get("designs", []))
    target["extent"] = {"kind": "bbox", "bbox": [-10.0, 30.0, 0.0, 40.0], "verified": True}
    # zone is at lon ~10, lat ~40 so completely outside bbox lon range
    plan = build("trend", [target["id"]], catalog=catalog)
    runnable(plan, catalog)
    assert plan["answer"]["level"] == "not_possible"
    limits = " ".join(limit["en"] for limit in plan["answer"].get("limits", []))
    assert "outside the available data extent" in limits


def test_spatial_extent_partial_is_with_limits():
    catalog = copy.deepcopy(CATALOG)
    target = next(
        v for v in catalog["variables"]
        if "state" in v.get("designs", []) and "trend" in v.get("designs", [])
    )
    since = target.get("support", {}).get("since") or 2000
    # bbox overlaps zone partially
    target["extent"] = {"kind": "bbox", "bbox": [10.05, 40.0, 10.2, 40.1], "verified": True}
    period = {"start": f"{since}-01-01", "end": f"{since + 1}-12-31"}
    plan = build("state", [target["id"]], period=period, catalog=catalog)
    runnable(plan, catalog)
    assert target["id"] in plan["variables"]
    assert plan["answer"]["level"] == "with_limits"
    limits = " ".join(limit["en"] for limit in plan["answer"].get("limits", []))
    assert "only part of this place" in limits


def test_spatial_extent_unverified_adds_limit():
    catalog = copy.deepcopy(CATALOG)
    target = next(
        v for v in catalog["variables"]
        if v["id"] == variable_with_mode("api")["id"]
    )
    target["extent"] = {"kind": "global", "verified": False}
    since = target["support"]["since"]
    period = {"start": f"{since}-01-01", "end": f"{since + 1}-12-31"}
    plan = build("state", [target["id"]], period=period, catalog=catalog)
    runnable(plan, catalog)
    assert plan["answer"]["level"] == "with_limits"
    limits = " ".join(limit["en"] for limit in plan["answer"].get("limits", []))
    assert "not been verified" in limits




def annual_maxima_catalog():
    """The real catalog, with the annual maxima also allowed in an episode (until the catalog says so itself)."""
    catalog = copy.deepcopy(CATALOG)
    for variable in catalog["variables"]:
        if variable["id"] in templates.ANNUAL_MAXIMA and "event" not in variable["designs"]:
            variable["designs"].append("event")
    return catalog


def test_the_state_of_annual_maxima_gives_return_levels_from_whole_years():
    maxima = [v for v in supporting("state") if v in templates.ANNUAL_MAXIMA]
    if not maxima:
        pytest.skip("no annual series of maxima in this catalog")
    others = [v for v in supporting("state") if v not in templates.ANNUAL_MAXIMA][:1]
    plan = build("state", maxima + others, period={**PERIOD, "months": [10]})
    runnable(plan)
    levels = [s for s in plan["steps"] if s["operation"] == "return_level"]
    assert len(levels) == len(maxima) and all("event_year" not in s["params"] for s in levels)
    for step in levels:
        read, = [s for s in plan["steps"] if s["id"] in step["depends_on"]]
        assert read["params"]["variable"] in maxima
        assert read["params"]["read_period"] == {"start": PERIOD["start"], "end": PERIOD["end"]}  # no months
    assert not [s for s in plan["steps"] if s["type"] == "compute" and s not in levels]


def test_an_episode_of_annual_maxima_sets_its_year_against_every_year_before_it():
    catalog = annual_maxima_catalog()
    maxima = [v for v in supporting("event", catalog) if v in templates.ANNUAL_MAXIMA]
    if not maxima:
        pytest.skip("no annual series of maxima in this catalog")
    others = [v for v in supporting("event", catalog) if v not in templates.ANNUAL_MAXIMA]
    plan = build("event", maxima + others, period={"start": "2024-10-29", "end": "2024-10-29"}, catalog=catalog)
    runnable(plan, catalog)
    by_id = {s["id"]: s for s in plan["steps"]}
    for step in (s for s in plan["steps"] if s["type"] == "compute"):
        read, *more = [by_id[d] for d in step["depends_on"]]
        annual = read["params"]["variable"] in templates.ANNUAL_MAXIMA
        assert (step["operation"] == "return_level") == annual
        if annual:
            since = min(v["support"]["since"] for v in catalog["variables"] if v["id"] in maxima)
            assert step["params"]["event_year"] == 2024 and "months" not in step["params"]
            # The whole years from the start of the record to the year before the episode, never only the months of
            # the episode, and the days of the episode from the daily variable (review of A11).
            variable, = [v for v in catalog["variables"] if v["id"] == read["params"]["variable"]]
            start = max(f"{variable['support']['since']}-01-01", plan["period"]["start"])
            assert read["params"]["read_period"] == {"start": start, "end": "2023-12-31"} and since <= 2024
            days, = more
            assert days["params"]["variable"] == templates.ANNUAL_MAXIMA[variable["id"]]
            assert days["params"]["read_period"] == {"start": "2024-10-29", "end": "2024-10-29"}
            assert days["params"]["aggregate"] == "none"
        else:
            assert not more
            assert step["params"]["year"] == 2024 and step["params"]["months"] == [10]
            assert "read_period" not in read["params"]
