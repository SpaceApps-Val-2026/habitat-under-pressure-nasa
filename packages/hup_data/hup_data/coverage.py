"""Coverage index: which files hold a variable for a rectangle and a period, and where they are.

A DuckDB table in memory, built from the manifest (manifest.jsonl and manifest.d/, see manifest.py). It is read
once and rebuilt only when the manifest changes.
Usage from the command line: python -m hup_data.coverage rebuild
"""
import sys
import threading

import duckdb
import yaml

from hup_data import manifest as manifest_file
from hup_data import paths

WORLD = [-180.0, -90.0, 180.0, 90.0]
_lock = threading.Lock()
_state: dict = {"con": None, "stamp": None, "rows": 0}


def _source_variables() -> dict[str, list[str]]:
    """Source id → variable ids, from catalog/variables.yaml. Empty while the catalog has not been written."""
    file = paths.CATALOG_DIR / "variables.yaml"
    if not file.is_file():
        return {}
    loaded = yaml.safe_load(file.read_text(encoding="utf-8")) or []
    variables = loaded.get("variables", []) if isinstance(loaded, dict) else loaded
    mapping: dict[str, list[str]] = {}
    for variable in variables:
        for source in variable.get("sources", []):
            mapping.setdefault(source, []).append(variable["id"])
    return mapping


def _tier(line: dict) -> str:
    if line.get("tier"):
        return line["tier"]
    return "ready" if line["path"].startswith("ready/") else "raw"


def _rows(source_variables: dict[str, list[str]]):
    """One row per file and variable. A file whose variables are unknown still gets one row, with no variable."""
    for line in manifest_file.lines():
        # No bbox: the whole world only for a `global` scope. A case file of unknown extent covers nothing, so the
        # plan says the package is missing instead of finding it in Nairobi (predownload.py bboxes fills them).
        west, south, east, north = line.get("bbox") or (WORLD if line.get("scope") == "global" else [None] * 4)
        period = line.get("period") or {}
        origin = "package" if line["path"].startswith("packages/") else "cache"
        for variable in line.get("variables") or source_variables.get(line["source"]) or [None]:
            yield (line["source"], variable, _tier(line), line["path"], line["format"], line.get("scope"),
                   west, south, east, north, period.get("start"), period.get("end"), origin)


def rebuild(source_variables: dict[str, list[str]] | None = None) -> int:
    """Rebuild the index from the manifest and return the number of rows.

    `source_variables` overrides the source → variables relation of the catalog; tests and exploration use it.
    """
    mapping = _source_variables() if source_variables is None else source_variables
    # Taken before reading: a line added while reading changes the stamp, so the next query rebuilds again.
    stamp = manifest_file.stamp()
    con = duckdb.connect(":memory:")
    con.execute(
        "CREATE TABLE coverage (source VARCHAR, variable VARCHAR, tier VARCHAR, path VARCHAR, format VARCHAR,"
        " scope VARCHAR, west DOUBLE, south DOUBLE, east DOUBLE, north DOUBLE, t_start DATE, t_end DATE,"
        " origin VARCHAR)"
    )
    rows = list(_rows(mapping))
    if rows:
        # One bulk load from a table in memory. Inserting row by row took half a minute for the 13,000 files of
        # the manifest, and it happened again after every file an analysis downloaded.
        import pandas as pd

        text = ["source", "variable", "tier", "path", "format", "scope"]
        frame = pd.DataFrame(rows, columns=[*text, "west", "south", "east", "north", "t_start", "t_end", "origin"])
        con.register("incoming", frame)
        con.execute(
            "INSERT INTO coverage SELECT " + ", ".join(f"CAST({name} AS VARCHAR)" for name in text)
            + ", CAST(west AS DOUBLE), CAST(south AS DOUBLE), CAST(east AS DOUBLE), CAST(north AS DOUBLE),"
              " CAST(CAST(t_start AS VARCHAR) AS DATE), CAST(CAST(t_end AS VARCHAR) AS DATE),"
              " CAST(origin AS VARCHAR) FROM incoming"
        )
        con.unregister("incoming")
    with _lock:
        _state.update(con=con, stamp=stamp, rows=len(rows))
    return len(rows)


def _connection():
    """The current index, rebuilt first if the manifest changed or the data folder is another one."""
    if _state["con"] is None or _state["stamp"] != manifest_file.stamp():
        rebuild()
    return _state["con"]


def query(variable: str, bbox: list[float], period: dict) -> list[dict]:
    """Files that hold `variable`, touch `bbox` (west, south, east, north) and overlap `period`.

    A file without a period in the manifest matches any period. An empty list means we hold nothing:
    the caller turns that into an absence, never into a zero.
    """
    west, south, east, north = bbox
    cursor = _connection().cursor()
    found = cursor.execute(
        "SELECT source, variable, tier, path, west, south, east, north, t_start, t_end, origin FROM coverage"
        " WHERE variable = ? AND west <= ? AND east >= ? AND south <= ? AND north >= ?"
        " AND (t_start IS NULL OR t_start <= CAST(? AS DATE)) AND (t_end IS NULL OR t_end >= CAST(? AS DATE))"
        " ORDER BY source, t_start, path",
        [variable, east, west, north, south, period["end"], period["start"]],
    ).fetchall()
    return [
        {"source": source, "variable": name, "tier": tier, "path": path, "bbox": [w, s, e, n],
         "period": {"start": start.isoformat(), "end": end.isoformat()} if start and end else None,
         "resolution_m": None, "origin": origin}
        for source, name, tier, path, w, s, e, n, start, end, origin in found
    ]


if __name__ == "__main__":
    if sys.argv[1:] != ["rebuild"]:
        sys.exit("usage: python -m hup_data.coverage rebuild")
    print(f"Coverage index: {rebuild()} rows from {paths.manifest()}")
