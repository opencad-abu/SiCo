"""One source selection, bounded fan-out and deterministic rank-based fusion."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import re
import time

from ..agent_resources.schema import require_supported, upgrade_manifest
from ..diagnostics import DiagnosticLog, UNEXPECTED_EXCEPTION
from .contracts import AUTHORITY, MAX_OUTPUT, KnowledgeError, bounded, checkpoint, query_terms
from .credentials import capture
from .jobs import run


class Knowledge:
    def __init__(self, project, manifest, environment=None, model_key="SICO_API_KEY",
                 diagnostic=None):
        manifest = upgrade_manifest(manifest)
        require_supported(manifest)
        self.project, self.sources = project, deepcopy(manifest["knowledge_sources"])
        self.diagnostic = diagnostic
        supplied = environment or {}
        credentials, self.references = capture(self.sources, supplied, model_key)
        self.environment = {k: v for k, v in supplied.items() if k in {
            "PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH", "SICO_PYTHON", "SICO_PYTHON_ROOT",
            "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"}}
        self.environment.update(credentials)

    def source(self, identity):
        row = next((r for r in self.sources if r["id"] == identity and r["enabled"]), None)
        if row is None:
            raise KnowledgeError("unknown_source", "知识源未在当前选择中启用")
        return row

    def operation(self, source, action, params, cancelled, deadline):
        started = time.monotonic()
        diagnostic = (self.diagnostic.child() if self.diagnostic is not None
                      else DiagnosticLog.disabled("knowledge"))
        try:
            value = run(self.project, source, action, params, self.environment,
                        cancelled, deadline, diagnostic)
        except KnowledgeError as exc:
            value = {
                "status": exc.code,
                "message": str(exc),
                "error_code": exc.category,
                "diagnostic_id": exc.diagnostic_id or diagnostic.correlation_id,
            }
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            diagnostic.emit(
                "knowledge.operation.failed", category=UNEXPECTED_EXCEPTION,
                stage=str(action)[:120], exception=type(exc).__name__,
            )
            value = {
                "status": "unavailable",
                "message": "知识源操作未完成，请核对资料和连接",
                "error_code": UNEXPECTED_EXCEPTION,
                "diagnostic_id": diagnostic.correlation_id,
            }
        return {"source_id": source["id"], "elapsed_ms": round((time.monotonic() - started) * 1000), **value}

    def status(self, cancelled=lambda: False):
        rows = []
        deadline = time.monotonic() + 30
        for source in self.sources:
            checkpoint(cancelled, float("inf"))
            rows.append(self.operation(source, "status", {}, cancelled, deadline) if source["enabled"] else
                        {"source_id": source["id"], "status": "disabled", "message": "未启用"})
        return bounded({"sources": rows, "authority": AUTHORITY})

    def build(self, identity, cancelled=lambda: False):
        source = self.source(identity)
        if source["type"] != "local":
            raise KnowledgeError("unsupported", "远程知识源由服务维护索引")
        return bounded(self.operation(source, "build", {}, cancelled, time.monotonic() + 90))

    def search(self, query, limit=8, cancelled=lambda: False):
        import json
        tokens = query_terms(query)
        if type(limit) is not int or not 1 <= limit <= 16:
            raise KnowledgeError("invalid_limit", "检索条数必须在 1 到 16 之间")
        sources = sorted((r for r in self.sources if r["enabled"]), key=lambda r: r["id"])
        deadline = time.monotonic() + 60
        def search_one(source):
            seconds = min(45, source.get("timeout_sec", 20))
            return self.operation(source, "search", {"query": query, "tokens": tokens, "limit": limit},
                                  cancelled, min(deadline, time.monotonic() + seconds))
        with ThreadPoolExecutor(max_workers=4) as pool:
            values = list(pool.map(search_one, sources))
        checkpoint(cancelled, float("inf"))
        ranked, statuses = [], []
        for result in values:
            for rank, hit in enumerate(result.pop("hits", []), 1):
                ranked.append({**hit, "source_rank": rank})
            statuses.append(result)
        # Round-robin by rank; never compare scores from unlike retrieval adapters.
        ranked.sort(key=lambda r: (r["source_rank"], r["source_id"], r["chunk_id"]))
        unique, seen = [], set()
        for row in ranked:
            identity = row["uri"], row["version"], row["chunk_id"]
            if identity not in seen:
                unique.append(row)
                seen.add(identity)
        partial = any(r["status"] != "ready" for r in statuses)
        successful = any(r["status"] in {"ready", "stale", "partial"} for r in statuses)
        result = {"status": "partial" if partial and successful else "unavailable" if partial else "ready",
                        "hits": unique[:limit], "sources": statuses, "no_matches": not unique,
                        "fusion": "source-rank round-robin; source-id tie break", "authority": AUTHORITY,
                        "truncated": len(unique) > limit}
        for row in result["sources"]:
            row["issues"] = row.get("issues", [])[:2]
        while len(json.dumps(result, ensure_ascii=False).encode()) > MAX_OUTPUT and result["hits"]:
            result["hits"].pop()
            result["truncated"] = True
        return bounded(result)

    def read(self, source_id, evidence_id, cancelled=lambda: False):
        source = self.source(source_id)
        if not isinstance(evidence_id, str) or re.fullmatch("[a-f0-9]{64}", evidence_id) is None:
            raise KnowledgeError("invalid_reference", "请使用搜索返回的完整引用标识")
        result = self.operation(source, "read", {"evidence_id": evidence_id}, cancelled,
                                time.monotonic() + min(45, source.get("timeout_sec", 15)))
        return bounded({"authority": AUTHORITY, **result})
