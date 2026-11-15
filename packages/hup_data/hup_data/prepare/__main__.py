"""Usage: python -m hup_data.prepare <climate | vectors | population | docs | all> [--force]"""
import sys
import time

from hup_data.prepare import climate, docs, unpack, vectors

TASKS = {"climate": climate.build, "vectors": vectors.build, "population": unpack.build, "docs": docs.build}


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in (*TASKS, "all"):
        print(__doc__)
        return 2
    code = 0
    for name in (TASKS if argv[0] == "all" else [argv[0]]):
        start = time.perf_counter()
        result = TASKS[name](force="--force" in argv[1:])
        state = "built" if result["rebuilt"] else "already up to date"
        print(f"{name}: {state} in {time.perf_counter() - start:.0f} s - {result['n_files']} artefacts", flush=True)
        for key, reason in result.get("failed", {}).items():
            print(f"{name}: FAILED {key}: {reason}", flush=True)
            code = 1
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
