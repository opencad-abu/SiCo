"""Rank local postings and read only version-checked evidence identifiers."""

import json
from pathlib import Path

from .catalog import connection, directory, metadata, source_key
from .contracts import KnowledgeError, checkpoint, clipped, digest
from .local_files import read_bytes, root, scan


def evidence(source, row):
    location = json.loads(row["location"])
    base = Path(source["path"])
    path = base if base.is_file() else base / row["document"]
    anchor = "page=" + str(location["page"]) if location["page"] else "L" + str(location["start"])
    return {"source_id": source["id"], "document_id": digest([source_key(source), row["document"]]),
            "chunk_id": row["id"], "evidence_id": row["id"], "title": row["document"],
            "excerpt": clipped(row["text"], 800), "location": location,
            "uri": path.as_uri() + "#" + anchor, "version": row["version"]}


def search(project, source, tokens, limit, cancelled, deadline):
    checkpoint(cancelled, deadline)
    with connection(directory(project, source) / "index.sqlite3", readonly=True) as con:
        meta = metadata(con)
        if str(root(source)) != meta["root"]:
            raise KnowledgeError("path_changed", "知识源解析路径已变化，请重新索引")
        placeholders = ",".join("?" for _ in tokens)
        # Weighted coverage with inverse document frequency; scores remain local to this source.
        query = ("SELECT c.*,SUM((CASE WHEN length(p.term)>1 THEN 2.0 ELSE 0.25 END) / "
                 "f.count) / (1.0+c.length/400.0) AS rank FROM postings p "
                 "JOIN frequencies f ON p.term=f.term JOIN chunks c ON c.id=p.chunk "
                 "WHERE p.term IN (" + placeholders + ") GROUP BY c.id ORDER BY rank DESC,c.id LIMIT ?")
        rows = list(con.execute(query, (*tokens, limit * 4)))
        result, seen, changed = [], set(), False
        for row in rows:
            checkpoint(cancelled, deadline)
            if row["document"] in seen:
                continue
            seen.add(row["document"])
            try:
                stale = digest(read_bytes(source, row["document"])) != row["version"]
            except (OSError, KnowledgeError):
                stale = True
            changed |= stale
            if not stale:
                result.append(evidence(source, row))
            if len(result) >= limit:
                break
        # Include additions/deletions, not only changes to currently matching files.
        resolved, current = scan(source, cancelled, deadline)
        changed |= meta["generation"] != digest([[name, version] for name, version, _data in current])
        changed |= resolved != meta["root"]
        return result, {"status": "stale" if changed else "partial" if meta["issues"] else "ready",
                        "generation": meta["generation"], "issues": meta["issues"],
                        "message": "资料有变化，请更新索引" if changed else ""}


def read(project, source, identity, cancelled, deadline):
    checkpoint(cancelled, deadline)
    with connection(directory(project, source) / "index.sqlite3", readonly=True) as con:
        if str(root(source)) != metadata(con)["root"]:
            raise KnowledgeError("path_changed", "知识源解析路径已变化，请重新索引")
        row = con.execute("SELECT * FROM chunks WHERE id=?", (identity,)).fetchone()
        if row is None:
            raise KnowledgeError("stale_reference", "引用不在当前索引中，请重新检索")
        try:
            current = digest(read_bytes(source, row["document"]))
        except (OSError, KnowledgeError):
            raise KnowledgeError("stale_reference", "原文已移走或不可读取，请重新索引") from None
        if current != row["version"]:
            raise KnowledgeError("stale_reference", "原文版本已变化，请重新索引并检索")
        return {**evidence(source, row), "text": row["text"], "verified": True}
