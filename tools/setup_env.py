"""Create the local Python environment without installing anything on the system.

Usage: python tools/setup_env.py        (any Python 3.10 or newer starts it)
Everything lands in three folders of this checkout, all ignored by Git:
  .venv-tools/   uv, pinned
  .venv-python/  the Python 3.12 interpreter that uv downloads
  .venv/         the project environment, from uv.lock
Safe to run again. The web dependencies are separate: npm --prefix apps/web ci
"""
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".venv-tools"
PYTHONS = ROOT / ".venv-python"
BIN = TOOLS / ("Scripts" if os.name == "nt" else "bin")
UV = BIN / ("uv.exe" if os.name == "nt" else "uv")
UV_VERSION = "0.13.8"


def env() -> dict:
    """uv keeps its interpreters inside the checkout and creates no launchers elsewhere."""
    return {**os.environ, "UV_PYTHON_INSTALL_DIR": str(PYTHONS), "UV_PYTHON_INSTALL_BIN": "0"}


def main() -> int:
    if not UV.is_file():
        venv.create(TOOLS, with_pip=True)
        python = BIN / ("python.exe" if os.name == "nt" else "python")
        subprocess.check_call([str(python), "-m", "pip", "install", "--quiet", f"uv=={UV_VERSION}"])
    subprocess.check_call([str(UV), "python", "install", "3.12"], cwd=ROOT, env=env())
    # Passing the interpreter explicitly avoids a uv failure on Windows when it cannot create its version link.
    name = "python.exe" if os.name == "nt" else "bin/python3.12"
    found = sorted(PYTHONS.glob(f"cpython-3.12.*/{name}"))
    if not found:
        print(f"No Python 3.12 under {PYTHONS}", file=sys.stderr)
        return 1
    return subprocess.call([str(UV), "sync", "--python", str(found[-1])], cwd=ROOT, env=env())


if __name__ == "__main__":
    sys.exit(main())
