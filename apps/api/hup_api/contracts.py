"""JSON Schema validators for the contracts in docs/contratos, read through hup_data.paths.CONTRACTS_DIR."""
import json
from functools import cache

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from hup_data import paths


@cache
def _registry() -> Registry:
    schemas = [json.loads(p.read_text(encoding="utf-8")) for p in paths.CONTRACTS_DIR.glob("*.schema.json")]
    return Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas)


@cache
def validator(name: str) -> Draft202012Validator:
    """Validator for one contract: "catalog", "plan", "event", "result", "analysis" or "manifest"."""
    schema = json.loads((paths.CONTRACTS_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=_registry(), format_checker=FormatChecker())


def errors(name: str, obj) -> list[str]:
    """Every schema error of `obj`, as "path: message"; empty when it validates."""
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message[:200]}" for e in validator(name).iter_errors(obj)]
