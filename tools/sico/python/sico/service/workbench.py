"""Copilot application tools: explicit stage selection and evidence-bound publication."""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import datetime, timezone

from ..core.contracts import ToolResult
from ..core.tools import Tool
from ..storage.workbench import WorkbenchIndex, object_link

INSTRUCTIONS = (
    "用 set_copilot_stage 把实质性工程工作归属到具名工作和阶段。"
    "后续工作复用返回的 work_id/stage_id；一个模型回合结束并不代表工程阶段完成。"
    "记录阶段目标及其明确的验收标准。"
    "用 list_copilot_data 查找已记录的输入和工具证据。当任务或阶段达到可报告的结论时，"
    "调用 publish_copilot_report，提交 Markdown 并附上所用的确切证据 ID。"
    "写明单位、缺失证据和下一步。outcome 为 completed 表示所报告范围已完成，"
    "并不代表仿真规格通过。报告发布为带来源链接的固定版本；"
    "不得编造证据 ID、数字或 URL。"
    "这些应用工具既不执行 EDA 操作，也不申请新的执行许可。"
)

TEXT = {"type": "string", "minLength": 1, "maxLength": 200}
REF = {"type": "string", "minLength": 1, "maxLength": 96}


class WorkbenchError(ValueError):
    """A public, application-generated error that the model can act on."""


class WorkbenchService:
    def __init__(self, loop):
        self.loop = loop
        self.index = WorkbenchIndex(loop.journal).sync()

    def active(self):
        state = self.loop.state
        if state.task.get("status") != "executing" or state.task.get("origin") == "startup":
            raise WorkbenchError("Copilot work requires an active user task")
        self.index.sync()
        return self.index.executions[state.task["id"]]

    def emit(self, kind, payload):
        self.loop._event(kind, payload)
        self.index.sync()

    def stage(self, args, context):
        current = self.active()
        work_id, stage_id = args.get("work_id"), args.get("stage_id")
        if stage_id and not work_id:
            raise WorkbenchError("An existing stage requires its work_id")
        if current["work_id"] and work_id != current["work_id"]:
            raise WorkbenchError("Keep this execution in its selected work; reuse its work_id")
        if work_id:
            work = self.index.works.get(work_id)
            if work is None or work["title"] != args["work_title"]:
                raise WorkbenchError("Existing work identity/title does not match")
        else:
            work = {"id": "w_" + uuid.uuid4().hex, "title": args["work_title"]}
        if stage_id:
            stage = self.index.stages.get(stage_id)
            if (
                stage is None
                or stage["work_id"] != work["id"]
                or any(
                    stage[key] != args[key] for key in ("stage_title", "objective", "acceptance")
                )
            ):
                raise WorkbenchError("Existing stage identity or criteria do not match")
        else:
            stage = {
                "id": "s_" + uuid.uuid4().hex,
                "work_id": work["id"],
                "status": "in_progress",
                **{key: args[key] for key in ("stage_title", "objective", "acceptance")},
            }
        if current["stage_id"] == stage["id"]:
            return ToolResult(data={"work": work, "stage": stage})
        self.emit("workbench.stage.selected", {"work": work, "stage": stage})
        return ToolResult(data={"work": work, "stage": stage})

    def listing(self, args, context):
        current = self.active()
        work_id = args.get("work_id", current["work_id"])
        stage_id = args.get("stage_id", "")
        if work_id and work_id not in self.index.works:
            raise WorkbenchError("Unknown work_id")
        if stage_id and (
            stage_id not in self.index.stages or self.index.stages[stage_id]["work_id"] != work_id
        ):
            raise WorkbenchError("Stage does not belong to the selected work")
        rows = [
            row
            for row in self.index.data.values()
            if (not work_id or row["work_id"] == work_id)
            and (not stage_id or row["stage_id"] == stage_id)
            and row["data_type"] != "report"
        ]
        offset, limit = args.get("offset", 0), args.get("limit", 10)
        works = list(self.index.works.values())
        stages = [s for s in self.index.stages.values() if not work_id or s["work_id"] == work_id]
        return ToolResult(
            data={
                "works": works[offset : offset + limit],
                "stages": stages[offset : offset + limit],
                "work_total": len(works),
                "stage_total": len(stages),
                "identity_next_offset": (
                    offset + limit if offset + limit < max(len(works), len(stages)) else None
                ),
                "total": len(rows),
                "next_offset": offset + limit if offset + limit < len(rows) else None,
                "items": [
                    {
                        **{
                            k: row[k]
                            for k in (
                                "id",
                                "title",
                                "category",
                                "work_id",
                                "stage_id",
                                "task_id",
                                "status",
                                "digest",
                            )
                        },
                        **{
                            k: row[k]
                            for k in (
                                "call_id",
                                "input_refs",
                                "result_refs",
                                "requested",
                                "observed",
                                "timestamp",
                            )
                            if k in row
                        },
                        "url": object_link("data", self.index.session_id, row["id"]),
                    }
                    for row in rows[offset : offset + limit]
                ],
            }
        )

    def publish(self, args, context):
        current = self.active()
        if not current["stage_id"]:
            raise WorkbenchError("Select an engineering stage before publishing a report")
        evidence = self.evidence(args["evidence_ids"], current)
        previous = None
        if args.get("report_id"):
            candidates = [
                r
                for r in self.index.reports.values()
                if r["id"] == args["report_id"] and r["kind"] == "published"
            ]
            if not candidates:
                raise WorkbenchError("Report to revise is unavailable")
            previous = max(candidates, key=lambda r: r["version"])
            if previous["stage_id"] != current["stage_id"]:
                raise WorkbenchError("A report revision must remain in the same stage")
        report = {
            "id": previous["id"] if previous else "r_" + uuid.uuid4().hex,
            "version": previous["version"] + 1 if previous else 1,
            "work_id": current["work_id"],
            "stage_id": current["stage_id"],
            "task_id": self.loop.state.task["id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "evidence": evidence,
            **{key: args[key] for key in ("title", "markdown", "outcome", "next_steps")},
        }
        if previous and all(
            previous[k] == report[k]
            for k in ("title", "markdown", "outcome", "next_steps", "evidence")
        ):
            report = previous
        else:
            self.emit("workbench.report.published", report)
        key = report["id"] + "_v" + str(report["version"])
        return ToolResult(
            data={
                "report_id": report["id"], "version": report["version"],
                "url": object_link("report", self.index.session_id, key),
            }
        )

    def evidence(self, ids, current):
        evidence = []
        for key in dict.fromkeys(ids):
            if key not in self.index.data:
                raise WorkbenchError("Evidence is unavailable; use IDs from list_copilot_data")
            row = self.index.data[key]
            if row["work_id"] != current["work_id"] or row["data_type"] == "report":
                raise WorkbenchError("Evidence must be recorded data from this engineering work")
            evidence.append({"id": key, "digest": row["digest"]})
        try:
            self.index.verify_evidence(evidence)
        except (ValueError, OSError, TypeError, KeyError):
            raise WorkbenchError(
                "Evidence is unreadable or its checksum changed; submission not accepted"
            ) from None
        return evidence


def attach_workbench(loop):
    service = WorkbenchService(loop)
    definitions = [
        (
            "set_copilot_stage",
            "Select an engineering work and stage; reuse IDs to continue across "
            "user inputs. Titles and criteria of an existing stage are immutable; a changed goal "
            "requires a new stage. No CAD execution.",
            service.stage,
            {
                "work_title": TEXT,
                "stage_title": TEXT,
                "objective": {**TEXT, "maxLength": 2000},
                "acceptance": {**TEXT, "maxLength": 4000},
                "work_id": REF,
                "stage_id": REF,
            },
            ["work_title", "stage_title", "objective", "acceptance"],
        ),
        (
            "list_copilot_data",
            "List registered evidence IDs and existing work/stage identities. "
            "Use returned IDs when publishing; never infer file references.",
            service.listing,
            {
                "work_id": REF,
                "stage_id": REF,
                "offset": {"type": "integer", "minimum": 0, "maximum": 1000000},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            [],
        ),
        (
            "publish_copilot_report",
            "Publish an immutable Markdown report linked to exact recorded "
            "evidence. Select a stage first. completed describes reported scope, not specification "
            "qualification. Supply report_id to publish a new revision of that report.",
            service.publish,
            {
                "title": TEXT,
                "markdown": {**TEXT, "maxLength": 80000},
                "outcome": {
                    "type": "string",
                    "enum": ["completed", "incomplete", "failed", "cancelled"],
                },
                "next_steps": {"type": "string", "maxLength": 4000},
                "report_id": REF,
                "evidence_ids": {"type": "array", "items": REF, "minItems": 1, "maxItems": 100},
            },
            ["title", "markdown", "outcome", "next_steps", "evidence_ids"],
        ),
    ]
    from .audit import QUESTION, AuditService

    loop.audit = AuditService(service)
    definitions.append((
            "prepare_copilot_audit",
            "Register an evidence-bound decision before calling request_user_input. "
            "Use a stable request_id for idempotency. Returns the exact questions to ask; "
            "this preparation does not wait for the user or authorize CAD operations.",
            loop.audit.prepare,
            {
                "request_id": REF, "title": TEXT,
                "recommendation": {**TEXT, "maxLength": 2000},
                "rationale": {**TEXT, "maxLength": 4000},
                "questions": {"type": "array", "items": QUESTION, "minItems": 1, "maxItems": 3},
                "evidence_ids": {"type": "array", "items": REF, "minItems": 1, "maxItems": 100},
            },
            ["request_id", "title", "recommendation", "rationale", "questions", "evidence_ids"],
        ))
    for name, description, handler, properties, required in definitions:
        schema = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

        def invoke(args, context, handler=handler):
            try:
                with getattr(loop, "lock", nullcontext()):
                    return handler(args, context)
            except (WorkbenchError, ValueError) as exc:
                return ToolResult("tool_error", str(exc))

        loop.tools.register(
            Tool(
                name,
                description,
                schema,
                lambda args, schema=schema: validate_arguments(args, schema),
                invoke,
                execution_domain="local",
                annotations={
                    "readOnlyHint": name == "list_copilot_data",
                    "destructiveHint": False,
                    "openWorldHint": False,
                    "idempotentHint": name == "list_copilot_data",
                },
            )
        )
    # Recorded sessions and older prompts still call the legacy studio:* names
    # (the tools predate the Silicon Copilot rename).
    for legacy, name in (
        ("set_studio_stage", "set_copilot_stage"),
        ("list_studio_data", "list_copilot_data"),
        ("publish_studio_report", "publish_copilot_report"),
        ("prepare_studio_audit", "prepare_copilot_audit"),
    ):
        loop.tools.alias(legacy, name)
    return service


def validate_arguments(args, schema):
    from cadai.circuit_spec_schema import validate

    # Circuit identifiers disallow newlines; report prose needs a bounded multiline field.
    multiline = {
        "markdown", "objective", "acceptance", "next_steps", "recommendation", "rationale"
    } & schema["properties"].keys()
    if not isinstance(args, dict):
        raise ValueError("Expected an object")
    for key in multiline:
        rule = schema["properties"][key]
        value = args.get(key)
        if (
            not isinstance(value, str)
            or not rule.get("minLength", 0) <= len(value) <= rule["maxLength"]
            or (rule.get("minLength", 0) and not value.strip())
            or any((ord(c) < 32 and c not in "\n\r\t") or ord(c) == 127 for c in value)
        ):
            raise ValueError(key + ": expected bounded text")
    validate(
        {k: v for k, v in args.items() if k not in multiline},
        {
            **schema,
            "properties": {k: v for k, v in schema["properties"].items() if k not in multiline},
            "required": [k for k in schema["required"] if k not in multiline],
        },
    )
