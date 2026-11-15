"""The catalog: catalog/*.yaml joined, validated and written as catalog/catalog.json.

    python -m hup_api.catalog build [--dir CATALOG_DIR] [--out FILE]

`load()` is the only way the rest of the code reads it. Layout: docs/paquetes/interfaces.md, section 6.
"""
import argparse
import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, SchemaError

from hup_api import contracts
from hup_api.library.folder import write_atomic
from hup_data import paths

FILES = ("sources", "variables", "operations", "chains", "norms", "archetypes", "blocks")
REQUIRED = ("sources", "variables", "operations")
LEGISLATION = "legislation"  # one mapping, not a list: cards, routes, disclaimer, visibility (catalog/legislation.yaml)


class CatalogError(Exception):
    """The YAML does not make a valid catalog. The message lists every problem, one per line."""


def _read(folder: Path) -> tuple[dict, list[str]]:
    problems = []
    version = folder / "version.txt"
    catalog = {"version": version.read_text(encoding="utf-8").strip() if version.is_file() else None}
    if not catalog["version"]:
        problems.append("version.txt: missing or empty")
    for key in FILES:
        file = folder / f"{key}.yaml"
        if not file.is_file():
            if key in REQUIRED:
                problems.append(f"{key}.yaml: missing")
            continue
        try:
            entries = yaml.safe_load(file.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            problems.append(f"{key}.yaml: not valid YAML: {error}")
            continue
        entries = [] if entries is None else entries
        if not isinstance(entries, list):
            problems.append(f"{key}.yaml: must be a list of entries")
            continue
        catalog[key] = entries
    file = folder / f"{LEGISLATION}.yaml"
    if file.is_file():
        try:
            legislation = yaml.safe_load(file.read_text(encoding="utf-8"))
        except yaml.YAMLError as error:
            problems.append(f"{LEGISLATION}.yaml: not valid YAML: {error}")
        else:
            if isinstance(legislation, dict):
                catalog[LEGISLATION] = legislation
            elif legislation is not None:
                problems.append(f"{LEGISLATION}.yaml: must be a mapping")
    return catalog, problems


def _entry(catalog: dict, key: str, index) -> str:
    """How a problem names its place: the file and the id of the entry."""
    if key == LEGISLATION:
        return f"{LEGISLATION}.yaml"
    entries = catalog.get(key, [])
    if isinstance(index, int) and index < len(entries) and isinstance(entries[index], dict):
        return f"{key}.yaml [{entries[index].get('id', f'entry {index + 1}')}]"
    return f"{key}.yaml"


def check(catalog: dict) -> list[str]:
    """Schema plus the cross-references of docs/contratos/validate.py. Empty when the catalog is coherent."""
    problems = []
    for error in contracts.validator("catalog").iter_errors(catalog):
        where = list(error.path)
        place = _entry(catalog, where[0], where[1] if len(where) > 1 else None) if where else "catalog"
        if where[:1] == ["version"]:
            place = "version.txt"
        rest = where[1:] if where[:1] == [LEGISLATION] else where[2:]
        problems.append(f"{place}: {'/'.join(map(str, rest)) or '(entry)'}: {error.message[:200]}")
    if problems:
        return problems

    for key in FILES:
        seen = set()
        for item in catalog.get(key, []):
            if item["id"] in seen:
                problems.append(f"{key}.yaml [{item['id']}]: duplicate id")
            seen.add(item["id"])

    sources = {s["id"] for s in catalog["sources"]}
    variables = {v["id"] for v in catalog["variables"]}
    operations = {o["id"] for o in catalog["operations"]}
    for v in catalog["variables"]:
        problems += [f"variables.yaml [{v['id']}]: source outside the catalog: {x}" for x in v["sources"]
                     if x not in sources]
    for o in catalog["operations"]:
        try:
            Draft202012Validator.check_schema(o["params"])
        except SchemaError as error:
            problems.append(f"operations.yaml [{o['id']}]: params is not a valid JSON Schema: {error.message[:200]}")
    for c in catalog.get("chains", []):
        problems += [f"chains.yaml [{c['id']}]: variable outside the catalog: {x}" for k in c["links"]
                     for x in (k["from"], k["to"]) if x not in variables]
    for n in catalog.get("norms", []):
        problems += [f"norms.yaml [{n['id']}]: variable outside the catalog: {x}" for x in n["applies_to"]
                     if x not in variables]
        if n["clause"]["document"] not in sources:
            problems.append(f"norms.yaml [{n['id']}]: document outside the catalog: {n['clause']['document']}")
        if n["reviewed_by"] and not n["clause"]["quote"]:
            problems.append(f"norms.yaml [{n['id']}]: marked as reviewed without a literal quote")
    for b in catalog.get("blocks", []):
        problems += [f"blocks.yaml [{b['id']}]: operation outside the catalog: {x}"
                     for x in b["needs"].get("operations", []) if x not in operations]
    if LEGISLATION in catalog:
        problems += _check_legislation(catalog[LEGISLATION])
    return problems


def _check_legislation(legislation: dict) -> list[str]:
    """Cross-references of legislation.yaml that the schema cannot express."""
    problems, where = [], f"{LEGISLATION}.yaml"
    general = [g["id"] for g in legislation["general_options"]]
    problems += [f"{where}: general_options: duplicate id {x}" for x in sorted({x for x in general
                                                                                 if general.count(x) > 1})]
    cards = [a["celex"] for a in legislation["acts"]]
    problems += [f"{where}: acts: duplicate celex {x}" for x in sorted({x for x in cards if cards.count(x) > 1})]
    routes = [r for a in legislation["acts"] for r in a["citizen_options"]] + legislation["general_options"]
    problems += [f"{where}: who without a label in who_labels: {x}"
                 for x in sorted({r["who"] for r in routes} - set(legislation["who_labels"]))]
    for act in legislation["acts"]:
        problems += [f"{where} [{act['celex']}]: general route outside general_options: {x}"
                     for x in act["general"] if x not in general]
        problems += [f"{where} [{act['celex']}]: also_for repeats a card: {x}"
                     for x in act.get("also_for", []) if x in cards]
    visibility = legislation.get("visibility", {})
    titles = [t["celex"] for t in visibility.get("title", [])]
    listed = titles + visibility.get("hidden", [])
    problems += [f"{where}: visibility lists an act that has a card: {x}" for x in listed if x in cards]
    problems += [f"{where}: visibility lists an act twice: {x}" for x in sorted({x for x in listed
                                                                                 if listed.count(x) > 1})]
    problems += [f"{where}: visibility [{t['celex']}]: under an act that is neither a card nor a title: {x}"
                 for t in visibility.get("title", []) for x in t.get("under", []) if x not in cards + titles]
    return problems


def _assemble(folder: Path) -> dict:
    catalog, problems = _read(folder)
    problems = problems or check(catalog)
    if problems:
        raise CatalogError("\n".join(f"{folder.name}/{p}" for p in problems))
    return catalog


def build(catalog_dir: Path | None = None, out: Path | None = None) -> dict:
    """Join the YAML, validate it and write catalog.json. Raises CatalogError and writes nothing if it fails."""
    folder = Path(catalog_dir or paths.CATALOG_DIR)
    catalog = _assemble(folder)
    text = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    write_atomic(Path(out or folder / "catalog.json"), text.encode("utf-8"))
    return catalog


def load(catalog_dir: Path | None = None) -> dict:
    """The built catalog. If catalog.json has not been built yet, it is built in memory from the YAML."""
    folder = Path(catalog_dir or paths.CATALOG_DIR)
    file = folder / "catalog.json"
    if file.is_file():
        return json.loads(file.read_text(encoding="utf-8"))
    return _assemble(folder)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hup_api.catalog")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("build", help="join catalog/*.yaml, validate and write catalog.json")
    command.add_argument("--dir", type=Path, default=None, help="catalog folder (default: catalog/)")
    command.add_argument("--out", type=Path, default=None, help="output file (default: <dir>/catalog.json)")
    args = parser.parse_args(argv)
    try:
        built = build(args.dir, args.out)
    except CatalogError as error:
        print(f"Catalog not built:\n{error}", file=sys.stderr)
        return 1
    counts = ", ".join(f"{len(built.get(k, []))} {k}" for k in FILES)
    counts += f", {len(built.get(LEGISLATION, {}).get('acts', []))} legislation cards"
    print(f"Catalog {built['version']} OK: {counts}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
