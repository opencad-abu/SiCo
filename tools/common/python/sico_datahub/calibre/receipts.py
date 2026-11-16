"""Durable Calibre job receipt records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..codec import ArtifactRef
from ..errors import HubError
from ._validation import artifact, digest, text

JOB_SCHEMA = "calibre.job.v1"
_KINDS = frozenset({"drc", "lvs"})
_COMMON_STATES = frozenset({"queued", "running"})
_TERMINAL_STATES = {
    "drc": frozenset({"clean", "violations", "failed", "unknown", "cancelled"}),
    "lvs": frozenset({"correct", "incorrect", "failed", "unknown", "cancelled"}),
}
_RUN_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


def validate_run_ref(value):
    if not isinstance(value, str) or _RUN_REF.fullmatch(value) is None:
        raise HubError("invalid_arguments", "Invalid Calibre run_ref")
    return value


def _timestamp(value, field_name):
    return text(value, field_name, empty=True, limit=64)


def _optional_int(value, field_name):
    if value is not None and type(value) is not int:
        raise HubError("invalid_arguments", "Invalid " + field_name)
    return value


@dataclass(frozen=True)
class JobReceipt:
    run_ref: str
    kind: str
    state: str
    config: ArtifactRef
    work_root: str
    created_at: str
    started_at: str = ""
    finished_at: str = ""
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    message: str = ""
    runset: Optional[ArtifactRef] = None
    effective_deck: Optional[ArtifactRef] = None
    effective_config: Optional[ArtifactRef] = None
    candidate: Optional[ArtifactRef] = None
    candidate_source: Optional[ArtifactRef] = None
    scope: str = "full"
    dependencies_sha256: str = ""
    tool_version: str = ""
    top_cell: str = ""
    stdout: Optional[ArtifactRef] = None
    stderr: Optional[ArtifactRef] = None
    results: Optional[ArtifactRef] = None
    summary: Optional[ArtifactRef] = None
    report: Optional[ArtifactRef] = None
    svdb: Optional[ArtifactRef] = None
    lvs_log: Optional[ArtifactRef] = None
    result_snapshot: Optional[ArtifactRef] = None
    schema: str = JOB_SCHEMA

    def __post_init__(self):
        if self.schema != JOB_SCHEMA:
            raise HubError("invalid_arguments", "Invalid Calibre job schema")
        validate_run_ref(self.run_ref)
        if self.kind not in _KINDS:
            raise HubError("invalid_arguments", "Invalid Calibre job kind")
        if self.state not in _COMMON_STATES | _TERMINAL_STATES[self.kind]:
            raise HubError("invalid_arguments", "Invalid Calibre job state")
        artifact(self.config, "config")
        if not Path(self.work_root).is_absolute():
            raise HubError("invalid_arguments", "Calibre work root must be absolute")
        _timestamp(self.created_at, "created_at")
        _timestamp(self.started_at, "started_at")
        _timestamp(self.finished_at, "finished_at")
        if self.pid is not None and (type(self.pid) is not int or self.pid <= 0):
            raise HubError("invalid_arguments", "Invalid Calibre job pid")
        _optional_int(self.exit_code, "exit code")
        text(self.message, "message", empty=True)
        artifact(self.runset, "runset", optional=True)
        artifact(self.effective_deck, "effective_deck", optional=True)
        artifact(self.effective_config, "effective_config", optional=True)
        artifact(self.candidate, "candidate", optional=True)
        artifact(self.candidate_source, "candidate_source", optional=True)
        if self.scope not in {"full", "candidate"}:
            raise HubError("invalid_arguments", "Invalid Calibre job scope")
        if self.dependencies_sha256:
            digest(self.dependencies_sha256)
        text(self.tool_version, "tool_version", empty=True, limit=128)
        text(self.top_cell, "top_cell", empty=True, limit=512)
        for value, name in (
            (self.stdout, "stdout"),
            (self.stderr, "stderr"),
            (self.results, "results"),
            (self.summary, "summary"),
            (self.report, "report"),
            (self.svdb, "svdb"),
            (self.lvs_log, "lvs_log"),
            (self.result_snapshot, "result_snapshot"),
        ):
            artifact(value, name, optional=True)

    @property
    def terminal(self):
        return self.state not in _COMMON_STATES

    def to_dict(self):
        return {
            "schema": self.schema,
            "run_ref": self.run_ref,
            "kind": self.kind,
            "state": self.state,
            "config": self.config.to_dict(),
            "work_root": self.work_root,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "pid": self.pid,
            "exit_code": self.exit_code,
            "message": self.message,
            "runset": self.runset.to_dict() if self.runset else None,
            "effective_deck": (
                self.effective_deck.to_dict() if self.effective_deck else None
            ),
            "effective_config": (
                self.effective_config.to_dict() if self.effective_config else None
            ),
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "candidate_source": (
                self.candidate_source.to_dict() if self.candidate_source else None
            ),
            "scope": self.scope,
            "dependencies_sha256": self.dependencies_sha256,
            "tool_version": self.tool_version,
            "top_cell": self.top_cell,
            "stdout": self.stdout.to_dict() if self.stdout else None,
            "stderr": self.stderr.to_dict() if self.stderr else None,
            "results": self.results.to_dict() if self.results else None,
            "summary": self.summary.to_dict() if self.summary else None,
            "report": self.report.to_dict() if self.report else None,
            "svdb": self.svdb.to_dict() if self.svdb else None,
            "lvs_log": self.lvs_log.to_dict() if self.lvs_log else None,
            "result_snapshot": (
                self.result_snapshot.to_dict() if self.result_snapshot else None
            ),
        }

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise HubError("invalid_arguments", "Calibre job receipt must be an object")
        fields = set(cls.__dataclass_fields__)
        optional = {
            "candidate", "candidate_source", "effective_deck",
            "effective_config", "lvs_log", "scope",
        }
        missing = fields - set(value)
        if set(value) - fields or missing - optional:
            raise HubError("invalid_arguments", "Invalid Calibre job receipt fields")

        def ref(name):
            item = value[name]
            return ArtifactRef.from_dict(item) if item is not None else None

        return cls(
            run_ref=value["run_ref"],
            kind=value["kind"],
            state=value["state"],
            config=ArtifactRef.from_dict(value["config"]),
            work_root=value["work_root"],
            created_at=value["created_at"],
            started_at=value["started_at"],
            finished_at=value["finished_at"],
            pid=value["pid"],
            exit_code=value["exit_code"],
            message=value["message"],
            runset=ref("runset"),
            effective_deck=(
                ref("effective_deck") if "effective_deck" in value else None
            ),
            effective_config=(
                ref("effective_config") if "effective_config" in value else None
            ),
            candidate=ref("candidate") if "candidate" in value else None,
            candidate_source=(
                ref("candidate_source") if "candidate_source" in value else None
            ),
            scope=value.get("scope", "full"),
            dependencies_sha256=value["dependencies_sha256"],
            tool_version=value["tool_version"],
            top_cell=value["top_cell"],
            stdout=ref("stdout"),
            stderr=ref("stderr"),
            results=ref("results"),
            summary=ref("summary"),
            report=ref("report"),
            svdb=ref("svdb"),
            lvs_log=ref("lvs_log") if "lvs_log" in value else None,
            result_snapshot=ref("result_snapshot"),
            schema=value["schema"],
        )


__all__ = ["JOB_SCHEMA", "JobReceipt", "validate_run_ref"]
