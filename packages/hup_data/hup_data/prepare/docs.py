"""Documents: every PDF is split into pages that can be searched, each with its document and its page number.

Input:  manifest lines with format pdf
Output: <data>/ready/docs/docs.sqlite with a full-text table (SQLite FTS5): source, document, page, text
A page whose text cannot be extracted (a scanned page) is stored empty and counted: it is never guessed.
"""
import sqlite3
from pathlib import Path

from pypdf import PdfReader

from hup_data import paths
from hup_data.prepare.common import Index, manifest_lines

LAYOUT = "1"


def database() -> Path:
    return paths.ready() / "docs" / "docs.sqlite"


def _connect() -> sqlite3.Connection:
    database().parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(database())
    con.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5("
        "source UNINDEXED, document UNINDEXED, page UNINDEXED, text, tokenize = \"unicode61 remove_diacritics 2\")"
    )
    return con


def build(force: bool = False) -> dict:
    """Index every PDF that is new or has changed. Returns what was done."""
    index = Index("docs", LAYOUT)
    done, kept, failed = [], [], {}
    con = _connect()
    try:
        for line in manifest_lines(formats={"pdf"}):
            document = Path(line["path"]).stem
            key = f"{line['source']}/{document}"
            if index.fresh(key, line["sha256"], database()) and not force:
                kept.append(key)
                continue
            try:
                reader = PdfReader(paths.data_dir() / line["path"])
                pages = [(page.extract_text() or "").strip() for page in reader.pages]
            except Exception as error:  # one unreadable document must not stop the others; it is reported
                failed[key] = f"{type(error).__name__}: {error}"[:300]
                print(f"  FAILED {key}: {failed[key]}", flush=True)
                continue
            with con:  # one transaction per document: it is replaced whole or not at all
                con.execute("DELETE FROM pages WHERE source = ? AND document = ?", (line["source"], document))
                rows = [(line["source"], document, number, text) for number, text in enumerate(pages, start=1)]
                con.executemany("INSERT INTO pages VALUES (?, ?, ?, ?)", rows)
            index.put(key, {"source": line["source"], "document": document, "from": line["path"],
                            "input_sha256": line["sha256"], "pages": len(pages),
                            "pages_without_text": sum(1 for text in pages if not text),
                            "license": line.get("license"), "url": line.get("ingest", {}).get("url")})
            done.append(key)
            print(f"  {key}: {len(pages)} pages", flush=True)
    finally:
        con.close()
    return {"rebuilt": bool(done), "n_files": len(done) + len(kept), "built": done, "kept": kept, "failed": failed}
