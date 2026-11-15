"""Library index (B8): list, filter and search the saved analyses.

An in-memory SQLite database with FTS5, rebuilt from the analysis folders at startup and whenever the set of
folders changes, and updated when an analysis finishes. It is never written to disk: in production the container
disk is lost on restart and SQLite on a mounted bucket is not reliable.

The text search covers the title, the summary and the findings, in every language the analysis has, without
accents and by word prefix, so "gigafactoria" finds "gigafactoría" and "valen" finds "Valencia".

Re-runs of one question are versions of one report: the list shows only the newest finished analysis of each group
(see group_key) unless history is asked for. Nothing is deleted; GET /api/analyses/{id}/versions lists the others.
"""
import json
import logging
import re
import sqlite3
import threading
from datetime import UTC, datetime

from hup_api.assistant.question_cache import normalize as normalize_question
from hup_api.library import folder
from hup_data import paths

log = logging.getLogger("hup_api.library")

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")  # {result.field}: filled by code, not words anybody searches for
_WORD = re.compile(r"\w+")

_lock = threading.Lock()
_state = {"root": None, "folders": None, "db": None}

_SCHEMA = """
create table analyses (id text primary key, sort_created text, status text, design text, featured integer,
                       item text, group_key text);
create index analyses_group on analyses (group_key, status, featured, sort_created);
create table variables (id text, variable text);
create virtual table texts using fts5 (id unindexed, body, tokenize = 'unicode61 remove_diacritics 2');
"""


def _text(value) -> dict | None:
    return value if isinstance(value, dict) and all(isinstance(v, str) for v in value.values()) else None


def item(analysis: dict) -> dict:
    """What the list returns for one analysis. A value the analysis does not have is null."""
    plan = analysis.get("plan") if isinstance(analysis.get("plan"), dict) else {}
    zone = plan.get("zone") if isinstance(plan.get("zone"), dict) else None
    return {
        "id": analysis["id"],
        "title": _text(analysis.get("title")),
        "summary": _text(analysis.get("summary")),
        "design": plan.get("design") if isinstance(plan.get("design"), str) else None,
        "zone": {"id": zone.get("id"), "name": zone.get("name"), "bbox": zone.get("bbox")} if zone else None,
        "created": analysis.get("created"),
        "status": analysis.get("status"),
        "featured": analysis.get("featured") is True,
    }


def group_key(analysis: dict) -> str | None:
    """Which analyses are versions of one report: same question (normalised as the question cache does), same zone
    and same design. Without question text (a manual plan) the plan fingerprint. None: a group of its own."""
    plan = analysis.get("plan") if isinstance(analysis.get("plan"), dict) else {}
    request = plan.get("request") if isinstance(plan.get("request"), dict) else {}
    question = normalize_question(request.get("text")) if isinstance(request.get("text"), str) else ""
    if question:
        zone = plan.get("zone") if isinstance(plan.get("zone"), dict) else {}
        place = zone.get("id") if isinstance(zone.get("id"), str) else json.dumps(zone.get("bbox"))
        return json.dumps(["q", question, place, plan.get("design")], ensure_ascii=False)
    fingerprint = analysis.get("plan_fingerprint")
    return json.dumps(["fp", fingerprint]) if isinstance(fingerprint, str) and fingerprint else None


def _body(analysis: dict) -> str:
    texts = [analysis.get("title"), analysis.get("summary")]
    texts += [f.get("text") for f in analysis.get("findings") or [] if isinstance(f, dict)]
    words = [v for t in texts if isinstance(t, dict) for v in t.values() if isinstance(v, str)]
    return _PLACEHOLDER.sub(" ", "\n".join(words))


def _sort_key(created) -> str:
    """Created as a sortable UTC text, whatever offset it was written with; unreadable dates go last."""
    try:
        return datetime.fromisoformat(str(created)).astimezone(UTC).isoformat()
    except ValueError:
        return ""


def _put(db: sqlite3.Connection, analysis: dict) -> bool:
    if not isinstance(analysis, dict) or not folder.valid_id(analysis.get("id") or ""):
        return False
    analysis_id = analysis["id"]
    _delete(db, analysis_id)
    row = item(analysis)
    if row["status"] == "cancelled":
        return False
    db.execute("insert into analyses values (?, ?, ?, ?, ?, ?, ?)",
               (analysis_id, _sort_key(row["created"]), row["status"], row["design"], int(row["featured"]),
                json.dumps(row, ensure_ascii=False), group_key(analysis)))
    plan = analysis.get("plan") if isinstance(analysis.get("plan"), dict) else {}
    variables = plan.get("variables") if isinstance(plan.get("variables"), list) else []
    db.executemany("insert into variables values (?, ?)",
                   [(analysis_id, v) for v in dict.fromkeys(variables) if isinstance(v, str)])
    db.execute("insert into texts values (?, ?)", (analysis_id, _body(analysis)))
    return True


def _delete(db: sqlite3.Connection, analysis_id: str) -> None:
    for table in ("analyses", "variables", "texts"):
        db.execute(f"delete from {table} where id = ?", (analysis_id,))  # noqa: S608 - fixed table names


def remove(analysis_id: str) -> None:
    """Forget a cancelled run even if a library query indexed it while it was in progress."""
    with _lock:
        if (db := _state["db"]) is not None and _state["root"] == str(paths.analyses()):
            _delete(db, analysis_id)
            db.commit()


def _read(analysis_id: str) -> dict | None:
    try:
        return folder.read_analysis(analysis_id)
    except (OSError, ValueError):
        log.warning("library: analysis %s could not be read", analysis_id)
        return None


def rebuild() -> int:
    """A new in-memory index from every analysis folder. Returns how many analyses it holds."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.executescript(_SCHEMA)
    with _lock:
        names = folder.ids()
        count = sum(_put(db, _read(name)) for name in names)
        db.commit()
        old = _state["db"]
        _state.update(root=str(paths.analyses()), folders=names, db=db)
    if old is not None:
        old.close()
    return count


def _ensure() -> None:
    """Rebuild when the store moved (HUP_DATA_DIR changed) or a folder appeared or went away."""
    if _state["db"] is None or _state["root"] != str(paths.analyses()) or _state["folders"] != folder.ids():
        rebuild()


def record(analysis: dict) -> None:
    """An analysis was written (it finished, or changed): the list shows it as it is now."""
    _ensure()
    with _lock:
        _put(_state["db"], analysis)
        _state["db"].commit()


def _match(q: str) -> str | None:
    """Every word of the query, as a quoted prefix; FTS5 operators in the query are just words."""
    words = _WORD.findall(q or "")
    return " ".join(f'"{w}"*' for w in words) or None


# A finished, not featured analysis of a group; `b` is another one of the same group.
_PEER = "b.group_key = a.group_key and b.status = 'finished' and b.featured = 0"
_NEWER = f"exists (select 1 from analyses b where {_PEER} and (b.sort_created, b.id) > (a.sort_created, a.id))"
_VERSIONS = (f"case when a.status = 'finished' and a.featured = 0 "
             f"then (select count(*) from analyses b where {_PEER}) end")


def query(q: str | None = None, design: str | None = None, variable: str | None = None,
          featured: bool | None = None, status: str | None = None, limit: int = 50,
          history: bool = False) -> list[dict]:
    """Featured first, then newest first. Failed analyses only when status asks for them.

    Without history, a finished analysis is left out when a newer finished one of its group exists (re-runs of the
    same question); the one shown carries `versions` when its group has more than one finished analysis. Featured
    analyses are curated: they are never hidden and never hide others. Running, planned and failed ones are never
    hidden either, so a re-run in progress does not take the finished report off the list."""
    sql, args = [f"select item, {_VERSIONS} from analyses a where"], []
    if status:
        sql.append("a.status = ?")
        args.append(status)
    else:
        sql.append("a.status is not 'failed'")
    if design:
        sql.append("and a.design = ?")
        args.append(design)
    if featured is not None:
        sql.append("and a.featured = ?")
        args.append(int(featured))
    if variable:
        sql.append("and exists (select 1 from variables v where v.id = a.id and v.variable = ?)")
        args.append(variable)
    match = _match(q)
    if match:
        sql.append("and a.id in (select id from texts where texts match ?)")
        args.append(match)
    if not history:
        sql.append(f"and not (a.status = 'finished' and a.featured = 0 and a.group_key is not null and {_NEWER})")
    sql.append("order by a.featured desc, a.sort_created desc, a.id limit ?")
    args.append(int(limit))
    _ensure()
    with _lock:
        rows = _state["db"].execute(" ".join(sql), args).fetchall()
    return [{**json.loads(item), **({"versions": count} if count and count > 1 else {})} for item, count in rows]


def versions(analysis_id: str) -> list[dict]:
    """The other analyses of the group of this one (any status the index holds), newest first."""
    _ensure()
    with _lock:
        row = _state["db"].execute("select group_key from analyses where id = ?", (analysis_id,)).fetchone()
    key = row[0] if row else None
    if row is None and (analysis := _read(analysis_id)) is not None:  # not in the index: a cancelled run
        key = group_key(analysis)
    if key is None:
        return []
    with _lock:
        rows = _state["db"].execute("select item from analyses where group_key = ? and id != ? "
                                    "order by sort_created desc, id desc", (key, analysis_id)).fetchall()
    return [{k: v for k, v in json.loads(r[0]).items() if k in ("id", "created", "status")} for r in rows]
