"""Prepare verified detail content off the Qt thread."""

from __future__ import annotations

from datetime import datetime
from html import escape

from ..core.links import object_link
from ..storage.native_media import raster_data, web_url
from .display import FIELD_LABELS, data_html, target_label
from .native_display import native_parts
from .schematic_preview import preview_images
from .tool_display import TITLES, result_html, table

CATEGORIES = {"source": "来源", "process": "过程", "output": "产出"}
STATUSES = {
    "completed": "范围已完成",
    "incomplete": "尚未完成",
    "failed": "失败",
    "cancelled": "已取消",
    "needs_reconcile": "待核对",
    "executing": "执行中",
    "in_progress": "进行中",
    "recorded": "已记录",
    "ok": "已返回",
    "tool_error": "工具失败",
    "invalid_arguments": "参数无效",
    "preflight_failed": "预检未通过",
}


def title(row):
    value = row["title"]
    label = TITLES.get(value, value)
    location = row.get("observed") or row.get("requested") or {}
    parts = [
        str(location[k])
        for k in ("history", "test", "corner", "point")
        if k in location and isinstance(location[k], (str, int))
    ]
    return label + (" · " + " / ".join(parts) if parts else "")


class DetailContent:
    def __init__(self, index, *, compact=False):
        self.index = index
        self.compact = compact
        self.notice = ""
        self.images = []
        self.external_urls = []

    def show_notice(self, text):
        self.notice = text

    def prepare(self, kind, key):
        self.notice, self.images, self.external_urls = "", [], []
        row = self.index.resolve(kind, key)
        if kind == "report":
            content = self.report(row)
        elif kind == "audit":
            content = self.audit(row)
        else:
            content = self.evidence(row)
        return {"title": title(row), "notice": self.notice, "images": self.images,
                "external_urls": self.external_urls, **content}

    def link(self, kind, key, label):
        href = object_link(kind, self.index.session_id, key)
        return "<a href='" + escape(href, quote=True) + "'>" + escape(label) + "</a>"

    def links(self, heading, rows):
        if not rows:
            return ""
        return (
            "<h3>"
            + heading
            + "</h3><ul>"
            + "".join("<li>" + self.link(kind, key, label) + "</li>" for kind, key, label in rows)
            + "</ul>"
        )

    def metadata(self, row):
        stage = self.index.stages.get(row.get("stage_id"), {})
        work = self.index.works.get(row.get("work_id"), {})
        timestamp = row.get("timestamp")
        try:
            timestamp = datetime.fromisoformat(timestamp).astimezone()
            timestamp = timestamp.strftime("%Y-%m-%d %H:%M:%S %Z")
        except (ValueError, TypeError):
            timestamp = "旧记录未提供"
        return data_html(
            {
                "任务": work.get("title", "未关联工程任务"),
                "阶段": stage.get("stage_title", "未关联阶段"),
                "目标": stage.get("objective", "未提供"),
                "验收条件": stage.get("acceptance", "未提供"),
                "记录时间": timestamp,
            }
        )

    def report(self, row):
        parts = []
        if row["kind"] == "execution":
            parts.append("<p><b>执行记录，尚未发布为工程报告。</b></p>")
        else:
            parts.append(
                "<p>版本 "
                + str(row["version"])
                + " · "
                + escape(STATUSES.get(row["outcome"], row["outcome"]))
                + "</p>"
            )
        try:
            self.index.verify_evidence(row["evidence"])
        except (ValueError, OSError, TypeError, KeyError):
            self.show_notice("部分来源数据暂不可读或校验失败；报告正文为发布时版本。")
        for ref in row["evidence"][:32]:
            try:
                images, notice = preview_images(
                    self.index.evidence_value(ref["id"]), self.index.reader)
                if images:
                    self.images.extend(images)
                    break
                if notice:
                    self.show_notice(notice)
            except (ValueError, OSError, TypeError, KeyError):
                continue
        suffix = []
        if row["next_steps"]:
            suffix.append(
                "<h3>下一步</h3><p>" + escape(row["next_steps"]).replace("\n", "<br>") + "</p>"
            )
        suffix.append(
            self.links(
                "来源数据",
                [
                    ("data", ref["id"], title(self.index.data[ref["id"]]))
                    for ref in row["evidence"]
                    if ref["id"] in self.index.data
                ],
            )
        )
        revisions = [r for r in self.index.reports.values() if r["id"] == row["id"] and r != row]
        suffix.append(
            self.links(
                "其他版本",
                [("report", r["key"], f"v{r['version']} · {r['title']}") for r in revisions],
            )
        )
        suffix.append("<h3>任务与阶段</h3>" + self.metadata(row))
        return {"prefix": "".join(parts), "markdown": row["markdown"],
                "suffix": "".join(suffix)}

    def audit(self, row):
        """Render a decision request and its answer without exposing protocol envelopes."""
        parts = ["<h2>" + escape(row["title"]) + "</h2>"]
        if "elicitation" in row:
            from ..codex.elicitation_schema import status_text

            parts.append("<p>" + escape(status_text(row)) + "</p>")
            spec = row["elicitation"]
            if spec["mode"] == "url":
                parts.append("<p>" + escape(spec["url"]) + "</p>")
            else:
                parts.append("<ul>" + "".join(
                    "<li>" + escape(field.get("title") or key) + "："
                    + escape(field.get("description") or field["type"]) + "</li>"
                    for key, field in spec["schema"]["properties"].items()) + "</ul>")
        if row.get("recommendation"):
            parts.append("<p><b>建议：</b>" + escape(row["recommendation"]) + "</p>")
        if row.get("rationale"):
            parts.append("<p><b>依据：</b>" + escape(row["rationale"]) + "</p>")
        parts.append(
            "<h3>需要确认</h3><ol>"
            + "".join(
                "<li><b>" + escape(q["header"]) + "</b>：" + escape(q["question"])
                + ("<ul>" + "".join(
                    "<li>" + escape(o["label"]) + "：" + escape(o["description"]) + "</li>"
                    for o in q["options"]
                ) + "</ul>" if q["options"] else "")
                + "</li>"
                for q in row["questions"]
            )
            + "</ol>"
        )
        if row.get("reply"):
            if "elicitation" in row:
                from ..codex.elicitation_schema import reply_text

                reply = "<p>" + escape(reply_text(row, row["reply"])).replace("\n", "<br>") + "</p>"
            else:
                reply = data_html(row["reply"]["answers"])
            parts.append(
                "<h3>用户答复</h3>"
                + reply
            )
        if row.get("reason"):
            parts.append("<p>" + escape(row["reason"]) + "</p>")
        parts.append(
            self.links(
                "触发证据",
                [
                    ("data", ref["id"], title(self.index.data[ref["id"]]))
                    for ref in row.get("evidence", []) if ref["id"] in self.index.data
                ],
            )
        )
        if row.get("pdk_update_ref"):
            from .pdk_update_review import markdown

            return {"prefix": "".join(parts), "markdown": markdown(self.index, row)}
        return {"html": "".join(parts)}

    def evidence(self, row):
        captured, captured_limited = "", False
        parts = [
            "<p>"
            + escape(CATEGORIES[row["category"]])
            + " · "
            + escape(STATUSES.get(row["status"], row["status"]))
            + "</p>"
        ]
        try:
            value = self.index.evidence_value(row["id"])
            if row.get("native"):
                native_value = value.get("data", value)
                native = native_parts(native_value, self.index.reader, inputs=row.get("inputs"))
                parts.append(native["html"])
                captured, captured_limited = native["output"], native["output_truncated"]
                item = native_value.get("item", {})
                if item.get("type") == "imageGeneration":
                    image = raster_data(item.get("result"))
                    if image:
                        self.images.append(image)
                    elif web_url(item.get("result")):
                        self.external_urls.append(item["result"])
                if item.get("type") == "webSearch":
                    for result in [item.get("action"), *(item.get("results") or [])[:100]]:
                        if isinstance(result, dict) and web_url(result.get("url")):
                            self.external_urls.append(result["url"])
                if "origin" in row:
                    origin = row["origin"]
                    original_task = (
                        self.index.executions.get(origin["task_id"], {}) if origin else {}
                    )
                    original_time = native_value.get("completed_at")
                    if type(original_time) is int:
                        try:
                            original_time = (
                                datetime.fromtimestamp(original_time).astimezone()
                                .strftime("%Y-%m-%d %H:%M:%S %Z")
                            )
                        except (ValueError, OSError, OverflowError):
                            original_time = None
                    parts.append(data_html({"证据来源": "原生历史补采",
                                            "原任务": original_task.get("title", "未归属"),
                                            "原目标": target_label(origin["context"]["snapshot"])
                                            if origin else "未提供",
                                            "原回合完成时间": original_time}))
                native = row["native"]
                parts.append(data_html({"Codex 会话": native["thread_id"],
                                        "Codex 回合": native["turn_id"],
                                        "Codex 操作": native["item_id"] or "回合汇总"}))
            elif row["data_type"] == "tool":
                images, notice = preview_images(value, self.index.reader)
                self.images.extend(images)
                if notice:
                    self.show_notice(notice)
                material = self.index.material(row["id"])
                if material is not None:
                    parts.append(
                        "<h3>归档数据</h3>"
                        + self.material_html(
                            material,
                            compact=self.compact,
                        )
                    )
                else:
                    parts.append(
                        result_html(
                            value,
                            context=row["tool"] in {"get_context", "get_entry_context"},
                        )
                    )
            elif row["data_type"] == "report":
                report = self.index.reports[row["report_key"]]
                parts.append(self.link("report", report["key"], "查看报告"))
            else:
                parts.append(data_html(value))
        except (ValueError, OSError, TypeError, KeyError):
            self.show_notice("来源数据暂不可读或校验失败，原始记录保留。")
        parts.append("<h3>任务与阶段</h3>" + self.metadata(row))
        if row.get("inputs") and not row.get("native"):
            parts.append("<h3>查询与执行条件</h3>" + data_html(row["inputs"]))
        parts.append(
            self.links(
                "来源关系",
                [
                    ("data", key, title(self.index.data[key]))
                    for key in row["parents"]
                    if key in self.index.data
                ],
            )
        )
        reports = [
            r
            for r in self.index.report_list()
            if any(ref["id"] == row["id"] for ref in r["evidence"])
        ]
        parts.append(
            self.links("引用此数据的报告", [("report", r["key"], r["title"]) for r in reports])
        )
        children = [r for r in self.index.data.values() if row["id"] in r["parents"]]
        parts.append(self.links("后续数据", [("data", r["id"], title(r)) for r in children]))
        # 输出单独交给详情页下方文本区（见 COPILOT_DETAIL_CODE_OUTPUT_PLAN.md §4.4）。
        return {"html": "".join(parts), "output": captured,
                "output_truncated": bool(captured_limited)}

    @staticmethod
    def material_html(value, *, compact=False):
        rows = value.get("rows", value.get("items", value.get("outputs", [])))
        primary = ("target", "history", "qualification", "spec_qualified", "coverage_complete")
        metadata = {k: value[k] for k in primary if k in value}
        target = metadata.get("target")
        if isinstance(target, list) and len(target) == 3:
            metadata["target"] = " / ".join(str(v) for v in target)
        if metadata.get("qualification") in {"pass", "fail", "incomplete"}:
            metadata["qualification"] = {
                "pass": "满足所列规格",
                "fail": "不满足所列规格",
                "incomplete": "证据不完整",
            }[metadata["qualification"]]
        parts = [data_html(metadata)]
        if isinstance(rows, list) and rows and all(isinstance(row, dict) for row in rows):
            preferred = ("test", "corner", "point", "output", "metric", "value", "unit", "status")
            keys = [k for k in preferred if any(k in row for row in rows[:100])]
            if not keys:
                keys = list(
                    dict.fromkeys(
                        k
                        for row in rows[:100]
                        for k, v in row.items()
                        if not isinstance(v, (dict, list))
                    )
                )[:8]

            def scalar(value):
                if value is None:
                    return "未提供"
                if isinstance(value, bool):
                    return "是" if value else "否"
                if isinstance(value, (dict, list)):
                    return "见行明细"
                return value

            if compact:
                parts.extend(
                    "<h4>" + str(n + 1) + "</h4>" + data_html({k: r.get(k) for k in keys})
                    for n, r in enumerate(rows[:100])
                )
            else:
                parts.append(
                    table(
                        [FIELD_LABELS.get(k, k) for k in keys],
                        [[scalar(r.get(k)) for k in keys] for r in rows[:100]],
                        wrap=True,
                    )
                )
            nested = [{k: v for k, v in row.items() if k not in keys} for row in rows[:100]]
            if any(nested):
                parts.append(
                    "<h3>行明细</h3>"
                    + data_html(
                        {str(n + 1): details for n, details in enumerate(nested) if details}
                    )
                )
            if len(rows) > 100:
                parts.append(f"<p>显示前 100 行，共 {len(rows)} 行。</p>")
        samples = value.get("samples")
        if isinstance(samples, list):
            parts.append(f"<p>已归档 {len(samples)} 个采样点。</p>" + data_html(samples[:30]))
        details = {
            k: v
            for k, v in value.items()
            if k not in {*primary, "rows", "items", "outputs", "samples"}
        }
        if details:
            parts.append("<h3>归档条件与来源</h3>" + data_html(details))
        return "".join(parts)
