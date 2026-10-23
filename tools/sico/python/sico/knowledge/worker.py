"""One disposable source operation; credentials arrive only on private stdin."""

import json
import resource
import signal
import sys
import time

from ..transport.framing import strict_json
from .contracts import KnowledgeError, bounded

# Explicit read-only adapter dispatch; unknown adapters never reach the network.
ADAPTERS = {"openai_docs_mcp": "openai_docs", "jedai_aai_mcp": "jedai_aai"}


def adapter(source):
    from importlib import import_module

    module = ADAPTERS.get(source.get("adapter"))
    if module is None:
        raise KnowledgeError("unsupported", "此远程知识适配器尚未接通")
    return import_module("." + module, __package__)


def execute(request):
    project, source = request["project"], request["source"]
    deadline = time.monotonic() + request["seconds"]
    def cancelled():
        return False
    action, params = request["action"], request["params"]
    if source["type"] == "local":
        from . import index, local_query

        if action == "build":
            return index.build(project, source, cancelled, deadline)
        if action == "status":
            return index.status(project, source, cancelled, deadline)
        if action == "search":
            hits, state = local_query.search(project, source, params["tokens"], params["limit"], cancelled, deadline)
            return {"hits": hits, **state}
        if action == "read":
            return local_query.read(project, source, params["evidence_id"], cancelled, deadline)
    else:
        env = request["environment"]
        module = adapter(source)
        if action == "status":
            return module.status(source, env, cancelled, deadline)
        if action == "search":
            hits, state = module.search(project, source, params["query"], params["tokens"],
                                        params["limit"], env, cancelled, deadline)
            return {"hits": hits, **state}
        if action == "read":
            return module.read(project, source, params["evidence_id"], env, cancelled, deadline)
    raise KnowledgeError("unsupported", "此知识源不支持所选操作")


def main():
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024 * 1024, 768 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (128 * 1024 * 1024, 128 * 1024 * 1024))
    diagnostic_id = ""
    def stop(_sig, _frame):
        raise KnowledgeError("cancelled", "知识源操作已取消")
    signal.signal(signal.SIGTERM, stop)
    try:
        raw = sys.stdin.buffer.readline(131073)
        if len(raw) > 131072:
            raise ValueError()
        request = strict_json(raw)
        candidate = request.get("diagnostic_id", "")
        if isinstance(candidate, str) and len(candidate) <= 128:
            diagnostic_id = candidate
        value = bounded(execute(request))
        result = {"ok": True, "value": value}
    except KnowledgeError as exc:
        result = {"ok": False, "code": exc.code, "message": str(exc),
                  "diagnostic_id": diagnostic_id}
    except Exception as exc:
        result = {"ok": False, "code": "unavailable",
                  "message": "知识源操作未完成，请核对资料和连接",
                  "diagnostic_id": diagnostic_id, "exception": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0
