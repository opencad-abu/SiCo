"""Build one local lexical index and atomically publish its complete generation."""

import json
import os
from pathlib import Path
import tempfile
import time

from ..storage.journal import sync_directory
from .catalog import connection, directory, metadata, source_key, writer
from .contracts import KnowledgeError, MAX_CHUNKS, checkpoint, digest, terms
from .local_files import read_bytes, scan
from .parsing import extract

SCHEMA = """
CREATE TABLE metadata (value TEXT NOT NULL);
CREATE TABLE documents (name TEXT PRIMARY KEY, version TEXT NOT NULL, issues TEXT NOT NULL);
CREATE TABLE chunks (id TEXT PRIMARY KEY, document TEXT NOT NULL, version TEXT NOT NULL,
                     text TEXT NOT NULL, location TEXT NOT NULL, length INTEGER NOT NULL);
CREATE TABLE postings (term TEXT NOT NULL, chunk TEXT NOT NULL, PRIMARY KEY (term, chunk));
CREATE INDEX chunk_postings ON postings(chunk);
CREATE TABLE frequencies (term TEXT PRIMARY KEY, count INTEGER NOT NULL);
"""


def build(project, source, cancelled=lambda: False, deadline=None):
    deadline = deadline or time.monotonic() + 90
    path = directory(project, source, create=True)
    with writer(path):
        return _build(path, source, cancelled, deadline)


def _build(path, source, cancelled, deadline):
    prior = connection(path / "index.sqlite3", readonly=True) if (path / "index.sqlite3").exists() else None
    fd, name = tempfile.mkstemp(prefix="generation-", suffix=".sqlite3", dir=path)
    os.close(fd)
    temporary = Path(name)
    con = connection(temporary)
    try:
        resolved, files = scan(source, cancelled, deadline)
        old = {r["name"]: r for r in prior.execute("SELECT * FROM documents")} if prior else {}
        con.executescript(SCHEMA)
        reused, parsed, count, issues = 0, 0, 0, []
        for relative, version, data in files:
            checkpoint(cancelled, deadline)
            if relative in old and old[relative]["version"] == version:
                doc_issues = json.loads(old[relative]["issues"])
                for row in prior.execute("SELECT * FROM chunks WHERE document=?", (relative,)):
                    con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)", tuple(row))
                    con.executemany("INSERT INTO postings VALUES (?,?)", prior.execute(
                        "SELECT term,chunk FROM postings WHERE chunk=?", (row["id"],)))
                    count += 1
                reused += 1
            else:
                values, doc_issues = extract(relative, data, cancelled, deadline)
                for position, value in enumerate(values):
                    checkpoint(cancelled, deadline)
                    identity = digest([source_key(source), relative, version, position, value])
                    tokens = terms(value["text"])
                    location = {"path": relative, **{k: value[k] for k in ("start", "end", "page")}}
                    con.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?)", (
                        identity, relative, version, value["text"], json.dumps(location), len(tokens)))
                    con.executemany("INSERT INTO postings VALUES (?,?)", [(t, identity) for t in tokens])
                count += len(values)
                parsed += 1
            if count > MAX_CHUNKS:
                raise KnowledgeError("index_limit", "资料片段超过 32768 项，请缩小范围")
            issues.extend(doc_issues[:32])
            con.execute("INSERT INTO documents VALUES (?,?,?)", (relative, version, json.dumps(doc_issues[:32])))
        # Do not publish a generation built from files that changed during extraction.
        for relative, version, _data in files:
            checkpoint(cancelled, deadline)
            if digest(read_bytes(source, relative)) != version:
                raise KnowledgeError("source_changed", "构建期间资料发生变化，上一索引保留")
        summary = {"version": 1, "root": resolved, "definition": source_key(source),
                   "generation": digest([[r, v] for r, v, _ in files]),
                   "documents": len(files), "chunks": count, "updated_at": time.time(),
                   "issues": issues[:32], "issue_count": len(issues), "parsed": parsed,
                   "reused": reused, "removed": len(set(old) - {r for r, _v, _d in files})}
        con.execute("INSERT INTO metadata VALUES (?)", (json.dumps(summary),))
        con.execute("INSERT INTO frequencies SELECT term,COUNT(*) FROM postings GROUP BY term")
        con.commit()
        con.close()
        checkpoint(cancelled, deadline)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, path / "index.sqlite3")
        sync_directory(path)
        return {"status": "partial" if issues else "ready", **summary}
    finally:
        con.close()
        if prior:
            prior.close()
        temporary.unlink(missing_ok=True)


def status(project, source, cancelled=lambda: False, deadline=None):
    deadline = deadline or time.monotonic() + 20
    path = directory(project, source) / "index.sqlite3"
    if not path.exists():
        return {"status": "pending_index", "message": "尚未索引，请更新索引"}
    with connection(path, readonly=True) as con:
        value = metadata(con)
    resolved, files = scan(source, cancelled, deadline)
    if resolved != value["root"]:
        raise KnowledgeError("path_changed", "知识源解析路径已变化，请重新索引")
    stale = value["generation"] != digest([[name, version] for name, version, _data in files])
    return {**value, "status": "stale" if stale else "partial" if value["issues"] else "ready",
            "message": "资料有变化，请更新索引" if stale else ""}
