"""The single quality gate. Runs every check in order and stops at the first failure.

Usage: python tools/gate.py [--only python | --only web]
Order: contracts, ruff, pytest, then in apps/web: tsc, eslint, vitest.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
NPM = shutil.which("npm") or "npm"
# The project environment is created by tools/setup_env.py; nothing is installed on the system.
BIN = ROOT / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
PYTHON = BIN / ("python.exe" if sys.platform == "win32" else "python")

PYTHON_STEPS = [
    ("contracts", [str(PYTHON), "-X", "utf8", "docs/contratos/validate.py"], ROOT),
    ("ruff", [str(PYTHON), "-m", "ruff", "check", "."], ROOT),
    ("pytest", [str(PYTHON), "-m", "pytest", "-q"], ROOT),
]
WEB_STEPS = [
    ("tsc", [NPM, "run", "typecheck"], WEB),
    ("eslint", [NPM, "run", "lint"], WEB),
    ("vitest", [NPM, "run", "test"], WEB),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=["python", "web"])
    only = parser.parse_args().only
    steps = (PYTHON_STEPS if only != "web" else []) + (WEB_STEPS if only != "python" else [])
    if only != "web" and not PYTHON.is_file():
        print("GATE FAILED: there is no .venv; create it with: python tools/setup_env.py", flush=True)
        return 1
    for name, command, cwd in steps:
        print(f"== {name}", flush=True)
        code = subprocess.run(command, cwd=cwd).returncode
        if code != 0:
            print(f"GATE FAILED at {name} (exit {code})", flush=True)
            return code
    print(f"GATE OK: {', '.join(name for name, _, _ in steps)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
