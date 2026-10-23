"""Persist bounded, versioned remote evidence without granting arbitrary URL reads."""

import json
import time

from .catalog import connection, directory, writer
from .contracts import KnowledgeError, digest

TTL = 7 * 86400


def remember(project, source, rows):
    path = directory(project, source, create=True) / "evidence.sqlite3"
    with writer(path.parent), connection(path) as con:
        con.execute("CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, created REAL, value TEXT)")
        con.execute("DELETE FROM evidence WHERE created < ?", (time.time() - TTL,))
        for row in rows:
            row["evidence_id"] = digest(row)
            con.execute("INSERT OR REPLACE INTO evidence VALUES (?,?,?)",
                        (row["evidence_id"], time.time(), json.dumps(row, ensure_ascii=False)))
        con.execute("DELETE FROM evidence WHERE id NOT IN (SELECT id FROM evidence ORDER BY created DESC LIMIT 1024)")
    return rows


def lookup(project, source, identity):
    path = directory(project, source) / "evidence.sqlite3"
    if not path.exists():
        raise KnowledgeError("stale_reference", "请先检索以取得引用标识")
    with connection(path, readonly=True) as con:
        row = con.execute("SELECT value FROM evidence WHERE id=? AND created>=?",
                          (identity, time.time() - TTL)).fetchone()
    if row is None:
        raise KnowledgeError("stale_reference", "引用不存在或已过期，请重新检索")
    return json.loads(row[0])
