"""Validate the examples against the schemas and check the cross-references.

Usage: python docs/contratos/validate.py     (needs the `jsonschema` package)
Exits with code 1 if anything fails. This is the quality gate for the contracts.
"""
import json
import re
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = Path(__file__).parent
EX = HERE / "examples"
schemas = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in HERE.glob("*.schema.json")}
registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())
failures = []


def read(name):
    text = (EX / name).read_text(encoding="utf-8")
    if name.endswith(".jsonl"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def validate(schema, objects, origin):
    Draft202012Validator.check_schema(schemas[schema])
    v = Draft202012Validator(schemas[schema], registry=registry, format_checker=FormatChecker())
    for i, obj in enumerate(objects):
        for err in v.iter_errors(obj):
            failures.append(f"{origin}[{i}] {'/'.join(map(str, err.path))}: {err.message[:160]}")


catalog, plan, results, analysis = read("catalog.json"), read("plan.json"), read("results.json"), read("analysis.json")
events, manifest = read("events.jsonl"), read("manifest.jsonl")
validate("catalog.schema.json", [catalog], "catalog")
validate("plan.schema.json", [plan], "plan")
validate("result.schema.json", results, "results")
validate("analysis.schema.json", [analysis], "analysis")
validate("event.schema.json", events, "events")
validate("manifest.schema.json", manifest, "manifest")

# Cross-references: what a schema alone cannot check.
operations = {o["id"]: o for o in catalog["operations"]}
sources = {s["id"] for s in catalog["sources"]}
variables = {v["id"] for v in catalog["variables"]}
steps = {s["id"] for s in plan["steps"]}
by_id = {r["id"]: r for r in results}

for s in plan["steps"]:
    if s["operation"] not in operations:
        failures.append(f"plan: step {s['id']} uses an operation that is not in the catalog: {s['operation']}")
    else:
        for err in Draft202012Validator(operations[s["operation"]]["params"]).iter_errors(s["params"]):
            failures.append(f"plan: params of step {s['id']}: {err.message[:160]}")
        op = operations[s["operation"]]
        if (s["type"], s["version"]) != (op["type"], op["version"]):
            failures.append(f"plan: step {s['id']} does not match the type or version of {op['id']} in the catalog")
    failures += [f"plan: {s['id']} depends on a missing step: {d}" for d in s["depends_on"] if d not in steps]
    failures += [f"plan: {s['id']} cites a source outside the catalog: {x}" for x in s.get("sources", []) if x not in sources]
failures += [f"plan: variable outside the catalog: {v}" for v in plan["variables"] if v not in variables]

for r in results:
    if r["step"] not in steps:
        failures.append(f"result {r['id']}: missing step {r['step']}")
    failures += [f"result {r['id']}: source outside the catalog: {x['source']}" for x in r["sources"] if x["source"] not in sources]

    if r["analysis"] != analysis["id"]:
        failures.append(f"result {r['id']}: belongs to another analysis")
    # A result cannot claim more evidence than its operation allows; the reviewer can only lower it.
    ceiling = operations.get(r["method"]["operation"], {}).get("evidence_level")
    if "evidence_level" in r and (ceiling is None or r["evidence_level"] > ceiling):
        failures.append(f"result {r['id']}: evidence level {r['evidence_level']} is above its operation ({ceiling})")
    failures += [f"result {r['id']}: diagnostic not declared by its operation: {d}" for d in r.get("diagnostics", {})
                 if d not in operations.get(r["method"]["operation"], {}).get("diagnostics", [])]

# Data class: a figure cannot look more direct than what it is made of. A reading copies the class of its
# variable; a computation is never more direct than the variable it treats.
RANK = ["measured", "reanalysis", "extrapolated", "approximated", "illustrative"]


def rank(data_class):
    """Position in RANK; an unknown value has already been reported by the schema."""
    return RANK.index(data_class) if data_class in RANK else None


variable_class = {v["id"]: v.get("data_class", "measured") for v in catalog["variables"]}
for r in results:
    op = operations.get(r["method"]["operation"], {})
    declared, own = op.get("output", {}).get("data_class"), variable_class.get(r["variable"], "measured")
    if declared == "inherit" and op.get("type") == "ingest" and r["data_class"] != own:
        failures.append(f"result {r['id']}: a reading of {r['variable']} must be {own}, not {r['data_class']}")
    elif declared not in (None, "inherit") and r["data_class"] != declared:
        failures.append(f"result {r['id']}: its operation produces {declared}, not {r['data_class']}")
    mine, floor = rank(r["data_class"]), rank(own)
    if None not in (mine, floor) and mine < floor:
        failures.append(f"result {r['id']}: {r['data_class']} is more direct than its variable ({own})")

# The catalog must be coherent with itself: every card, link and rule points at something that exists.
for v in catalog["variables"]:
    failures += [f"variable {v['id']}: source outside the catalog: {x}" for x in v["sources"] if x not in sources]
for v in catalog["variables"]:
    if "baseline" in v and v["baseline"]["variable"] not in variables:
        failures.append(f"variable {v['id']}: baseline outside the catalog: {v['baseline']['variable']}")
for c in catalog.get("chains", []):
    failures += [f"chain {c['id']}: variable outside the catalog: {x}" for k in c["links"] for x in (k["from"], k["to"])
                 if x not in variables]
for n in catalog.get("norms", []):
    failures += [f"norm {n['id']}: variable outside the catalog: {x}" for x in n["applies_to"] if x not in variables]
    if n["clause"]["document"] not in sources:
        failures.append(f"norm {n['id']}: document outside the catalog: {n['clause']['document']}")
    if n["reviewed_by"] and not n["clause"]["quote"]:
        failures.append(f"norm {n['id']}: marked as reviewed without a literal quote")
blocks = {b["id"]: b for b in catalog.get("blocks", [])}
norms = {n["id"] for n in catalog.get("norms", [])}
for b in blocks.values():
    failures += [f"block {b['id']}: operation outside the catalog: {x}" for x in b["needs"].get("operations", [])
                 if x not in operations]


def check_text(origin, texts, allowed):
    """Figures are placeholders filled by code. Outside them only years are allowed."""
    for language, text in texts.items():
        if re.search(r"\d", re.sub(r"\{[^}]*\}|\b(19|20)\d\d\b", "", text)):
            failures.append(f"{origin} ({language}): hand-written figure; it must be a {{result.field}} placeholder")
        for rid, field in re.findall(r"\{([^.}]+)\.([^}]+)\}", text):
            if rid not in allowed or field not in by_id.get(rid, {}):
                failures.append(f"{origin} ({language}): placeholder {{{rid}.{field}}} cannot be filled")


failures += [f"analysis: missing result {x}" for x in analysis["results"] if x not in by_id]
findings = {f["id"] for f in analysis["findings"]}
for f in analysis["findings"]:
    failures += [f"finding {f['id']}: missing result {x}" for x in f["results"] if x not in by_id]
    check_text(f"finding {f['id']}", f["text"], f["results"])
    # The sentence cannot claim more than the best-supported of its results.
    levels = [by_id[x]["evidence_level"] for x in f["results"] if "evidence_level" in by_id.get(x, {})]
    if "evidence_level" in f and (not levels or f["evidence_level"] > max(levels)):
        failures.append(f"finding {f['id']}: evidence level {f['evidence_level']} is above its results")
    # Nor can it present as more direct what its results are.
    worst = max((rank(by_id[x]["data_class"]) or 0 for x in f["results"] if x in by_id), default=0)
    if rank(f["data_class"]) is not None and rank(f["data_class"]) < worst:
        failures.append(f"finding {f['id']}: presented as {f['data_class']}, but its results are {RANK[worst]}")
failures += [f"alert {a['rule']}: missing result {a['result']}" for a in analysis["alerts"] if a["result"] not in by_id]

for style, story in analysis.get("story", {}).items():
    for i, b in enumerate(story):
        origin = f"story {style}[{i}] {b['block']}"
        used = [by_id[x] for x in b.get("results", []) if x in by_id]
        failures += [f"{origin}: missing result {x}" for x in b.get("results", []) if x not in by_id]
        if b.get("finding") and b["finding"] not in findings:
            failures.append(f"{origin}: missing finding {b['finding']}")
        if b.get("norm") and b["norm"] not in norms:
            failures.append(f"{origin}: norm outside the catalog: {b['norm']}")
        check_text(origin, b.get("text", {}), b.get("results", []))
        if b["block"] not in blocks:
            failures.append(f"{origin}: block outside the catalog")
            continue
        if style not in blocks[b["block"]]["styles"]:
            failures.append(f"{origin}: the block is not declared for this style")
        # A block is only allowed when its data exists. An absence shows as an absence, so it only fits LimitsBox.
        needs = blocks[b["block"]]["needs"]
        if needs and len(used) < needs.get("min_results", 1):
            failures.append(f"{origin}: the block needs results and has none")
        for r in used:
            if needs.get("result_types") and (r["type"] not in needs["result_types"] or "absence" in r):
                failures.append(f"{origin}: result {r['id']} is not a {' or '.join(needs['result_types'])} with data")
            failures += [f"{origin}: result {r['id']} lacks {x}" for x in needs.get("fields", []) if x not in r]
            if needs.get("operations") and r["method"]["operation"] not in needs["operations"]:
                failures.append(f"{origin}: result {r['id']} does not come from {needs['operations']}")

if [e["seq"] for e in events] != list(range(1, len(events) + 1)):
    failures.append("events: seq is not consecutive from 1")
if events[-1]["type"] != "analysis_finished":
    failures.append("events: the last event must be analysis_finished")
failures += [f"event {e['seq']}: missing step {e['step']}" for e in events if e.get("step") and e["step"] not in steps]
for e in events:
    cited = e["data"].get("results", []) + ([e["data"]["result"]] if e["type"] in ("partial_result", "alert") else [])
    failures += [f"event {e['seq']}: missing result {x}" for x in cited if x not in by_id]

print("\n".join(failures) if failures else f"Contracts OK: {len(schemas)} schemas, 6 examples, {len(events)} events.")
sys.exit(1 if failures else 0)
