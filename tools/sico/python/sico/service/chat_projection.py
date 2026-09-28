"""Canonical role-aware chat state shared by live events and journal paging."""

from __future__ import annotations

import os

from sico.core.links import object_link

from . import chat_audit as transcript_audit
from . import chat_reconcile as transcript_reconcile
from . import chat_text as transcript_text
from .chat_retention import TranscriptHistory
from .chat_tools import TranscriptTools
from .reconcile_calls import ReconcileCalls
from .display import event_time, recommended_label


def system_user_name(environment=None):
    """Resolve the display name from the launcher's operating-system environment."""
    source = os.environ if environment is None else environment
    for key in ("USER", "LOGNAME", "USERNAME"):
        value = str(source.get(key, "")).strip()
        if value and not any(ord(char) < 32 for char in value):
            return value
    return "你"


class ChatProjection(TranscriptTools, TranscriptHistory):
    # No emoji: the field terminals only render plain dots.
    ACTIVITY_DOTS = ("", ".", "..", "...")
    # 工具计数器执行中的标记：小三角形逐个出现（1、2、3），
    # 执行结束后只留下最后一个作为展开/收拢开关。
    TOOL_MARKS = (1, 2, 3)
    # Keep a bounded presentation tail. Full history belongs to the journal.
    RETAIN_MESSAGES = 400
    # Startup reads stay neutral context bubbles; they are not task tool work.
    CONTEXT_TOOLS = frozenset({"get_context", "get_entry_context", "get_project_context"})
    TOOL_ITEM_LIMIT = 50
    TOOL_HIGHLIGHT_LIMIT = 5
    TOOL_OK_STATUSES = frozenset({"", "ok", "completed", "success"})
    TOOL_STATUS_TEXT = {
        "running": "执行中", "unconfirmed": "结果未确认", "needs_reconcile": "需核对",
        "tool_error": "执行失败", "preflight_failed": "前置检查未通过",
        "operation_failed": "操作失败，回执已保留",
        "simulation_project_dir_invalid": "仿真目录配置无效",
        "task_source_mismatch": "任务来源不匹配",
        "circuit_preflight_failed": "电路前置检查未通过",
        "circuit_invalid_call": "电路调用参数无效",
        "circuit_read_failed": "电路读取失败",
        "circuit_result_too_large": "电路响应超出大小限制",
        "circuit_invalid_reply": "电路响应无效",
        "circuit_requires_target": "需要选择电路目标",
        "invalid_arguments": "参数无效", "unavailable": "不可用", "failed": "失败", "ok": "成功",
    }

    def __init__(self, username=None, *, read_only=False):
        self.messages = []
        self.streaming = None
        self.activity = None
        self.tools = None
        self.calls = {}
        self.native_records = {}
        self.supplements = {}
        self.pdk_notices = set()
        self.audits = {}
        self.startup = False
        self.dirty = True
        self.dot_phase = 0
        self.mark_phase = 0
        self.reconciliation = ReconcileCalls()
        self.username = username or system_user_name()
        self.read_only = read_only
        self._next_message_id = 0
        self._source_sequence = 0
        self._source_slot = 0

    _recommended_label = staticmethod(recommended_label)
    _event_time = staticmethod(event_time)

    def changed(self, message):
        """Observe a displayed row mutation; storage adapters override this hook."""

    def removed(self, message):
        """Observe removal from the conversation (cache eviction is separate)."""

    def _changed(self, message):
        message["revision"] += 1
        self.changed(message)
        self.dirty = True

    def live_messages(self):
        """Bubbles that are still in flight: model activity and the running tool batch.

        工具计数在整个批次执行期间保持动效（真实会话里很多调用只有零点几秒，
        逐个调用触发根本看不到三角形累积）；批次结束后才收起成一个三角形。
        """
        live, seen = [], set()
        for message in (self.activity, self.tools):
            if message is None or message.get("state", "running") != "running":
                continue
            if id(message) not in seen:
                seen.add(id(message))
                live.append(message)
        return live

    def advance_activity(self):
        """Cycle the running marks; returns False when nothing is running."""
        live = self.live_messages()
        if not live:
            self.dot_phase = 0
            self.mark_phase = 0
            return False
        self.dot_phase = (self.dot_phase + 1) % len(self.ACTIVITY_DOTS)
        suffix = self.ACTIVITY_DOTS[self.dot_phase]
        marks = self.TOOL_MARKS[self.mark_phase]
        self.mark_phase = (self.mark_phase + 1) % len(self.TOOL_MARKS)
        for message in live:
            if message.get("role") == "tools":
                # 工具计数用逐个出现的小三角形，不再追加省略号。
                if message.get("marks", 0) != marks:
                    message["marks"] = marks
                    self._changed(message)
            elif message.get("suffix", "") != suffix:
                message["suffix"] = suffix
                self._changed(message)
        return True

    def settle_activity(self):
        """Stop the animation and drop every trailing mark; reports what it dropped."""
        self.dot_phase = 0
        self.mark_phase = 0
        cleared = False
        for message in self.live_messages():
            if message.get("suffix") or message.get("marks"):
                message["suffix"] = ""
                message["marks"] = 0
                self._changed(message)
                cleared = True
        return cleared

    def append(self, role, text="", *, result=None):
        self._next_message_id += 1
        self._source_slot += 1
        key = (self._source_sequence * 1024 + self._source_slot
               if self._source_sequence else self._next_message_id)
        message = {"role": role, "text": text, "result": result, "revision": 0,
                   "message_id": key, "source_sequence": self._source_sequence}
        self.messages.append(message)
        self.changed(message)
        self.trim_history()
        self.dirty = True
        return message

    def receive(self, event):
        if len(self.calls) > 1024 or len(self.audits) > 1024:
            raise ValueError("Too many outstanding transcript references")
        self._source_sequence = event.get("sequence", 0)
        self._source_slot = 0
        self.reconciliation.receive(event)
        self._receive_event(event)
        if event.get("detail"):
            self.append("native", "查看完整记录", result={
                "url": "event-detail:" + event["detail"],
                "status": "聊天中仅展示部分内容",
            })

    def _receive_event(self, event):
        kind, payload = event["kind"], event["payload"]
        if kind == "task.started":
            self._refresh_reconcile(active=False)
            self._close_tools(event)
            self.startup = payload.get("origin") == "startup"
            self.streaming = None
            self.activity = None
            self.calls.clear()
            if not self.startup:
                self.append("user", payload.get("text", ""))
                if payload.get("inputs") or payload.get("turn_options"):
                    self.append("notice", "输入资料与回合设置", result={
                        "url": "turn-input:" + str(event["sequence"]),
                        "status": f"{len(payload.get('inputs', []))} 项资料",
                    })
        elif kind.startswith("codex.steer.") and "id" in payload:
            key = payload["id"]
            text = "补充当前任务 · " + payload["message"] + "\n\n" + payload["text"]
            message = self.supplements.get(key)
            if message is None:
                message = self.supplements[key] = self.append("user", text)
            else:
                message["text"] = text
                self._changed(message)
        elif kind == "model.status":
            if payload.get("phase") == "waiting_user" and any(
                row.get("status") == "pending" for row in self.audits.values()
            ):
                return
            if payload.get("phase") == "tool_call":
                # The tool counter already names the running call.
                self._clear_activity()
                return
            text = self._activity_text(payload)
            if self.activity is None:
                self.activity = self.append("activity", text)
            else:
                self.activity["text"] = text
                self._changed(self.activity)
        elif kind == "tool.started":
            if self.startup and payload["name"] in self.CONTEXT_TOOLS:
                self.calls[(event.get("task_id"), payload["id"])] = None
                return
            group = self._open_tools(event)
            item = self._append_tool(group, payload, event)
            self.calls[(event.get("task_id"), payload["id"])] = (group, item)
        elif kind == "model.delta":
            self._clear_activity()
            self._close_tools(event)
            if self.streaming is None:
                self.streaming = self.append("assistant")
            self.streaming["text"] += payload["text"]
            self._changed(self.streaming)
        elif kind == "model.completed":
            self._clear_activity()
            self._close_tools(event)
            if payload.get("text"):
                if self.streaming is None:
                    self.append("assistant", payload["text"])
                else:
                    if self._source_sequence:
                        self.removed(self.streaming)
                        self.streaming["message_id"] = self._source_sequence * 1024 + 1
                        self.streaming["source_sequence"] = self._source_sequence
                        self._source_slot = 1
                        # A clipped delta can have its own evidence row. The
                        # completed answer is ordered at its authoritative event.
                        self.messages = [m for m in self.messages if m is not self.streaming]
                        self.messages.append(self.streaming)
                    self.streaming["text"] = payload["text"]
                    self._changed(self.streaming)
            self.streaming = None
        elif kind == "workbench.report.published":
            self._clear_activity()
            key = payload["id"] + "_v" + str(payload["version"])
            self.append("report", payload["title"], result={
                "url": object_link("report", event["session_id"], key),
                "version": payload["version"],
            })
        elif kind == "session.reconcile_queried":
            self.append("notice", payload["text"])
        elif kind == "session.reconcile_decided":
            self._refresh_reconcile()
        elif kind == "session.tool_receipt":
            self._refresh_reconcile()
            self.append("notice", "已收到停止前工具的补充回执，可在核对卡片查询原请求结果。")
        elif kind == "tool.finished":
            self._receive_tool_finished(event, payload)
        elif kind in {"codex.plan.updated", "codex.diff.updated", "codex.native.updated",
                      "codex.history.item"}:
            self._receive_native(event, kind, payload)
        elif kind == "codex.history.unavailable":
            self.append("notice", payload["message"])
        elif kind == "codex.elicitation.unsupported":
            self.append("notice", payload["reason"])
        elif kind == "codex.resources.updated":
            text = transcript_text.resource_notice(payload)
            if text and text != getattr(self, "resource_notice", ""):
                self.append("notice", text)
            self.resource_notice = text
        elif kind == "workbench.audit.prepared":
            self.audits[payload["id"]] = {
                **payload, "status": "prepared", "task_id": event.get("task_id")}
        elif kind.startswith("workbench.audit."):
            self._receive_audit(event, kind, payload)
        elif kind == "session.rebound":
            self._clear_activity()
            self.append(
                "notice",
                "会话已重新连接当前工程；历史设计目标需重新读取和预检。\n"
                + str(payload.get("message", ""))
                + "\n若电路调用被拒，请从目标窗口重新发起，或让 agent 打开设计视图后继续。",
            )
        elif kind == "context.detached":
            self._clear_activity()
            self.append("notice", payload["message"])
        elif kind in {"task.completed", "task.failed", "task.needs_reconcile", "task.cancelled"}:
            self._receive_terminal(event, kind, payload)

    def _receive_tool_finished(self, event, payload):
        data = (payload.get("result") or {}).get("data")
        if isinstance(data, dict):
            for notice in data.get("notices") or []:
                if not isinstance(notice, dict) or notice.get("code") != "pdk_path_changed":
                    continue
                message = notice.get("message")
                if isinstance(message, str) and message and message not in self.pdk_notices:
                    self.pdk_notices.add(message)
                    self.append("notice", message)
        entry = self.calls.pop((event.get("task_id"), payload.get("id")), None)
        if self.startup and payload["name"] in self.CONTEXT_TOOLS:
            self._clear_activity()
            self.append("context", result=payload["result"])
            return
        if entry is None:
            group = self._open_tools(event)
            item = self._append_tool(group, payload, event)
        else:
            group, item = entry
        self._finish_tool(group, item, payload, event)

    def _receive_native(self, event, kind, payload):
        from sico.service.metadata import native_record_title
        suffix = {"codex.plan.updated": "plan", "codex.diff.updated": "diff",
                  "codex.native.updated": "native", "codex.history.item": "history"}[kind]
        native = payload["native"]
        key = (suffix, native["thread_id"], native["turn_id"], native["item_id"])
        message = self.native_records.get(key)
        if message is None:
            label = {"plan": "执行计划", "diff": "文件差异"}.get(
                suffix, native_record_title(native))
            if suffix == "history":
                label = "历史补采：" + label
            message = self.append("native", label)
            self.native_records[key] = message
        message["result"] = {
            "url": object_link("data", event["session_id"], f"d_{event['sequence']}_{suffix}"),
            "status": ("进行中" if payload.get("status") == "executing" else
                       "待核对" if payload.get("status") == "needs_reconcile" else
                       "未归属" if suffix == "history" and not payload.get("origin")
                       else "已更新"),
        }
        self._changed(message)

    def _receive_audit(self, event, kind, payload):
        row = self.audits.get(payload["id"])
        if row is None:
            return
        row["status"] = {
            "workbench.audit.opened": "pending",
            "workbench.audit.reply_received": "answer_received",
            "workbench.audit.resumed": "answered",
            "workbench.audit.invalidated": "invalid",
            "workbench.audit.dispatching": "dispatching",
            "workbench.audit.unconfirmed": "unconfirmed",
        }.get(kind, row["status"])
        if kind == "workbench.audit.opened":
            self._clear_activity()
            row["message"] = self.append("audit", result={
                "url": object_link("audit", event["session_id"], row["id"]),
            })
        elif kind == "workbench.audit.reply_received":
            if "elicitation" in row:
                from sico.codex.elicitation_schema import reply_text

                text = reply_text(row, payload)
            else:
                text = "\n".join(
                    q["header"] + "：" + "；".join(
                        self._recommended_label(v)
                        for v in payload["answers"][q["id"]].values() if v
                    ) for q in row["questions"]
                )
            self.append("user", text)
        row["reason"] = payload.get("reason", "")
        self._update_audit(row)

    def _receive_terminal(self, event, kind, payload):
        self._clear_activity()
        self._close_tools(event)
        self.streaming = None
        for row in self.audits.values():
            if row.get("task_id") == event.get("task_id") and row["status"] == "dispatching":
                row["status"] = "unconfirmed"
                self._update_audit(row)
            if (row.get("task_id") == event.get("task_id")
                    and row["status"] in {"pending", "answer_received"}):
                row["status"] = "withdrawn"
                self._update_audit(row)
        # Unfinished calls are already reported as "未确认" in their counter.
        self.calls.clear()
        self.dirty = True
        if kind == "task.completed":
            return
        message = payload.get("message", payload.get("reason", ""))
        if kind == "task.needs_reconcile":
            self.append("reconcile", self._reconcile_notice(message), result={
                "session_id": event.get("session_id", ""),
                "task_id": event.get("task_id", ""), "active": True,
                "message": message, "operations": self.reconciliation.rows(),
            })
        else:
            self._refresh_reconcile(active=False)
            labels = {"task.failed": "任务失败", "task.cancelled": "任务已取消"}
            label = labels.get(kind, "任务中断")
            self.append("notice", label + "\n" + message)

    def _reconcile_notice(self, message):
        return transcript_reconcile.reconcile_notice(self.reconciliation.rows(), message)

    def _refresh_reconcile(self, *, active=True):
        for row in self.messages:
            if row["role"] != "reconcile" or not row["result"].get("active"):
                continue
            row["result"]["active"] = active
            if active:
                row["result"]["operations"] = self.reconciliation.rows()
                row["text"] = self._reconcile_notice(row["result"]["message"])
            self._changed(row)

    def _clear_activity(self):
        if self.activity is not None:
            self.removed(self.activity)
            self.messages = [m for m in self.messages if m is not self.activity]
            self.activity = None
            self.dirty = True

    def _update_audit(self, row):
        message = row.get("message")
        if message is None:
            return
        message["text"] = transcript_audit.audit_text(
            row["title"], row["status"], row["questions"],
            recommendation=row.get("recommendation"), reason=row.get("reason"),
            elicitation="elicitation" in row, recommended_label=self._recommended_label,
        )
        message["result"]["pending"] = row["status"] == "pending"
        self._changed(message)

    @staticmethod
    def _activity_text(payload):
        return transcript_text.activity_text(payload)

    @staticmethod
    def _tool_result_text(payload):
        return transcript_text.tool_result_text(payload)
