"""Typed reading of a question, Jev against the lexical floor, on the 25 questions of jev_eval.yaml.

    python -X utf8 -m tests.assistant.jev_eval [--no-jev] [--timeout 10]

Prints one row per question and the totals; saves the report under tests/assistant/reports/. Jev is called through
hup_api/llm/jev.py, which reads TYPESAFE_API_KEY from the environment; this script never reads or prints the key.
The fast-path column says whether service.fast_plan_fields would plan the question without the planning model
(it does not resolve the place: no gazetteer, no network beyond Jev).
"""
import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

HERE = Path(__file__).parent
JEV_PRICE_PER_TOKEN = 42 / 1_000_000_000  # docs/arquitectura-v0.3.md, 5.2: 42 USD per billion input tokens


def _time_kind(understanding: dict) -> str | None:
    times = understanding.get("times") or []
    return times[0]["kind"] if times else None


def _score(expect: dict, got: dict) -> dict:
    designs = expect["designs"]
    out = {
        "design": (got["design"] in designs) if designs else None,
        "subjects": set(expect["subjects"]) <= set(got["subjects"]),
        "shortlist": set(expect["shortlist"]) <= set(got["shortlist"]),
        "time": got["time"] == expect["time"],
        "place": got["place"] == expect["place"] if got["place"] is not None else None,
        "in_scope": got["in_scope"] == expect["in_scope"] if got["in_scope"] is not None else None,
    }
    return out


def run(cases: list[dict], use_jev: bool, timeout: float) -> dict:
    from hup_api import catalog as catalog_module
    from hup_api.assistant import prefilter, service, understand
    from hup_api.llm import jev

    catalog = catalog_module.load()
    rows = []
    for case in cases:
        text, language, expect = case["text"], case["language"], case["expect"]
        words = understand.words(text, language)
        lexical = prefilter.select(catalog, text, words, "lexical")
        floor = {"design": (words["intents"] or [None])[0], "subjects": words["subjects"],
                 "shortlist": lexical["variables"], "time": _time_kind(words),
                 "place": understand.place_words(text) is not None, "in_scope": None}
        row = {"id": case["id"], "language": language, "lexical": floor, "lexical_score": _score(expect, floor)}
        if use_jev:
            try:
                reading = understand.typed(text, catalog, timeout=timeout)
            except jev.JevUnavailable as error:
                row["jev_error"] = str(error)
            else:
                merged = understand.with_typed(words, reading)
                selection = prefilter.select(catalog, text, merged, "jev", reading)
                time_choice = reading["time"]["choice"]
                got = {"design": reading["design"]["choice"],
                       "subjects": [s for s, p in reading["subjects"].items() if p >= understand.SUBJECT_MIN],
                       "shortlist": selection["variables"],
                       "time": None if time_choice == "none" else time_choice,
                       "place": reading["place"] >= 0.5, "in_scope": reading["in_scope"] >= 0.5}
                fast = service.fast_plan_fields(text, language, merged, reading, catalog)
                row.update(jev=got, jev_score=_score(expect, got), seconds=reading["seconds"],
                           input_tokens=reading["usage"].get("input_tokens"),
                           design_confidence=reading["design"]["confidence"],
                           design_probability=reading["design"]["probabilities"].get(reading["design"]["choice"]),
                           in_scope_p=reading["in_scope"], place_p=reading["place"],
                           jev_alone_shortlist=set(expect["shortlist"]) <= {
                               v for v, p in reading["variables"].items() if p >= prefilter.JEV_THRESHOLD},
                           shortlist_sizes=[len(lexical["variables"]), len(selection["variables"])],
                           fast_path=fast is not None,
                           fast_design=(fast or {}).get("design"), fast_variables=(fast or {}).get("variables"),
                           fast_period=(fast or {}).get("period"), fast_place=(fast or {}).get("place"),
                           fast_right=(fast["design"] in expect["designs"]
                                       and set(expect["shortlist"]) <= set(fast["variables"])) if fast else None)
        rows.append(row)
    return {"date": datetime.now(UTC).isoformat(timespec="seconds"), "rows": rows}


def _totals(rows: list[dict], key: str) -> dict:
    fields = ("design", "subjects", "shortlist", "time", "place", "in_scope")
    out = {}
    for field in fields:
        scored = [r[key][field] for r in rows if key in r and r[key][field] is not None]
        out[field] = f"{sum(scored)}/{len(scored)}" if scored else "n/a"
    return out


def _marks(score: dict) -> str:
    return "".join("·" if v is None else ("+" if v else "-") for v in score.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.assistant.jev_eval")
    parser.add_argument("--no-jev", action="store_true")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--reports", type=Path, default=HERE / "reports")
    args = parser.parse_args(argv)
    cases = yaml.safe_load((HERE / "jev_eval.yaml").read_text(encoding="utf-8"))["cases"]
    report = run(cases, not args.no_jev, args.timeout)
    rows = report["rows"]
    for r in rows:
        line = f"{r['id']} lex[{_marks(r['lexical_score'])}]"
        if "jev" in r:
            fast = f"yes {r['fast_design']} {'OK' if r['fast_right'] else 'WRONG'}" if r["fast_path"] else "no"
            line += (f" jev[{_marks(r['jev_score'])}]"
                     f" {r['jev']['design']:<9} conf {r['design_confidence']:.2f} scope {r['in_scope_p']:.2f}"
                     f" place {r['place_p']:.2f} {r['seconds']:.2f}s {r['input_tokens']} tok"
                     f" fast={fast}")
        elif "jev_error" in r:
            line += f" jev ERROR {r['jev_error']}"
        print(line)
    report["lexical_totals"] = _totals(rows, "lexical_score")
    report["jev_totals"] = _totals(rows, "jev_score")
    seconds = [r["seconds"] for r in rows if "seconds" in r]
    tokens = [r["input_tokens"] or 0 for r in rows if "input_tokens" in r]
    report["jev_seconds_median"] = statistics.median(seconds) if seconds else None
    report["jev_seconds_max"] = max(seconds) if seconds else None
    report["jev_errors"] = sum("jev_error" in r for r in rows)
    report["jev_input_tokens"] = sum(tokens)
    report["jev_cost_usd"] = sum(tokens) * JEV_PRICE_PER_TOKEN
    report["fast_path"] = sum(bool(r.get("fast_path")) for r in rows)
    report["fast_path_right"] = sum(bool(r.get("fast_right")) for r in rows)
    print("\nfields: design subjects shortlist time place in_scope")
    print("lexical", report["lexical_totals"])
    print("jev    ", report["jev_totals"], f"errors {report['jev_errors']}, median {report['jev_seconds_median']} s,"
          f" max {report['jev_seconds_max']} s, {report['jev_input_tokens']} input tokens,"
          f" {report['jev_cost_usd']:.6f} USD; fast path {report['fast_path']} (right {report['fast_path_right']})")
    args.reports.mkdir(parents=True, exist_ok=True)
    file = args.reports / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-jev-eval.json"
    file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
