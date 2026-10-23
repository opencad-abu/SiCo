"""Read-only Work/Stage/Execution detail projections for the task tree."""

from __future__ import annotations

from html import escape

from .detail_content import DetailContent, STATUSES
from .display import data_html
from .task_tree import (
    TERMINAL, stage_conflict, stage_facts, stage_status, work_status,
)

STAGE_LABELS = {
    "planned": "计划中",
    "active": "进行中",
    "blocked": "阻塞",
    "reported": "已报告",
    "closed": "已关闭",
    "in_progress": "进行中",
}
WORK_LABELS = {
    "active": "进行中",
    "blocked": "阻塞",
    "reported": "已报告",
    "closed": "已关闭",
    "idle": "空闲",
}
CHILD_LABELS = {
    "started": "执行中",
    "completed": "已完成",
    "failed": "失败",
    "interrupted": "中断",
    "closed": "已关闭",
}
ORIGIN_LABELS = {
    "chat": "用户任务",
    "quick": "快速输入",
    "startup": "初始化上下文",
    "thread_operation": "会话操作",
}


def _label(mapping, value):
    return mapping.get(value, value or "未提供")


def _broken(text):
    return escape(str(text or "")).replace("\n", "<br>")


def _short(value, length=12):
    return escape(str(value or "")[:length])


def _table(headers, rows):
    if not rows:
        return "<p>暂无记录。</p>"
    head = "".join("<th>" + escape(value) + "</th>" for value in headers)
    body = "".join("<tr>" + "".join("<td>" + value + "</td>" for value in row) + "</tr>"
                   for row in rows)
    return ("<table border='1' cellspacing='0' cellpadding='5'><tr>"
            + head + "</tr>" + body + "</table>")


class TaskDetailContent:
    def __init__(self, index, *, compact=False):
        self.index = index
        self.compact = compact

    def prepare(self, kind, key):
        if kind == "task":
            return self.task(key)
        if kind == "stage":
            return self.stage(key)
        if kind == "work":
            return self.work(key)
        raise ValueError("Unsupported task detail")

    def _execution_table(self, executions):
        content = DetailContent(self.index, compact=self.compact)
        rows = []
        for task_id, execution in executions:
            report = "execution_" + task_id
            link = (content.link("report", report, "执行记录")
                    if report in self.index.reports else "")
            rows.append((
                escape(_label(STATUSES, execution.get("status", ""))),
                escape(str(execution.get("title", ""))[:200]),
                _short(task_id),
                link,
            ))
        return _table(("状态", "请求", "任务", "记录"), rows)

    def _report_links(self, reports):
        content = DetailContent(self.index, compact=self.compact)
        links = [
            content.link("report", row["key"], "v" + str(row.get("version", "")) + " · "
                         + str(row.get("title", "")))
            for row in reports
            if row.get("key") in self.index.reports
        ]
        return "<ul>" + "".join("<li>" + link + "</li>" for link in links) + "</ul>" if links else ""

    def task(self, key):
        execution = self.index.executions.get(key)
        if execution is None:
            raise ValueError("Task detail is unavailable")
        status = execution.get("status", "")
        terminal = status in TERMINAL
        evidence = [
            {"id": row["id"], "digest": row["digest"]}
            for row in self.index.data.values()
            if row.get("task_id") == key and row.get("data_type") != "report"
            and row.get("digest")
        ][:32]
        row = {
            "id": key,
            "key": key,
            "kind": "task",
            "title": execution.get("title", ""),
            "version": 1,
            "outcome": status,
            "status": status,
            "markdown": execution.get("text", "") if terminal else "",
            "next_steps": "",
            "work_id": execution.get("work_id", ""),
            "stage_id": execution.get("stage_id", ""),
            "timestamp": execution.get("finished_at") or execution.get("started_at", ""),
            "evidence": evidence,
        }
        content = DetailContent(self.index, compact=self.compact)
        result = content.report(row)
        fields = {
            "任务状态": _label(STATUSES, status),
            "任务 ID": key,
            "输入 ID": execution.get("input_id", ""),
            "来源": _label(ORIGIN_LABELS, execution.get("origin", "")),
            "开始时间": execution.get("started_at", ""),
            "结束时间": execution.get("finished_at", "") or "未结束",
        }
        if any(execution.get(name) for name in (
                "input_tokens", "output_tokens", "total_tokens")):
            fields["词元"] = (
                "输入 " + str(execution.get("input_tokens", 0))
                + " · 输出 " + str(execution.get("output_tokens", 0))
                + " · 合计 " + str(execution.get("total_tokens", 0))
            )
        header = data_html(fields)
        links = []
        input_id = str(execution.get("input_id", ""))
        if input_id:
            links.append(
                "<a href='locate-input:" + escape(input_id, quote=True)
                + "'>在对话中定位</a>")
        links.append("<a href='copy-request:" + escape(key, quote=True) + "'>复制请求</a>")
        links.append("<a href='copy-result:" + escape(key, quote=True) + "'>复制结果</a>")
        work_id = str(execution.get("work_id", ""))
        stage_id = str(execution.get("stage_id", ""))
        if work_id:
            links.append(
                "<a href='work:" + escape(work_id, quote=True) + "'>查看工程任务</a>")
        if stage_id:
            links.append(
                "<a href='stage:" + escape(stage_id, quote=True) + "'>查看阶段计划</a>")
        reports = [
            report for report in self.index.reports.values()
            if report.get("kind") == "published" and report.get("stage_id") == stage_id
        ]
        for report in reports[:8]:
            links.append(content.link(
                "report", report["key"],
                "已发布报告 v" + str(report.get("version", "")) + " · "
                + str(report.get("title", "")),
            ))
        if links:
            header += "<p>" + " · ".join(links) + "</p>"
        children = list((execution.get("children") or {}).values())
        children.sort(key=lambda row: (
            0 if row.get("status") in {"failed", "interrupted"} else 1,
            str(row.get("updated_at", "")),
            str(row.get("name", "")),
        ))
        children = children[:20]
        if children:
            rows = [(
                escape(_label(CHILD_LABELS, row.get("status", ""))),
                escape(str(row.get("name", ""))),
                escape(str(row.get("thread_id", ""))),
            ) for row in children]
            header += "<h3>子任务与卡点</h3>" + _table(("状态", "子任务", "线程"), rows)
            blockers = [
                row for row in children
                if row.get("status") in {"failed", "interrupted"}
            ]
            if blockers:
                header += "<p><b>卡点：</b>" + "、".join(
                    escape(str(row.get("name", ""))) for row in blockers
                ) + "</p>"
        if execution.get("diagnostic"):
            header += "<p><b>诊断：</b>" + _broken(execution["diagnostic"]) + "</p>"
        result["prefix"] = header + result.get("prefix", "")
        result["title"] = execution.get("title", "")
        result["notice"] = content.notice or (
            "" if terminal else "任务执行中，最终结果尚未产生。"
        )
        result["images"] = content.images
        result["external_urls"] = content.external_urls
        if not result.get("markdown"):
            result["markdown"] = "（任务尚未产生最终回复）"
        return result

    def stage(self, key):
        stage = self.index.stages.get(key)
        if stage is None:
            raise ValueError("Stage detail is unavailable")
        work = self.index.works.get(stage.get("work_id"), {})
        executions = [
            (task_id, execution)
            for task_id, execution in self.index.executions.items()
            if execution.get("stage_id") == key
        ]
        status = stage_status(stage, [execution for _, execution in executions])
        facts = stage_facts(stage, [execution for _, execution in executions])
        source = str(stage.get("status_source", ""))
        source_label = (
            "模型判断" if source == "model"
            else "阶段计划" if source == "plan"
            else "报告事实" if facts["reported"]
            else "记录/派生"
        )
        reports = [
            row for row in self.index.reports.values()
            if row.get("stage_id") == key and row.get("kind") == "published"
        ]
        blockers = [
            execution for _, execution in executions
            if execution.get("status") in {"failed", "needs_reconcile"}
        ]
        fields = {
            "工程任务": work.get("title", "未关联工程任务"),
            "阶段状态": _label(STAGE_LABELS, status),
            "状态来源": source_label,
            "目标": stage.get("objective", "未提供"),
            "验收条件": stage.get("acceptance", "未提供"),
        }
        if stage.get("reason"):
            fields["原因"] = stage["reason"]
        if stage.get("blocker"):
            fields["阻塞"] = stage["blocker"]
        if stage.get("next_step"):
            fields["下一步"] = stage["next_step"]
        if stage.get("plan_revision"):
            fields["计划版本"] = str(stage["plan_revision"])
        if isinstance(stage.get("order"), int):
            fields["计划顺序"] = str(stage["order"] + 1)
        dependencies = [
            self.index.stages.get(stage_id, {}).get("stage_title", stage_id)
            for stage_id in stage.get("depends_on") or []
        ]
        if dependencies:
            fields["依赖阶段"] = "、".join(dependencies)
        plan = self.index.plans.get(stage.get("work_id"), {})
        if plan.get("note"):
            fields["计划说明"] = plan["note"]
        parts = [data_html(fields), data_html({
            "执行中/排队": "是" if facts["active"] else "否",
            "执行失败/待核对": "是" if facts["blocked"] else "否",
            "已有发布报告": "是" if facts["reported"] else "否",
        })]
        if stage_conflict(stage, facts):
            parts.append("<p><b>冲突：</b>模型判断与执行/报告事实不一致，请核对。</p>")
        if status == "planned":
            parts.append("<p>阶段尚未开始；选择后才会关联执行。</p>")
        parts.append("<h3>执行尝试</h3>" + self._execution_table(executions))
        if reports:
            parts.append("<h3>报告</h3>" + self._report_links(reports))
        if blockers:
            parts.append(
                "<h3>阻塞</h3><ul>"
                + "".join(
                    "<li>" + escape(_label(STATUSES, row.get("status", ""))) + "："
                    + escape(str(row.get("diagnostic", "")) or row.get("title", ""))
                    + "</li>"
                    for row in blockers
                )
                + "</ul>"
            )
        return {"title": stage.get("stage_title", ""), "prefix": "".join(parts)}

    def work(self, key):
        work = self.index.works.get(key)
        if work is None:
            raise ValueError("Work detail is unavailable")
        stages = [
            stage for stage in self.index.stages.values()
            if stage.get("work_id") == key
        ]
        rows = []
        stage_states = []
        total = 0
        for stage in stages:
            executions = [
                (task_id, execution)
                for task_id, execution in self.index.executions.items()
                if execution.get("stage_id") == stage.get("id")
            ]
            total += len(executions)
            status = stage_status(stage, [execution for _, execution in executions])
            stage_states.append({"status": status})
            blocked = sum(
                execution.get("status") in {"failed", "needs_reconcile"}
                for _, execution in executions
            )
            rows.append((
                escape(str(stage.get("stage_title", ""))),
                escape(_label(STAGE_LABELS, status)),
                str(len(executions)),
                str(blocked) if blocked else "—",
            ))
        status = work_status(stage_states)
        parts = [data_html({
            "工程任务": work.get("title", ""),
            "状态": _label(WORK_LABELS, status),
            "阶段数": len(stages),
            "执行尝试": total,
        })]
        parts.append("<h3>阶段</h3>" + _table(
            ("阶段", "状态", "执行", "阻塞"), rows
        ))
        return {"title": work.get("title", ""), "prefix": "".join(parts)}
