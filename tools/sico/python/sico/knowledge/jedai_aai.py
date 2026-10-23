"""JedAI AAI MCP adapter: category-scoped vector search with verified citations."""

import json
from urllib.parse import urlsplit

from .catalog import source_key
from .contracts import KnowledgeError, checkpoint, clipped, digest
from .credentials import token
from .remote_evidence import lookup, remember
from .remote_http import McpHttp

SCHEME = "cda://"
MAX_CANDIDATES = 50
MAX_READ_CANDIDATES = 16


def valid_link(value):
    """JedAI cites internal cda:// documents and http(s) source links; links are never fetched."""
    if (not isinstance(value, str) or not 0 < len(value) <= 2048
            or any(c.isspace() or ord(c) < 32 for c in value)):
        return False
    if value.startswith(SCHEME):
        return len(value) > len(SCHEME)
    parsed = urlsplit(value)
    return (parsed.scheme in {"http", "https"} and bool(parsed.hostname)
            and parsed.username is None and parsed.password is None)


def client(source, environment, cancelled, deadline):
    return McpHttp(source, token(source, environment), environment, cancelled, deadline)


def categories(remote):
    value = json.loads(remote.tool("list_document_categories", {}))
    if not isinstance(value, list):
        raise ValueError()
    return {row.get("category") for row in value
            if isinstance(row, dict) and isinstance(row.get("category"), str)}


def status(source, environment, cancelled, deadline):
    """Authentication and the selected category are the whole read-only status."""
    token(source, environment)
    try:
        found = categories(client(source, environment, cancelled, deadline))
    except KnowledgeError as exc:
        if exc.code in {"cancelled", "timeout", "auth_required"}:
            raise
        return {"status": "unavailable", "message": str(exc)}
    if source["collection"] not in found:
        return {"status": "unavailable", "message": "JedAI 集合不存在或未共享给当前账号"}
    return {"status": "ready", "message": "认证与集合可用"}


def candidates(remote, query, collection, count):
    try:
        value = json.loads(remote.tool("search_jedai_knowledge", {
            "query": query, "categories": [collection], "k": count}))
        results = value["results"]
        if not isinstance(results, list):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise KnowledgeError("protocol", "远程搜索结果缺少有效命中列表") from None
    return [row for row in results[:MAX_CANDIDATES] if isinstance(row, dict)]


def public(row):
    """Citation rows never expose the private retrieval query used for re-checking."""
    return {key: value for key, value in row.items() if key != "query"}


def search(project, source, query, tokens, limit, environment, cancelled, deadline):
    remote = client(source, environment, cancelled, deadline)
    hits = candidates(remote, query, source["collection"], min(16, limit * 4))
    rows, seen, failures = [], set(), 0
    for position, hit in enumerate(hits):
        checkpoint(cancelled, deadline)
        link, text = hit.get("link"), hit.get("page_content")
        if not valid_link(link) or not isinstance(text, str) or not text.strip():
            failures += 1
            continue
        version = digest(text.encode())
        if (link, version) in seen:
            continue
        seen.add((link, version))
        name = hit.get("name")
        rows.append({
            "source_id": source["id"], "document_id": digest([source_key(source), link]),
            "chunk_id": digest([link, version]),
            "title": clipped(name if isinstance(name, str) and name else link, 256),
            "excerpt": clipped(text, 800), "version": version, "uri": link,
            "location": {"start": None, "end": None, "page": None},
            "position": position, "query": clipped(query, 512)})
        if len(rows) >= min(limit, 5):
            break
    stored = remember(project, source, rows)
    return ([public(row) for row in stored],
            {"status": ("partial" if rows else "unavailable") if failures else "ready",
             "failed_documents": failures,
             "message": "部分命中缺少可引用原文" if failures else ""})


def read(project, source, identity, environment, cancelled, deadline):
    row = lookup(project, source, identity)
    query = row.pop("query", "")
    if not isinstance(query, str) or not query:
        raise KnowledgeError("stale_reference", "引用缺少可复核的检索来源，请重新检索")
    remote = client(source, environment, cancelled, deadline)
    for hit in candidates(remote, query, source["collection"], MAX_READ_CANDIDATES):
        link, text = hit.get("link"), hit.get("page_content")
        if link != row["uri"] or not isinstance(text, str):
            continue
        if digest(text.encode()) != row["version"]:
            raise KnowledgeError("stale_reference", "远程原文已变更，请重新检索")
        return {**public(row), "text": text, "verified": True}
    raise KnowledgeError("stale_reference", "远程片段已不可再取得，请重新检索")
