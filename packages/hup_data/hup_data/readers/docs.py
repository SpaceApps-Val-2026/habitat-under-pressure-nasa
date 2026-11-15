"""Search in the prepared documents: pages that match some words, each with its document and page number."""
import re
import sqlite3

from hup_data.prepare import docs as prepared
from hup_data.prepare.common import read_index


def documents() -> dict[str, dict]:
    """Indexed documents on this computer: "<source>/<document>" → pages, licence, url. Empty if none."""
    return read_index("docs")


def search(words: str, source: str | None = None, document: str | None = None, limit: int = 10) -> list[dict]:
    """Best-matching pages for `words` (all of them must appear), with a short literal excerpt.

    Each hit: source, document, page, excerpt. The excerpt is text copied from the page, never rewritten.
    An empty list means no page matches, or that the documents have not been prepared on this computer.
    """
    terms = re.findall(r"\w+", words, flags=re.UNICODE)
    if not terms or not prepared.database().is_file():
        return []
    match = " ".join(f'"{term}"' for term in terms)  # quoted: user text never becomes query syntax
    sql = ("SELECT source, document, page, snippet(pages, 3, '', '', ' … ', 40) FROM pages WHERE pages MATCH ?"
           + (" AND source = ?" if source else "") + (" AND document = ?" if document else "")
           + " ORDER BY bm25(pages) LIMIT ?")
    args = [match] + ([source] if source else []) + ([document] if document else []) + [int(limit)]
    con = sqlite3.connect(f"file:{prepared.database().as_posix()}?mode=ro", uri=True)
    try:
        found = con.execute(sql, args).fetchall()
        return [{"source": s, "document": d, "page": int(p), "excerpt": text} for s, d, p, text in found]
    finally:
        con.close()
