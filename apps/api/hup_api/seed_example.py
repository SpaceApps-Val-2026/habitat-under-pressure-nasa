"""Copy the contract examples into <data>/analyses/<id>/, so the web can open a saved analysis.

    python -m hup_api.seed_example
"""
import json
import shutil

from hup_api.library import folder
from hup_data import paths

EXAMPLES = paths.CONTRACTS_DIR / "examples"


def seed() -> str:
    """Write plan.json, analysis.json, results.json and events.jsonl of the example. Returns its analysis id."""
    analysis = json.loads((EXAMPLES / "analysis.json").read_text(encoding="utf-8"))
    analysis_id = analysis["id"]
    target = folder.directory(analysis_id)
    target.mkdir(parents=True, exist_ok=True)
    for name in ("plan.json", "results.json", "analysis.json"):
        folder.write_json(analysis_id, name, json.loads((EXAMPLES / name).read_text(encoding="utf-8")))
    events = target / "events.jsonl"
    tmp = target / ".events.jsonl.seed"
    shutil.copyfile(EXAMPLES / "events.jsonl", tmp)
    tmp.replace(events)
    return analysis_id


if __name__ == "__main__":
    print(f"Seeded analyses/{seed()}")
