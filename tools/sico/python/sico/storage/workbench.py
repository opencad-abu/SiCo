"""Replayable engineering work, evidence and immutable reports from public events."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from ..core.links import object_link as object_link
from ..core.links import parse_link as parse_link
from ..transport.framing import strict_json
from .journal import MAX_RECORD, SessionJournal
from .migration_locations import target_for
from .native_origin import NativeOrigins, native_title

LOCAL_TOOLS = frozenset({
    "set_copilot_stage", "list_copilot_data", "publish_copilot_report", "prepare_copilot_audit",
})
# 历史会话记录的是旧产品名（Copilot Studio）时期的工具名，投影时一并当作本地工具。
RECORDED_LOCAL_TOOLS = LOCAL_TOOLS | {
    "set_studio_stage", "list_studio_data", "publish_studio_report", "prepare_studio_audit",
}
TERMINAL = {"completed", "failed", "cancelled", "needs_reconcile"}
REFERENCE_KEYS = frozenset(
    {
        "result_ref",
        "report_ref",
        "waveform_ref",
        "input_ref",
        "output_ref",
        "measurement_ref",
        "response_ref",
        "context_ref",
        "setup_ref",
        "preview_ref",
        "prepare_ref",
        "task_ref",
        "plan_ref",
        "symbol_ref",
        "pdk_binding",
        "binding_ref",
        "waveform_refs",
        "measurement_refs",
    }
)
COORDINATE_KEYS = ("history", "test", "corner", "point", "output", "signal", "target")


def coordinates(value):
    if not isinstance(value, dict):
        return {}
    return {k: deepcopy(value[k]) for k in COORDINATE_KEYS if k in value}


def content_digest(value):
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def references(value, depth=0):
    if depth > 16:
        return set()
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in REFERENCE_KEYS:
                if isinstance(item, str):
                    result.add(item)
                elif key == "waveform_refs" and isinstance(item, dict):
                    result.update(v for v in item.values() if isinstance(v, str))
                elif key == "measurement_refs" and isinstance(item, list):
                    result.update(v for v in item if isinstance(v, str))
                elif isinstance(item, (dict, list)):
                    result.update(references(item, depth + 1))
            elif isinstance(item, (dict, list)):
                result.update(references(item, depth + 1))
    elif isinstance(value, list):
        for item in value:
            result.update(references(item, depth + 1))
    return result


def _category(name, inputs):
    if name == "execute_circuit_operation":
        return "process" if inputs.get("operation") == "run_simulation_setup" else "output"
    if name.startswith(("preview_", "prepare_", "measure_", "evaluate_", "bind_", "begin_")):
        return "process"
    return "source"


class WorkbenchIndex:
    def __init__(self, reader):
        self.reader, self.session_id = reader, reader.session_id
        self.sequence = 0
        self.works, self.stages, self.executions = {}, {}, {}
        self.data, self.reports, self.calls, self.producers = {}, {}, {}, {}
        self.native_outputs = {}
        self.native_origins = NativeOrigins()
        self.audits = {}

    def sync(self):
        events = (
            self.reader.events(self.sequence)
            if isinstance(self.reader, SessionJournal)
            else self.reader.events()
        )
        for event in events:
            self.receive(event)
        return self

    def association(self, task, sequence):
        selections = self.executions.get(task, {}).get("selections", [])
        if not selections:
            return {"work_id": "", "stage_id": ""}
        earlier = [s for s in selections if s["sequence"] <= sequence]
        return (earlier or selections[:1])[-1]

    def decisions(self, task, stage, sequence):
        return [r["id"] for r in self.audits.values()
                if r["task_id"] == task and r["stage_id"] == stage
                and 0 < r.get("resumed_sequence", 0) < sequence]

    def _data(self, event, suffix, title, category, value, *, parents=(), **extra):
        execution = self.executions.get(event.get("task_id"), {})
        key = f"d_{event['sequence']}_{suffix}"
        row = {
            "id": key,
            "title": title,
            "category": category,
            "value": deepcopy(value),
            "digest": content_digest({"value": value, "inputs": extra.get("inputs", {})}),
            "sequence": event["sequence"],
            "task_id": event.get("task_id", ""),
            "work_id": execution.get("work_id", ""),
            "stage_id": execution.get("stage_id", ""),
            "parents": list(dict.fromkeys(parents)),
            "timestamp": event.get("timestamp", ""),
            "status": "recorded",
            **extra,
        }
        self.data[key] = row
        return key

    def receive(self, event):
        if event.get("session_id") != self.session_id:
            raise ValueError("Workbench event belongs to another session")
        sequence = event["sequence"]
        if sequence <= self.sequence:
            return
        if sequence != self.sequence + 1:
            raise ValueError("Workbench event sequence is incomplete")
        kind, payload, task = event["kind"], event["payload"], event.get("task_id", "")
        if kind in {"codex.history.item", "codex.history.output"}:
            self.native_origins.validate(payload, require_origin=kind == "codex.history.output")
        if "elicitation_origin" in payload:
            self.native_origins.validate_input(payload["elicitation_origin"], task)
        if "input_origin" in payload:
            self.native_origins.validate_input(payload["input_origin"], task, child=True)
        self.native_origins.observe(event)
        self.sequence = sequence
        if kind == "task.started":
            self.executions[task] = {
                "title": payload["text"],
                "status": "executing",
                "work_id": "",
                "stage_id": "",
                "startup": payload.get("origin") == "startup",
                "text": "",
                "context": payload["context"],
                "selections": [],
            }
            parents = []
            if not self.executions[task]["startup"]:
                request = self._data(
                    event,
                    "input",
                    payload["text"],
                    "source",
                    {"text": payload["text"]},
                    data_type="request",
                )
                self.executions[task]["request"] = request
                parents.append(request)
            source = self._data(
                event,
                "source",
                "设计来源快照",
                "source",
                payload["context"],
                data_type="context",
                parents=parents,
            )
            self.executions[task]["source"] = source
        elif kind == "workbench.stage.selected":
            work, stage = deepcopy(payload["work"]), deepcopy(payload["stage"])
            self.works[work["id"]], self.stages[stage["id"]] = work, stage
            self.executions[task].update(work_id=work["id"], stage_id=stage["id"])
            self.executions[task]["selections"].append(
                {
                    "sequence": sequence,
                    "work_id": work["id"],
                    "stage_id": stage["id"],
                }
            )
            for row in self.data.values():
                if row["task_id"] == task and not row["stage_id"]:
                    row.update(work_id=work["id"], stage_id=stage["id"])
        elif kind == "tool.started":
            if payload["name"] not in RECORDED_LOCAL_TOOLS:
                self.calls[(task, payload["id"])] = {**deepcopy(payload), "sequence": sequence}
        elif kind in {"tool.finished", "session.tool_receipt"} and payload["name"] not in RECORDED_LOCAL_TOOLS:
            if kind == "session.tool_receipt":
                task = payload["task_id"]
                event = {**event, "task_id": task}
            call = self.calls.pop((task, payload["id"]), payload if kind == "session.tool_receipt" else {})
            execution = self.executions.get(task, {})
            inputs, result = call.get("input", {}), payload["result"]
            parents = [execution[k] for k in ("request", "source") if k in execution]
            if payload.get("native"):
                native_key = (task, content_digest(payload["native"]))
                parents += self.native_outputs.pop(native_key, [])
            parents += [
                self.producers[ref] for ref in sorted(references(inputs)) if ref in self.producers
            ]
            association = self.association(task, call.get("sequence", sequence))
            audit_ids = self.decisions(
                task, association["stage_id"], call.get("sequence", sequence)
            )
            parents += [self.audits[key]["reply_data"] for key in audit_ids]
            key = self._data(
                event,
                "tool",
                payload["name"],
                "process" if payload.get("native") else _category(payload["name"], inputs),
                result,
                parents=parents,
                data_type="tool",
                tool=payload["name"],
                inputs=inputs,
                status=result.get("status", "unknown"),
                work_id=association["work_id"],
                stage_id=association["stage_id"],
                audit_ids=audit_ids,
                **({"native": deepcopy(payload["native"])} if payload.get("native") else {}),
            )
            try:
                resolved = self.evidence_value(key)
            except (ValueError, OSError, TypeError, KeyError):
                resolved = result
            self.data[key].update(
                call_id=payload["id"],
                input_refs=sorted(references(inputs)),
                result_refs=sorted(references(resolved)),
                requested=coordinates(inputs),
                observed=coordinates(resolved.get("data")),
            )
            output_refs = references(resolved) if result.get("status") == "ok" else set()
            for ref in output_refs - references(inputs):
                self.producers.setdefault(ref, key)
        elif kind in {"codex.item.output", "codex.plan.updated", "codex.diff.updated",
                      "codex.native.updated", "codex.history.item", "codex.history.output"}:
            association = {}
            historical = kind in {"codex.history.item", "codex.history.output"}
            if historical:
                task = (payload.get("origin") or {}).get("task_id", "")
                event = {**event, "task_id": task}
                if payload.get("origin"):
                    association = self.association(task, payload["origin"]["turn_sequence"])
            execution = self.executions.get(task, {})
            suffix = {"codex.item.output": "native_output", "codex.history.output": "native_output",
                      "codex.plan.updated": "plan",
                      "codex.diff.updated": "diff", "codex.native.updated": "native",
                      "codex.history.item": "history"}[kind]
            value = ({"artifact": payload["artifact"]} if suffix == "native_output"
                     else payload["value"])
            native_key = (task, content_digest(payload["native"]))
            parents = [execution[k] for k in ("request", "source") if k in execution]
            if suffix != "native_output":
                parents += self.native_outputs.pop(native_key, [])
            data_key = self._data(
                event, suffix, {"native_output": "原生输出片段", "plan": "执行计划",
                                "diff": "文件差异", "native": native_title(payload["native"]),
                                "history": "历史补采：" + native_title(payload["native"])}[suffix],
                "process", value,
                data_type="archive" if suffix == "native_output" else "codex_" + suffix,
                native=deepcopy(payload["native"]), status=payload.get("status", "recorded"),
                parents=parents,
                **({key: association[key] for key in ("work_id", "stage_id")}
                   if association else {}),
                **({"origin": deepcopy(payload.get("origin"))} if historical else {}),
            )
            if suffix == "native_output":
                self.native_outputs.setdefault(native_key, []).append(data_key)
        elif kind == "model.completed" and task in self.executions:
            if payload.get("text"):
                self.executions[task]["text"] = payload["text"]
        elif kind == "workbench.report.published":
            report = deepcopy(payload)
            key = report["id"] + "_v" + str(report["version"])
            report.update(key=key, sequence=sequence, kind="published")
            report["audit_ids"] = self.decisions(task, report["stage_id"], sequence)
            self.reports[key] = report
            self.stages[report["stage_id"]]["last_report"] = key
            self._data(
                event,
                "report",
                report["title"],
                "output",
                {"report": key},
                parents=[row["id"] for row in report["evidence"]],
                data_type="report",
                report_key=key,
                work_id=report["work_id"],
                stage_id=report["stage_id"],
                audit_ids=report["audit_ids"],
            )
        elif kind == "workbench.audit.prepared":
            row = deepcopy(payload)
            row.update(status="prepared", sequence=sequence, timestamp=event.get("timestamp", ""))
            self.audits[row["id"]] = row
        elif kind.startswith("workbench.audit."):
            row = self.audits[payload["id"]]
            action = kind.rsplit(".", 1)[1]
            row["updated_at"] = event.get("timestamp", "")
            if action == "opened":
                row.update(status="pending", binding=deepcopy(payload))
            elif action == "bound":
                row["binding"] = deepcopy(payload)
            elif action == "reply_received":
                row.update(status="answer_received", reply=deepcopy(payload))
                row["reply"]["timestamp"] = event.get("timestamp", "")
                row["reply"]["sequence"] = sequence
            elif action == "resumed":
                row.update(status="answered", resumed_sequence=sequence)
                row["reply_data"] = self._data(
                    event, "decision", row["title"] + " · 用户决定", "process",
                    ({"audit_id": row["id"], "回答者": row["reply"]["actor"],
                      "答复": row["reply"]["response"], "交付": "已发送；服务结果另行核对"}
                     if "elicitation" in row else
                     {"audit_id": row["id"], "回答者": row["reply"]["actor"],
                     "答复": [{"问题": q["question"],
                               "选择": row["reply"]["answers"][q["id"]]["choice"],
                               "补充": row["reply"]["answers"][q["id"]]["text"]}
                              for q in row["questions"]]}),
                    data_type="decision", audit_ids=[row["id"]],
                    parents=[r["id"] for r in row["evidence"]],
                    work_id=row["work_id"], stage_id=row["stage_id"],
                )
            elif action == "invalidated":
                row.update(status="invalid", reason=payload["reason"])
            elif action == "dispatching":
                row.update(status="dispatching")
            elif action == "unconfirmed":
                row.update(status="unconfirmed", reason=payload["reason"])
        elif kind.startswith("task.") and task in self.executions:
            status = kind[5:]
            if status not in TERMINAL:
                return
            execution = self.executions[task]
            execution["status"] = status
            for audit in self.audits.values():
                if audit["task_id"] != task:
                    continue
                audit["execution_status"] = status
                if audit["status"] == "dispatching":
                    audit.update(status="unconfirmed", reason="答复交付未确认；不会自动重发。")
                if audit["status"] in {"prepared", "pending", "answer_received"}:
                    audit.update(
                        status="withdrawn", reason="执行已结束；未交付的答复不会自动重放。"
                    )
            if not execution["startup"] and execution["text"]:
                key = "execution_" + task
                self.reports[key] = {
                    "key": key,
                    "id": key,
                    "version": 1,
                    "kind": "execution",
                    "task_id": task,
                    "work_id": execution["work_id"],
                    "stage_id": execution["stage_id"],
                    "title": execution["title"],
                    "markdown": execution["text"],
                    "outcome": status,
                    "next_steps": "",
                    "timestamp": event.get("timestamp", ""),
                    "sequence": sequence,
                    "evidence": [
                        {"id": r["id"], "digest": r["digest"]}
                        for r in self.data.values()
                        if r["task_id"] == task and r["data_type"] != "report"
                    ],
                }

    def report_list(self):
        published_tasks = {r["task_id"] for r in self.reports.values() if r["kind"] == "published"}
        return [
            r
            for r in self.reports.values()
            if r["kind"] == "published" or r["task_id"] not in published_tasks
        ]

    def resolve(self, kind, object_id):
        rows = {"data": self.data, "report": self.reports, "audit": self.audits}.get(kind, {})
        if object_id not in rows:
            raise ValueError("Workbench object is unavailable")
        return rows[object_id]

    def evidence_value(self, key):
        row = self.resolve("data", key)
        value = deepcopy(row["value"])
        if content_digest({"value": value, "inputs": row.get("inputs", {})}) != row["digest"]:
            raise ValueError("Evidence snapshot has changed")
        artifact = value.get("artifact") if isinstance(value, dict) else None
        if isinstance(artifact, dict) and (value.get("truncated") or row["data_type"] == "archive"):
            value = strict_json(
                self.reader.artifact_text(
                    artifact["path"], artifact["sha256"], max_bytes=MAX_RECORD
                )
            )
        if row.get("native") and value.get("data", value).get("native") != row["native"]:
            raise ValueError("Native evidence identity has changed")
        if "origin" in row and value.get("origin") != row["origin"]:
            raise ValueError("Native evidence origin has changed")
        if "origin" in row and row["task_id"] != (row["origin"] or {}).get("task_id", ""):
            raise ValueError("Native evidence task has changed")
        return value

    def material(self, key):
        """Resolve only an archived MCP result through its original read/validation code."""
        value = self.evidence_value(key)
        data = value.get("data") if isinstance(value, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("artifact"), dict):
            return None
        from cadai.result_tools import ARTIFACT_SCHEMAS, ResultStore

        artifact = data["artifact"]
        refs = references(data)
        candidates = [
            ref for ref in refs if any(ref.startswith(prefix + "_") for prefix in ARTIFACT_SCHEMAS)
        ]
        if not candidates:
            return None
        state_root = self.reader.root.parents[1]
        supplied = Path(artifact.get("path", ""))
        selected = next((ref for ref in candidates if supplied.name == ref + ".json"), None)
        if selected is None or not supplied.is_absolute():
            raise ValueError("Unregistered result artifact path")
        current_directory = state_root / "ai" / "measurement-results"
        if supplied.parent == current_directory:
            target_state = state_root
        else:
            mapped = target_for(state_root, supplied)
            if mapped is None:
                raise ValueError("Unregistered result artifact path")
            if mapped.name != supplied.name or mapped.parent.name != "measurement-results":
                raise ValueError("Unregistered result artifact mapping")
            target_state = mapped.parents[2]

        class ReadOnlyStore(ResultStore):
            def directory(self, *, create=False):
                if create:
                    raise ValueError("Result archive is read-only")
                path = target_state
                for name in ("ai", "measurement-results"):
                    path = path / name
                    if path.is_symlink() or not path.is_dir():
                        raise ValueError("Result archive directory is unavailable")
                return path

        result = ReadOnlyStore(target_state).get(selected)
        if artifact.get("sha256") != selected.rsplit("_", 1)[1]:
            raise ValueError("Result artifact reference has changed")
        return result

    def verify_evidence(self, references_to_check):
        for ref in references_to_check:
            row = self.resolve("data", ref["id"])
            if row["digest"] != ref["digest"]:
                raise ValueError("Report evidence version has changed")
            value = self.evidence_value(row["id"])
            if row.get("native"):
                inputs = row.get("inputs", {})
                artifacts = ([inputs["artifact"]] if inputs.get("truncated") else [])
                artifacts += value.get("data", value).get("output_chunks", [])
                for artifact in artifacts:
                    strict_json(self.reader.artifact_text(artifact["path"], artifact["sha256"]))
            self.material(row["id"])
