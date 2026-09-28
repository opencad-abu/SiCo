"""Worker-side process list and escaped, bounded output details."""

from __future__ import annotations

import hashlib
import json
import shlex
import time
from collections import deque
from datetime import datetime
from html import escape
from pathlib import Path

from cadai import process_monitor

from .display import data_html
from .native_display import unpack
from .published import freeze

STATES = {"running": "运行中", "exited": "已退出", "completed": "已完成",
          "failed": "失败", "declined": "未执行", "unknown": "待核对"}
OS_STATES = {"R": "运行", "S": "等待", "D": "等待 I/O", "T": "已暂停",
             "t": "调试暂停", "Z": "已退出（待回收）", "X": "已退出", "I": "空闲"}
LIMIT = process_monitor.OUTPUT_LIMIT


def timestamp(value):
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def when(value):
    return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S") if value else "未提供"


def elapsed(row):
    start = row.get("started_at")
    if not start:
        return "未提供"
    end = row.get("finished_at")
    if end is None and row["state"] not in {"running"}:
        return "未确认"
    seconds = max(0, int((end or time.time()) - start))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return (f"{hours}小时 " if hours else "") + (f"{minutes}分 " if minutes else "") + f"{seconds}秒"


def command_name(command):
    try:
        words = shlex.split(command)
        return Path(words[0]).name if words else "shell"
    except ValueError:
        return "shell"


class ProcessIndex:
    def __init__(self, reader):
        self.reader = reader
        self.rows = {}
        self.bindings = {}

    def receive(self, event):
        payload = event["payload"]
        native = payload.get("native")
        if not isinstance(native, dict) or native.get("item_type") != "commandExecution":
            return
        kind = event["kind"]
        if kind not in {"tool.started", "tool.finished", "codex.item.output", "codex.history.item",
                        "codex.history.output"}:
            return
        key = "command_" + hashlib.sha256(repr(tuple(native.get(k) for k in (
            "thread_id", "turn_id", "item_id"))).encode()).hexdigest()
        row = self.rows.setdefault(key, {
            "id": key, "name": "shell", "command": "", "cwd": "", "pid": None,
            "state": "running", "started_at": timestamp(event.get("timestamp")),
            "finished_at": None, "exit_code": None, "source": "shell", "chunks": deque(maxlen=8),
            "task_id": ((payload.get("origin") or {}).get("task_id", "")
                        if kind.startswith("codex.history.") else event.get("task_id", "")),
            "native": dict(native), "input": {}, "result": {},
        })
        if kind in {"codex.item.output", "codex.history.output"}:
            row["chunks"].append(payload["artifact"])
            return
        try:
            if kind == "tool.started":
                row["input"] = payload.get("input", {})
                item = unpack(row["input"], self.reader)
            else:
                row["result"] = payload.get("result", payload.get("value", {}))
                result = unpack(row["result"], self.reader)
                data = result.get("data", result)
                item = data.get("item", {})
                observed = data.get("completion_observed", False)
                status = item.get("status")
                row["state"] = ({"completed": "completed", "failed": "failed",
                                 "declined": "declined"}.get(status, "unknown")
                                if observed else "unknown")
                row["exit_code"] = item.get("exitCode")
                if row["state"] == "completed" and row["exit_code"] not in {0, None}:
                    row["state"] = "failed"
                row["finished_at"] = (timestamp(event.get("timestamp"))
                                      if row["state"] != "unknown" else None)
            for field in ("command", "cwd"):
                if isinstance(item.get(field), str):
                    row[field] = item[field][:65536]
            # Codex processId identifies a PTY session, not a Linux PID.
            if item.get("processId") is not None:
                row["execution_id"] = str(item["processId"])[:128]
            row["name"] = command_name(row["command"])
        except (OSError, ValueError, TypeError, KeyError):
            row["notice"] = "部分进程记录暂不可读或校验失败。"

    def _rows(self, live, detail=None):
        owned = process_monitor.snapshot(str(self.reader.directory), with_output=bool(detail),
                                         output_id=detail) if live else []
        owned = [r for r in owned if not (r["source"] == "codex_child" and (
            r["name"] in {"sico-mcp", "codex", "codex-exec-server"}
            or "sico.codex.mcp" in r.get("argv", [])))]
        # A parent launch and its discovered descendant may also be explicitly
        # registered by another launcher. Prefer that authoritative registration.
        direct = {(r["pid"], r["start"]) for r in owned if not r["parent_id"]}
        owned = [r for r in owned if not r["parent_id"] or (r["pid"], r["start"]) not in direct]
        by_id = {r["id"]: r for r in owned}
        claimed = set(self.bindings.values())
        rows = []
        commands = {}
        for row in self.rows.values():
            if row["state"] in {"running", "unknown"}:
                token = (row["command"], row["cwd"])
                commands[token] = commands.get(token, 0) + 1
        for original in self.rows.values():
            row = {k: v for k, v in original.items() if k not in {"input", "result", "chunks"}}
            if not live and row["state"] == "running":
                row["state"] = "unknown"
            if (row["id"] not in self.bindings and row["state"] in {"running", "unknown"}
                    and commands.get((row["command"], row["cwd"])) == 1):
                candidates = []
                for child in owned:
                    argv = child.get("argv", [])
                    if (child["source"] != "codex_child" or child["state"] != "running"
                            or child["id"] in claimed or child["cwd"] != row["cwd"]
                            or not argv or not row["command"]):
                        continue
                    try:
                        direct_command = shlex.split(row["command"])
                    except ValueError:
                        direct_command = []
                    if (argv == direct_command or (len(argv) >= 3 and argv[-2] in {"-c", "-lc"}
                                                  and argv[-1] == row["command"])):
                        candidates.append(child)
                if len(candidates) == 1:
                    self.bindings[row["id"]] = candidates[0]["id"]
                    claimed.add(candidates[0]["id"])
            child = by_id.get(self.bindings.get(row["id"]))
            if child:
                for key in ("pid", "ppid", "os_state", "rss"):
                    row[key] = child.get(key)
                if child["state"] == "running":
                    row.update(state="running", finished_at=None)
                elif child["state"] == "exited" and row["state"] in {"running", "unknown"}:
                    row.update(state="exited", finished_at=child["finished_at"])
                elif child["state"] == "unknown" and row["state"] == "running":
                    row.update(state="unknown", finished_at=None)
            rows.append(row)
        rows.extend(r for r in owned if r["id"] not in claimed)
        for row in rows:
            row["state_label"] = STATES.get(row["state"], "待核对")
            row["elapsed"] = elapsed(row)
        return sorted(rows, key=lambda row: (row["state"] != "running",
                                             -(row.get("started_at") or 0), row["id"]))

    def page(self, live, keyword="", offset=0):
        if not isinstance(keyword, str) or len(keyword) > 256 or type(offset) is not int or offset < 0:
            raise ValueError("Invalid process query")
        rows = self._rows(live)
        running = sum(r["state"] == "running" for r in rows)
        keyword = keyword.strip().casefold()
        if keyword:
            rows = [r for r in rows if keyword in "\n".join(str(r.get(k, "")) for k in
                    ("pid", "name", "command", "cwd", "state_label")).casefold()]
        offset = min(offset, max(0, (len(rows) - 1) // 200 * 200))
        fields = ("id", "pid", "name", "command", "state_label", "elapsed")
        return freeze({"rows": [{k: r.get(k) for k in fields} for r in rows[offset:offset + 200]],
                       "total": len(rows), "running": running, "offset": offset})

    def _output(self, key):
        row = self.rows[key]
        result = unpack(row["result"], self.reader)
        data = result.get("data", result)
        final = data.get("item", {}).get("aggregatedOutput")
        if (isinstance(final, str) and data.get("completion_observed")
                and data.get("item", {}).get("status") in {"completed", "failed", "declined"}):
            return final[-LIMIT:], len(final) > LIMIT
        # The sealed turn receipt predates late command output. The event stream
        # contains both portions in order, including repeated identical chunks.
        chunks = row["chunks"] or data.get("output_chunks", [])
        if not chunks and isinstance(final, str):
            return final[-LIMIT:], len(final) > LIMIT
        parts, size = [], 0
        for reference in reversed(chunks):
            value = json.loads(self.reader.artifact_text(reference["path"], reference["sha256"]))
            if value.get("native") != row["native"] or not isinstance(value.get("text"), str):
                raise ValueError("Output belongs to another command")
            text = value["text"]
            parts.append(text)
            size += len(text)
            if size >= LIMIT:
                break
        return "".join(reversed(parts))[-LIMIT:], size >= LIMIT or len(chunks) == 8

    def detail(self, key, live):
        row = next((r for r in self._rows(live, detail=key) if r["id"] == key), None)
        if row is None:
            raise ValueError("Process does not belong to this session")
        notice = row.get("notice", "")
        text, limited = row.get("output", ""), row.get("output_truncated", False)
        if key in self.rows:
            try:
                text, limited = self._output(key)
            except (ValueError, OSError, KeyError, TypeError):
                notice = "输出暂不可读或校验失败，原始记录保留。"
        fields = {"PID": row.get("pid") or "未提供", "名称": row["name"],
                  "运行状态": row["state_label"], "运行时长": row["elapsed"],
                  "启动时间": when(row.get("started_at")),
                  "结束时间": when(row.get("finished_at")),
                  "退出码": row.get("exit_code"), "工作目录": row.get("cwd") or "未提供"}
        if row.get("ppid"):
            fields["父进程 PID"] = row["ppid"]
        if row.get("os_state"):
            fields["系统状态"] = OS_STATES.get(row["os_state"], row["os_state"])
        if row.get("rss") is not None:
            fields["内存"] = f"{row['rss'] / 1048576:.1f} MiB"
        if row.get("execution_id"):
            fields["命令会话编号"] = row["execution_id"]
        if row.get("task_id"):
            fields["所属任务"] = row["task_id"]
        if row.get("logs"):
            fields["日志文件"] = "\n".join(row["logs"])
        # 启动命令与输出交给详情页分区渲染（见 COPILOT_DETAIL_CODE_OUTPUT_PLAN.md）：
        # 命令按识别结果高亮进文档区，输出进下方独立文本区。html 只留字段表，
        # 截断提示留在文档区。
        prefix = data_html(fields)
        if limited:
            prefix += "<p>当前显示最近的输出；较早内容请查看日志或会话归档。</p>"
        command = str(row.get("command", "") or "")
        return freeze({
            "title": "进程 · " + row["name"],
            "prefix": prefix,
            "script": command,
            "script_language": "",
            "output": text,
            "output_truncated": bool(limited),
            "logs": tuple(str(path) for path in (row.get("logs") or ())),
            "notice": notice,
            "process_state": row["state"],
        })
