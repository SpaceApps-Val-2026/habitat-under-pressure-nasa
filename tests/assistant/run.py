"""Assistant test bench (B10): which share of the test questions give a right answer, with which model and cost.

    python -m tests.assistant.run --dry-run                          the stand-in model: spends nothing
    python -m tests.assistant.run --provider anthropic --model <id>  a real run: costs money, a person launches it

Prints, per case, PASS or FAIL and why; then the rate, the tokens, the cost and the median latency. Saves the
report as JSON under tests/assistant/reports/. The dry run also uses a stand-in place resolver and a temporary
data folder, so it touches no network and no real data. A real run uses the configured data folder, the real
place resolver and the key in .env, which this script never reads or prints.
"""
import argparse
import copy
import json
import os
import re
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

HERE = Path(__file__).parent
CAUSAL = re.compile(r"(?i)\b(caus\w*|provoc\w*|debido a|due to|because of|por culpa)\b")


class Double:
    """The stand-in model of --dry-run: answers the case's recorded fields and counts nothing."""

    def __init__(self):
        self.answer = None

    def __call__(self, task, data):
        call = {"task": task, "provider": "dry-run", "model": "double", "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0, "seconds": 0.0}
        return {"output": copy.deepcopy(self.answer), "call": call, "calls": [call]}


def stand_in_resolver(case):
    def resolve(text, language="en"):
        if case.get("gazetteer") == "not_found":
            return {"status": "not_found"}
        slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "place"
        return {"status": "found", "zone": {"id": f"dry-run-{slug}", "name": text, "bbox": [0.0, 0.0, 0.1, 0.1],
                                            "source": "dry-run stand-in, not a real place"}}
    return resolve


def check(answer: dict, case: dict) -> list[str]:
    """Why this answer does not pass; empty when one accepted shape matches."""
    texts = json.dumps(answer.get("plan", {}).get("answer", {}), ensure_ascii=False)
    if CAUSAL.search(texts):
        return [f"causal wording in the answer: {CAUSAL.search(texts).group(0)}"]
    reasons = []
    for accepted in case["expect"]["accept"]:
        why = _mismatch(answer, accepted)
        if not why:
            return []
        reasons.append(why)
    return [" / ".join(reasons)]


def _mismatch(answer: dict, accepted: dict) -> str | None:
    if answer.get("kind") != accepted["kind"]:
        return f"kind {answer.get('kind')} is not {accepted['kind']}"
    if accepted["kind"] == "article":
        return None if answer.get("article_url", "").startswith("/q/") else "the article has no report URL"
    if accepted["kind"] == "clarification":
        allowed = accepted.get("missing")
        return None if not allowed or answer.get("missing") in allowed else f"missing {answer.get('missing')}"
    plan = answer["plan"]
    level = plan["answer"]["level"]
    if accepted.get("design") and plan["design"] != accepted["design"]:
        return f"design {plan['design']} is not {accepted['design']}"
    lacking = set(accepted.get("variables", [])) - set(plan["variables"])
    if lacking:
        return f"variables lack {sorted(lacking)}"
    if accepted.get("levels") and level not in accepted["levels"]:
        return f"level {level} is not one of {accepted['levels']}"
    limits = [limit["en"] for limit in plan["answer"].get("limits", [])]
    if accepted.get("limit_contains") and not any(accepted["limit_contains"] in limit for limit in limits):
        return f"no limit says '{accepted['limit_contains']}'"
    if accepted.get("no_remote_without_limit"):
        remote = [s for s in plan["steps"] if s.get("data", {}).get("origin") == "remote"]
        if remote and not any("without network" in limit for limit in limits):
            return "a remote read without a limit that says there is no network"
    return None


def run(cases: list[dict], dry_run: bool, provider: str | None, model: str | None) -> dict:
    from hup_api import catalog as catalog_module
    from hup_api.assistant import service
    from hup_api.llm.adapter import LLMUnavailable
    from hup_data import places

    catalog = catalog_module.load()
    double, spent = Double(), []
    original_llm, original_resolve = service.llm_complete, places.resolve

    def recording(task, data):
        try:
            got = (double if dry_run else original_llm)(task, data)
        except LLMUnavailable as error:
            spent.extend(error.calls)
            raise
        spent.extend(got.get("calls") or [got["call"]])
        return got

    service.llm_complete = recording
    rows = []
    try:
        for case in cases:
            os.environ["HUP_OFFLINE"] = "1" if case.get("offline") or dry_run else "0"
            if dry_run:
                double.answer = case["double"]
                places.resolve = stand_in_resolver(case)
            answers, reasons, latencies = [], [], []
            for text in case["wordings"]:
                started = time.monotonic()
                try:
                    answer = service.from_text(text, case["language"], None, catalog)
                except LLMUnavailable as error:
                    answer = {"kind": "unavailable", "reason": str(error)}
                latencies.append(time.monotonic() - started)
                answers.append(answer)
                reasons += [f"«{text[:40]}»: {r}" for r in check(answer, case)]
            plans = [a for a in answers if a.get("kind") == "plan"]
            if len(plans) == 2 and plans[0]["plan_fingerprint"] != plans[1]["plan_fingerprint"]:
                reasons.append("the two wordings give different plans")
            rows.append({"id": case["id"], "passed": not reasons, "reasons": reasons, "latencies": latencies,
                         "answers": [{k: a.get(k) for k in ("kind", "missing", "plan_fingerprint")}
                                     | ({"design": a["plan"]["design"], "variables": a["plan"]["variables"],
                                         "level": a["plan"]["answer"]["level"]} if a.get("kind") == "plan" else {})
                                     for a in answers]})
    finally:
        service.llm_complete, places.resolve = original_llm, original_resolve
    latencies = [t for row in rows for t in row["latencies"]]
    known_costs = [c["cost_usd"] for c in spent if c.get("cost_usd") is not None]
    return {
        "date": datetime.now(UTC).isoformat(timespec="seconds"), "dry_run": dry_run,
        "provider": "dry-run" if dry_run else provider, "model": "double" if dry_run else model,
        "catalog_version": catalog["version"], "cases": rows,
        "passed": sum(row["passed"] for row in rows), "total": len(rows),
        "input_tokens": sum(c.get("input_tokens") or 0 for c in spent),
        "output_tokens": sum(c.get("output_tokens") or 0 for c in spent),
        "cost_usd": sum(known_costs) if known_costs else None,
        "calls_without_cost": sum(c.get("cost_usd") is None for c in spent),
        "median_seconds": statistics.median(latencies) if latencies else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.assistant.run")
    parser.add_argument("--dry-run", action="store_true", help="use the stand-in model; spends nothing")
    parser.add_argument("--provider", choices=["anthropic", "openai"])
    parser.add_argument("--model", help="model id for the plan task (HUP_LLM_MODEL_PLAN)")
    parser.add_argument("--cases", type=Path, default=HERE / "cases.yaml")
    parser.add_argument("--reports", type=Path, default=HERE / "reports")
    args = parser.parse_args(argv)
    if not args.dry_run and not (args.provider and args.model):
        parser.error("a real run needs --provider and --model; use --dry-run to spend nothing")

    cases = yaml.safe_load(args.cases.read_text(encoding="utf-8"))["cases"]
    saved = {k: os.environ.get(k) for k in ("HUP_DATA_DIR", "HUP_OFFLINE", "HUP_LLM_PROVIDER", "HUP_LLM_MODEL_PLAN")}
    scratch = tempfile.TemporaryDirectory() if args.dry_run else None
    try:
        if scratch:
            os.environ["HUP_DATA_DIR"] = scratch.name
        else:
            os.environ["HUP_LLM_PROVIDER"], os.environ["HUP_LLM_MODEL_PLAN"] = args.provider, args.model
        report = run(cases, args.dry_run, args.provider, args.model)
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if scratch:
            scratch.cleanup()

    for row in report["cases"]:
        print(f"{row['id']:<4} {'PASS' if row['passed'] else 'FAIL'}" + "".join(f"\n     {r}" for r in row["reasons"]))
    cost = "unknown" if report["cost_usd"] is None else f"{report['cost_usd']:.4f} USD"
    median = "n/a" if report["median_seconds"] is None else f"{report['median_seconds']:.2f} s"
    print(f"\n{report['provider']} {report['model']}: {report['passed']}/{report['total']} cases passed; "
          f"{report['input_tokens']} input and {report['output_tokens']} output tokens; cost {cost}"
          f" ({report['calls_without_cost']} calls without a known price); median latency {median}")
    args.reports.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{report['provider']}-{re.sub(r'[^A-Za-z0-9.-]+', '_', report['model'])}"
    file = args.reports / f"{name}.json"
    file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {file}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
