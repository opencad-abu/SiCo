"""Bounded tool-counter state and receipt labels for the transcript."""

from datetime import datetime


class TranscriptTools:
    def _open_tools(self, event):
        """Open (or reuse) the counter for the tool batch of the current turn."""
        group = self.tools
        if group is not None and group.get("state") == "running":
            return group
        group = self.append("tools")
        group.update({
            "role": "tools", "text": "", "result": None, "revision": 0,
            "state": "running",
            "counts": {"total": 0, "ok": 0, "failed": 0, "needs_reconcile": 0, "unconfirmed": 0},
            "items": [], "truncated": False, "current": "", "expanded": False,
            "started": event.get("timestamp", ""), "finished": "",
            "suffix": "", "marks": 0,
        })
        self.changed(group)
        self.dirty = True
        self.tools = group
        return group

    def _keep_tool_item(self, group, item):
        """Bounded receipt list; failed receipts displace finished ones."""
        items = group["items"]
        if len(items) < self.TOOL_ITEM_LIMIT:
            items.append(item)
            return
        group["truncated"] = True
        for index, existing in enumerate(items):
            if existing["status"] == "ok":
                items[index] = item
                return

    def _append_tool(self, group, payload, event):
        arguments = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        if (payload.get("name") == "execute_circuit_operation"
                and isinstance(arguments.get("arguments"), dict)):
            arguments = arguments["arguments"]
        item = {
            "id": payload.get("id", ""), "name": payload.get("name", ""),
            "status": "running", "summary": "", "duration": "",
            "request_id": arguments.get("request_id", ""),
            "started": event.get("timestamp", ""), "finished": "", "sequence": 0,
        }
        group["counts"]["total"] += 1
        self._keep_tool_item(group, item)
        group["current"] = item["name"]
        self._refresh_tools(group)
        return item

    def _finish_tool(self, group, item, payload, event):
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        if item["status"] == "unconfirmed":
            group["counts"]["unconfirmed"] -= 1
        status = str(result.get("status") or "")
        item["status"] = status or "ok"
        item["summary"] = self._tool_result_text(payload)
        item["finished"] = event.get("timestamp", "")
        item["duration"] = self._elapsed(item["started"], item["finished"])
        # The workbench records the call as d_<sequence>_tool on this event.
        sequence = event.get("sequence")
        item["sequence"] = sequence if isinstance(sequence, int) and sequence > 0 else 0
        counts = group["counts"]
        if item["status"] == "needs_reconcile":
            counts["needs_reconcile"] += 1
            if item not in group["items"]:
                self._keep_tool_item(group, item)
        elif item["status"] in self.TOOL_OK_STATUSES or result.get("ok") is True:
            counts["ok"] += 1
        else:
            counts["failed"] += 1
            if item not in group["items"]:
                self._keep_tool_item(group, item)
        if group.get("current") == item["name"]:
            group["current"] = ""
        if item["finished"]:
            group["finished"] = item["finished"]
        self._refresh_tools(group)

    def _close_tools(self, event=None):
        """Freeze the counter; calls without a result stay explicitly unconfirmed."""
        group, self.tools = self.tools, None
        if group is None or group.get("state") != "running":
            return
        for item in group["items"]:
            if item["status"] == "running":
                item["status"] = "unconfirmed"
                group["counts"]["unconfirmed"] += 1
        group["state"] = "done"
        group["current"] = ""
        group["suffix"] = ""
        group["marks"] = 0
        timestamp = (event or {}).get("timestamp", "")
        if timestamp:
            group["finished"] = timestamp
        self._refresh_tools(group)

    def _refresh_tools(self, group):
        group["text"] = self._tools_text(group)
        if group.get("state") != "running":
            # 批次结束才收起动效：只留下最后一个三角形。
            group["suffix"] = ""
            group["marks"] = 0
        self._changed(group)

    @staticmethod
    def _tools_head_parts(group):
        """Head segments as (text, is_issue) so failures can be called out."""
        counts = group["counts"]
        parts = [("工具 ×" + str(counts["total"]), False)]
        if group.get("state") == "running" and group.get("current"):
            parts.append(("正在执行 " + str(group["current"]), False))
        else:
            for key, label in (("ok", "成功"), ("failed", "失败"),
                               ("needs_reconcile", "需核对"), ("unconfirmed", "未确认")):
                if counts.get(key):
                    parts.append((label + " " + str(counts[key]), key != "ok"))
            elapsed = TranscriptTools._elapsed(group.get("started"), group.get("finished"))
            if elapsed:
                parts.append((elapsed, False))
        return parts

    def _tools_head(self, group):
        """One-line summary of the batch; the counter itself is not a bubble."""
        return " · ".join(text for text, _ in self._tools_head_parts(group))

    def _tools_text(self, group):
        lines = [self._tools_head(group)]
        lines.extend(line["text"] for line in self._tool_lines(group, expanded=False))
        return "\n".join(lines)

    @classmethod
    def _tool_issue(cls, item):
        """True for receipts the user has to look at: failures and open questions."""
        return item["status"] not in cls.TOOL_OK_STATUSES and item["status"] != "running"

    @staticmethod
    def tool_data_key(item):
        """Workbench data key of a finished call; empty for local or unsaved calls."""
        sequence = item.get("sequence")
        return f"d_{sequence}_tool" if isinstance(sequence, int) and sequence > 0 else ""

    @classmethod
    def _tool_lines(cls, group, *, expanded):
        """Receipt lines with the severity and data reference of each entry."""
        items = group["items"] if expanded else cls._tool_highlights(group)
        lines = [{"text": cls._tool_line(item, expanded=expanded),
                  "issue": cls._tool_issue(item), "key": cls.tool_data_key(item)}
                 for item in items]
        if group.get("truncated"):
            lines.append({"text": "（明细已截断；可在“工具数据”面板查看全部）",
                          "issue": False, "key": ""})
        return lines

    @classmethod
    def _tool_line(cls, item, *, expanded):
        """Compact receipt line: name, status and summary; the fold adds duration."""
        parts = [str(item.get("name", "")),
                 cls.TOOL_STATUS_TEXT.get(item["status"], item["status"])]
        if expanded and item.get("duration"):
            parts.append(str(item["duration"]))
        detail = str(item.get("summary") or "")
        prefix = str(item.get("name", "")) + "："
        if detail.startswith(prefix):
            detail = detail[len(prefix):]
        if detail:
            parts.append(detail)
        return " · ".join(part for part in parts if part)

    def toggle_tools(self, message_id):
        """Expand or collapse one counter; returns False when the id is unknown."""
        message = self._tools_message(message_id)
        if message is None:
            return False
        message["expanded"] = not message.get("expanded")
        self._changed(message)
        return True

    def head_tool_key(self, message_id):
        """Data reference of the counter's first open issue, else of its first call."""
        message = self._tools_message(message_id)
        if message is None:
            return ""
        for item in (*self._tool_highlights(message), *message["items"]):
            key = self.tool_data_key(item)
            if key:
                return key
        return ""

    def _tools_message(self, message_id):
        try:
            wanted = int(message_id)
        except (TypeError, ValueError):
            return None
        for message in self.messages:
            if message["role"] == "tools" and message["message_id"] == wanted:
                return message
        return None

    @classmethod
    def _tool_highlights(cls, group):
        """Every receipt that is not a plain success stays visible."""
        return [item for item in group["items"]
                if cls._tool_issue(item)][: cls.TOOL_HIGHLIGHT_LIMIT]

    @staticmethod
    def _elapsed(started, finished):
        """Compact duration between two journal timestamps; empty when unknown."""
        if not started or not finished:
            return ""
        try:
            begin = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
        except ValueError:
            return ""
        seconds = (end - begin).total_seconds()
        if seconds < 0:
            return ""
        if seconds < 10:
            return f"{seconds:.1f}s"
        if seconds < 60:
            return f"{seconds:.0f}s"
        minutes, rest = divmod(int(seconds), 60)
        if minutes < 60:
            return f"{minutes}m{rest:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h{minutes:02d}m"
