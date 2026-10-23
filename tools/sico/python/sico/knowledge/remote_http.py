"""Bounded Streamable HTTP client for read-only knowledge adapters."""

import json
import time
import urllib.error
import urllib.request

from ..transport.framing import strict_json
from .contracts import KnowledgeError, checkpoint

MAX_RESPONSE = 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise KnowledgeError("redirect_rejected", "知识源地址重定向，请核对实际服务地址")


class McpHttp:
    def __init__(self, source, token, environment, cancelled, deadline):
        self.source, self.token = source, token
        self.cancelled, self.deadline = cancelled, deadline
        self.counter, self.session = 0, None
        proxies = {kind: environment.get(kind.upper() + "_PROXY", environment.get(kind + "_proxy"))
                   for kind in ("http", "https")}
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({k: v for k, v in proxies.items() if v}), NoRedirect())
        result = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "sico-knowledge", "version": "1"}})
        if not isinstance(result.get("capabilities"), dict) or "tools" not in result["capabilities"]:
            raise KnowledgeError("protocol", "知识源未提供 MCP 工具能力")
        self.request("notifications/initialized", {}, notification=True)

    def request(self, method, params, *, notification=False):
        checkpoint(self.cancelled, self.deadline)
        self.counter += 1
        value = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            value["id"] = self.counter
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                   "MCP-Protocol-Version": "2025-06-18"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(self.source["url"], data=json.dumps(value).encode(), headers=headers)
        try:
            timeout = max(0.1, min(self.source["timeout_sec"], self.deadline - time.monotonic()))
            with self.opener.open(request, timeout=timeout) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                raw = response.read(MAX_RESPONSE + 1)
                kind = response.headers.get_content_type()
        except urllib.error.HTTPError as exc:
            code = "auth_required" if exc.code in {401, 403} else "unavailable"
            raise KnowledgeError(code, "远程知识源认证未通过" if code == "auth_required" else
                                 "远程知识源暂不可用") from None
        except (OSError, TimeoutError, ValueError):
            raise KnowledgeError("unavailable", "远程知识源连接未完成") from None
        checkpoint(self.cancelled, self.deadline)
        if len(raw) > MAX_RESPONSE:
            raise KnowledgeError("response_limit", "远程知识源响应超过 1 MiB")
        if notification:
            return {}
        try:
            if kind == "text/event-stream":
                messages = []
                for event in raw.decode().replace("\r\n", "\n").split("\n\n"):
                    data = "\n".join(line[5:].lstrip() for line in event.splitlines() if line.startswith("data:"))
                    if data:
                        messages.append(strict_json(data))
                result = next(r for r in messages if r.get("id") == self.counter)
            else:
                result = strict_json(raw)
            if result.get("jsonrpc") != "2.0" or result.get("id") != self.counter or "error" in result:
                raise ValueError()
            if not isinstance(result["result"], dict):
                raise ValueError()
            return result["result"]
        except (ValueError, KeyError, TypeError, StopIteration, UnicodeError):
            raise KnowledgeError("protocol", "远程知识源响应格式无效") from None

    def tool(self, name, arguments):
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise KnowledgeError("remote_error", "远程资料未能读取，请核对集合和来源")
        content = result.get("content", [])
        text = "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
        if not text:
            raise KnowledgeError("protocol", "远程知识源未返回文本")
        if self.token:
            text = text.replace(self.token, "[credential]")
        return text
