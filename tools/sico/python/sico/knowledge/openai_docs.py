"""OpenAI Docs MCP adapter: search, fetch verified text, retain scoped citations."""

import html
import json
from urllib.parse import unquote, urlsplit, urlunsplit

from .catalog import source_key
from .contracts import KnowledgeError, checkpoint, clipped, digest, terms
from .credentials import token
from .parsing import chunks
from .remote_evidence import lookup, remember
from .remote_http import McpHttp

HOSTS = {"developers.openai.com", "platform.openai.com", "learn.chatgpt.com"}


def status(source, environment, cancelled, deadline):
    """Selection is checked lazily; the docs service is verified on search/read."""
    token(source, environment)
    return {"status": "unchecked", "message": "搜索时验证远程服务及原文引用"}


def allowed(url, collection):
    if not isinstance(url, str) or len(url) > 2048 or any(c.isspace() for c in url):
        return None
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.netloc not in HOSTS or parsed.query or
            any(p in {".", ".."} for p in unquote(parsed.path).split("/"))):
        return None
    if collection == "codex" and not (parsed.netloc == "learn.chatgpt.com" and parsed.path.startswith("/docs/")
                                      or "/codex" in parsed.path):
        return None
    if collection == "api" and not (parsed.path.startswith("/api/") or parsed.netloc == "platform.openai.com"):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def client(source, environment, cancelled, deadline):
    return McpHttp(source, token(source, environment), environment, cancelled, deadline)


def search(project, source, query, tokens, limit, environment, cancelled, deadline):
    remote = client(source, environment, cancelled, deadline)
    try:
        results = json.loads(remote.tool("search_openai_docs", {"query": query, "limit": min(50, limit * 4)}))
        hits = results["hits"]
        if not isinstance(hits, list):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise KnowledgeError("protocol", "远程搜索结果缺少有效命中列表") from None
    rows, seen, failures = [], set(), 0
    for hit in hits[:50]:
        checkpoint(cancelled, deadline)
        if not isinstance(hit, dict):
            failures += 1
            continue
        url = allowed(hit.get("url"), source["collection"])
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            text = remote.tool("fetch_openai_doc", {"url": url})
            parts = chunks(text)
            if not parts:
                continue
            ranked = sorted(enumerate(parts), key=lambda pair: (
                -len(set(tokens) & set(terms(pair[1]["text"]))), pair[0]))
            position, part = ranked[0]
            hierarchy = hit.get("hierarchy")
            title = hierarchy.get("lvl1") if isinstance(hierarchy, dict) else None
            title = html.unescape(title if isinstance(title, str) and title else url)
            version = digest(text.encode())
            rows.append({"source_id": source["id"], "document_id": digest([source_key(source), url]),
                         "chunk_id": digest([url, version, position]), "title": clipped(title, 256),
                         "excerpt": clipped(part["text"], 800), "version": version,
                         "uri": url, "location": {"start": part["start"], "end": part["end"],
                                                    "page": None}, "position": position})
        except KnowledgeError as exc:
            if exc.code in {"cancelled", "timeout", "auth_required"}:
                raise
            failures += 1
        if len(rows) >= min(limit, 5) or len(seen) >= 8:
            break
    return remember(project, source, rows), {"status": ("partial" if rows else "unavailable") if failures else "ready",
                                            "failed_documents": failures,
                                            "message": "部分命中原文不可读取" if failures else ""}


def read(project, source, identity, environment, cancelled, deadline):
    row = lookup(project, source, identity)
    url = allowed(row["uri"], source["collection"])
    if not url:
        raise KnowledgeError("invalid_reference", "引用不在知识集合范围内")
    text = client(source, environment, cancelled, deadline).tool("fetch_openai_doc", {"url": url})
    if digest(text.encode()) != row["version"]:
        raise KnowledgeError("stale_reference", "远程原文已变更，请重新检索")
    parts = chunks(text)
    part = parts[row["position"]]
    return {**row, "text": part["text"], "verified": True}
