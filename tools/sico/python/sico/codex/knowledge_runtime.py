"""Derive one session-owned knowledge MCP bridge from its frozen resource selection."""

import json
import threading

from ..installation import python_path
from ..interpreter import cad_python
from ..knowledge.contracts import KnowledgeError
from ..knowledge.engine import Knowledge
from ..knowledge.tools import schemas
from .mcp import ToolBridge


class KnowledgeRuntime:
    def __init__(self, project, manifest, settings, diagnostic=None):
        self.engine = Knowledge(project, manifest, settings.environment,
                                settings.options.get("api_key_env", "SICO_API_KEY"),
                                diagnostic=diagnostic)
        self.stop = threading.Event()
        self.lock, self.active = threading.Condition(), 0
        self.bridge = ToolBridge(self, self.execute, timeout=70)
        self.environment = {"SICO_KNOWLEDGE_PORT": str(self.bridge.port),
                            "SICO_KNOWLEDGE_TOKEN": self.bridge.token}
        self.references = self.engine.references
        # 两个只读检索工具默认批准（与内置 sico_ic 的宿主授权一致）；调用仍进入会话审计。
        self.config = {
            "command": cad_python(settings.environment), "args": ["-s", "-c",
                "import os; from sico.codex.mcp import main; "
                "os.environ['SICO_MCP_PORT']=os.environ.pop('SICO_KNOWLEDGE_PORT'); "
                "os.environ['SICO_MCP_TOKEN']=os.environ.pop('SICO_KNOWLEDGE_TOKEN'); "
                "raise SystemExit(main())"],
            "cwd": str(project), "env": {"PYTHONPATH": python_path(), "SICO_MCP_TIMEOUT": "70"},
            "env_vars": ["LD_LIBRARY_PATH", *self.environment], "required": True,
            "enabled": True, "enabled_tools": ["search", "read"],
            "startup_timeout_sec": 10, "tool_timeout_sec": 70,
            "default_tools_approval_mode": "approve",
        }

    @staticmethod
    def mcp_schemas():
        return schemas()

    def execute(self, name, arguments, metadata=None):
        with self.lock:
            if self.stop.is_set() or self.active >= 2:
                raise ValueError("知识源正在关闭或检查容量已满")
            self.active += 1
        try:
            if not isinstance(arguments, dict):
                raise KnowledgeError("invalid_arguments", "检索参数需要对象")
            if name == "search" and set(arguments) <= {"query", "limit"} and "query" in arguments:
                value = self.engine.search(**arguments, cancelled=self.stop.is_set)
            elif name == "read" and set(arguments) == {"source_id", "evidence_id"}:
                value = self.engine.read(**arguments, cancelled=self.stop.is_set)
            else:
                raise KnowledgeError("invalid_arguments", "不支持此检索工具或参数")
            failed = value.get("status") not in {None, "ready", "partial"}
        except KnowledgeError as exc:
            value = {"status": exc.code, "message": str(exc),
                     "error_code": exc.category}
            if exc.diagnostic_id:
                value["diagnostic_id"] = exc.diagnostic_id
            failed = True
        finally:
            with self.lock:
                self.active -= 1
                self.lock.notify_all()
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
                "isError": failed}

    def close(self):
        self.stop.set()
        self.bridge.close()
        with self.lock:
            if not self.lock.wait_for(lambda: self.active == 0, timeout=5):
                raise RuntimeError("知识源检查进程尚未确认退出")
