"""Bounded candidate/revision ledger for the verification MCP profile.

The ledger is a controller-owned staging boundary.  It records candidate
bytes and deterministic gate summaries, but it is not a publisher and it
never accepts a verdict or evidence claim from the agent.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

MAX_CANDIDATE_SOURCE_BYTES = 256 * 1024
MAX_CANDIDATE_ASSUMPTIONS = 32
MAX_ASSUMPTION_CHARS = 512
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.$+-]{0,127}$")
_VERDICT_KEYS = frozenset(
    {"verdict", "provider_verdict", "provider_status", "provider_decision", "decision", "pass_fail"}
)
_LANGUAGES = frozenset({"systemverilog", "veriloga"})


class CandidateLedgerError(ValueError):
    """Raised for a fail-closed candidate or feedback request."""


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_verdict(value: Any, field: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if isinstance(key, str) and key.casefold() in _VERDICT_KEYS:
                raise CandidateLedgerError(f"{field} contains forbidden verdict field: {key}")
            _reject_verdict(child, field)
    elif isinstance(value, list):
        for child in value:
            _reject_verdict(child, field)


def _bounded_map(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateLedgerError(f"{field} must be an object")
    _reject_verdict(value, field)
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        normalized = json.loads(rendered)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CandidateLedgerError(f"{field} must be bounded JSON: {exc}") from exc
    if not isinstance(normalized, dict) or len(rendered.encode("utf-8")) > 32 * 1024:
        raise CandidateLedgerError(f"{field} exceeds the bounded JSON limit")
    return normalized


def _required_digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise CandidateLedgerError(f"{field} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class CandidateRecord:
    candidate_sha256: str
    revision: int
    parent_sha256: str | None
    source_generation: str
    template_lock: str | None
    module: str
    language: str
    source_bytes: int
    source_sha256: str
    context_sha256: str | None
    assumptions: tuple[str, ...]
    artifact_name: str
    feedback: Mapping[str, Any] | None = None

    def to_dict(self, *, include_source: bool = False, source: str | None = None) -> dict[str, Any]:
        value: dict[str, Any] = {
            "candidate_sha256": self.candidate_sha256,
            "revision": self.revision,
            "parent_sha256": self.parent_sha256,
            "source_generation": self.source_generation,
            "template_lock": self.template_lock,
            "module": self.module,
            "language": self.language,
            "source_bytes": self.source_bytes,
            "source_sha256": self.source_sha256,
            "context_sha256": self.context_sha256,
            "assumptions": list(self.assumptions),
            "artifact": {"name": self.artifact_name, "private": True},
            "feedback": None if self.feedback is None else dict(self.feedback),
        }
        if include_source and source is not None:
            value["source"] = source
        return value


class CandidateLedger:
    """Append-only bounded candidate store scoped to one MCP runtime."""

    def __init__(self, spool: Path):
        self.spool = spool.resolve()
        if not self.spool.is_dir() or self.spool.is_symlink():
            raise CandidateLedgerError("candidate spool is not a private directory")
        self._records: dict[str, CandidateRecord] = {}
        self._sources: dict[str, str] = {}
        self._load_existing()

    def _load_existing(self) -> None:
        for artifact in sorted(self.spool.glob("candidate-*.json")):
            if artifact.is_symlink() or not artifact.is_file():
                raise CandidateLedgerError("candidate spool contains an unsafe artifact")
            try:
                payload = json.loads(artifact.read_text(encoding="utf-8"))
                source = payload.get("source")
                if not isinstance(source, str):
                    raise ValueError("candidate source is missing")
                record = self._record_from_payload(payload, source)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                raise CandidateLedgerError(
                    f"candidate artifact is invalid: {artifact.name}: {exc}"
                ) from exc
            if record.artifact_name != artifact.name:
                raise CandidateLedgerError("candidate artifact name does not match metadata")
            if record.candidate_sha256 in self._records:
                raise CandidateLedgerError("candidate ledger contains a duplicate candidate")
            self._records[record.candidate_sha256] = record
            self._sources[record.candidate_sha256] = source

    @staticmethod
    def _record_from_payload(payload: Mapping[str, Any], source: str) -> CandidateRecord:
        candidate_sha = _required_digest(payload.get("candidate_sha256"), "candidate_sha256")
        module = payload.get("module")
        language = payload.get("language")
        generation = _required_digest(payload.get("source_generation"), "source_generation")
        template = payload.get("template_lock")
        if template is not None:
            template = _required_digest(template, "template_lock")
        parent = payload.get("parent_sha256")
        if parent is not None:
            parent = _required_digest(parent, "parent_sha256")
        context = payload.get("context_sha256")
        if context is not None:
            context = _required_digest(context, "context_sha256")
        if not isinstance(module, str) or _IDENTIFIER.fullmatch(module) is None:
            raise ValueError("candidate module is invalid")
        if language not in _LANGUAGES:
            raise ValueError("candidate language is unsupported")
        revision = payload.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("candidate revision is invalid")
        assumptions = payload.get("assumptions", [])
        if not isinstance(assumptions, list) or len(assumptions) > MAX_CANDIDATE_ASSUMPTIONS:
            raise ValueError("candidate assumptions are invalid")
        if any(not isinstance(item, str) or not item or len(item) > MAX_ASSUMPTION_CHARS for item in assumptions):
            raise ValueError("candidate assumption is invalid")
        source_bytes = source.encode("utf-8")
        if len(source_bytes) > MAX_CANDIDATE_SOURCE_BYTES:
            raise ValueError("candidate source exceeds 256 KiB")
        if payload.get("source_sha256") != _digest_bytes(source_bytes):
            raise ValueError("candidate source_sha256 does not match source")
        if payload.get("source_bytes") != len(source_bytes):
            raise ValueError("candidate source_bytes does not match source")
        canonical = {
            "source": source,
            "module": module,
            "language": language,
            "source_generation": generation,
            "template_lock": template,
            "parent_sha256": parent,
            "context_sha256": context,
            "assumptions": assumptions,
            "revision": revision,
        }
        if _digest(canonical) != candidate_sha:
            raise ValueError("candidate digest does not match canonical content")
        feedback = payload.get("feedback")
        if feedback is not None and not isinstance(feedback, Mapping):
            raise ValueError("candidate feedback is invalid")
        if isinstance(feedback, Mapping):
            feedback_status = feedback.get("status")
            feedback_code = feedback.get("code")
            feedback_evidence = feedback.get("evidence")
            feedback_sha = feedback.get("sha256")
            if feedback_status not in {"PASS", "FAIL", "BLOCKED"} or not isinstance(feedback_code, str):
                raise ValueError("candidate feedback fields are invalid")
            if not isinstance(feedback_evidence, Mapping) or feedback_evidence.get("source_generation") != generation:
                raise ValueError("candidate feedback source_generation is invalid")
            if feedback_sha != _digest(feedback_evidence):
                raise ValueError("candidate feedback digest does not match evidence")
        return CandidateRecord(
            candidate_sha256=candidate_sha,
            revision=revision,
            parent_sha256=parent,
            source_generation=generation,
            template_lock=template,
            module=module,
            language=language,
            source_bytes=len(source_bytes),
            source_sha256=_digest_bytes(source_bytes),
            context_sha256=context,
            assumptions=tuple(assumptions),
            artifact_name=str(payload.get("artifact", {}).get("name", ""))
            if isinstance(payload.get("artifact"), Mapping)
            else "",
            feedback=dict(feedback) if isinstance(feedback, Mapping) else None,
        )

    def submit(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "source", "module", "language", "source_generation", "template_lock",
            "parent_sha256", "context_sha256", "assumptions",
        }
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise CandidateLedgerError(f"unknown candidate field(s): {unknown}")
        source = arguments.get("source")
        if not isinstance(source, str) or not source:
            raise CandidateLedgerError("candidate source is required")
        source_bytes = source.encode("utf-8")
        if len(source_bytes) > MAX_CANDIDATE_SOURCE_BYTES:
            raise CandidateLedgerError("candidate source exceeds 256 KiB")
        module = arguments.get("module")
        if not isinstance(module, str) or _IDENTIFIER.fullmatch(module) is None:
            raise CandidateLedgerError("candidate module is invalid")
        language = arguments.get("language")
        if language not in _LANGUAGES:
            raise CandidateLedgerError("candidate language is unsupported")
        source_generation = _required_digest(arguments.get("source_generation"), "source_generation")
        template_lock = arguments.get("template_lock")
        if template_lock is not None:
            template_lock = _required_digest(template_lock, "template_lock")
        parent = arguments.get("parent_sha256")
        if parent is not None:
            parent = _required_digest(parent, "parent_sha256")
            if parent not in self._records:
                raise CandidateLedgerError("parent candidate is not present in this session")
            parent_record = self._records[parent]
            if parent_record.source_generation != source_generation:
                raise CandidateLedgerError("parent candidate source_generation does not match")
            if parent_record.template_lock != template_lock:
                raise CandidateLedgerError("parent candidate template_lock does not match")
            revision = parent_record.revision + 1
        else:
            revision = 0
        context_sha = arguments.get("context_sha256")
        if context_sha is not None:
            context_sha = _required_digest(context_sha, "context_sha256")
        assumptions = arguments.get("assumptions", [])
        if not isinstance(assumptions, list) or len(assumptions) > MAX_CANDIDATE_ASSUMPTIONS:
            raise CandidateLedgerError("assumptions must contain at most 32 items")
        normalized_assumptions: list[str] = []
        for item in assumptions:
            if not isinstance(item, str) or not item or len(item) > MAX_ASSUMPTION_CHARS:
                raise CandidateLedgerError("candidate assumption is invalid")
            normalized_assumptions.append(item)
        canonical = {
            "source": source,
            "module": module,
            "language": language,
            "source_generation": source_generation,
            "template_lock": template_lock,
            "parent_sha256": parent,
            "context_sha256": context_sha,
            "assumptions": normalized_assumptions,
            "revision": revision,
        }
        candidate_sha = _digest(canonical)
        if candidate_sha in self._records:
            raise CandidateLedgerError("candidate already exists")
        source_sha = _digest_bytes(source_bytes)
        artifact_name = f"candidate-{candidate_sha}.json"
        artifact = self.spool / artifact_name
        if artifact.exists() or artifact.is_symlink():
            raise CandidateLedgerError("candidate artifact already exists")
        record = CandidateRecord(
            candidate_sha256=candidate_sha,
            revision=revision,
            parent_sha256=parent,
            source_generation=source_generation,
            template_lock=template_lock,
            module=module,
            language=language,
            source_bytes=len(source_bytes),
            source_sha256=source_sha,
            context_sha256=context_sha,
            assumptions=tuple(normalized_assumptions),
            artifact_name=artifact_name,
        )
        payload = record.to_dict(include_source=True, source=source)
        payload["candidate_sha256"] = candidate_sha
        temporary = self.spool / ("." + artifact_name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        temporary.replace(artifact)
        self._records[candidate_sha] = record
        self._sources[candidate_sha] = source
        return record.to_dict()

    def get(self, candidate_sha256: str, *, include_source: bool = False) -> dict[str, Any]:
        candidate_sha256 = _required_digest(candidate_sha256, "candidate_sha256")
        record = self._records.get(candidate_sha256)
        if record is None:
            raise CandidateLedgerError("candidate is not present in this session")
        return record.to_dict(include_source=include_source, source=self._sources[candidate_sha256])

    def attach_feedback(self, candidate_sha256: str, recipe_result: Mapping[str, Any]) -> dict[str, Any]:
        candidate_sha256 = _required_digest(candidate_sha256, "candidate_sha256")
        record = self._records.get(candidate_sha256)
        if record is None:
            raise CandidateLedgerError("candidate is not present in this session")
        feedback = _feedback_from_recipe(recipe_result, record.source_generation)
        updated = CandidateRecord(**{**record.__dict__, "feedback": feedback})
        self._records[candidate_sha256] = updated
        artifact = self.spool / record.artifact_name
        payload = updated.to_dict(include_source=True, source=self._sources[candidate_sha256])
        temporary = self.spool / ("." + record.artifact_name + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(artifact)
        return updated.to_dict()


def _feedback_from_recipe(value: Mapping[str, Any], expected_source_generation: str) -> dict[str, Any]:
    aivw = value.get("aivw") if isinstance(value.get("aivw"), Mapping) else value
    status = aivw.get("status") if isinstance(aivw, Mapping) else None
    if not isinstance(status, str):
        status = "BLOCKED"
    normalized_status = "PASS" if status == "PASS" else "FAIL" if status.startswith("FAIL") else "BLOCKED"
    details = aivw.get("details") if isinstance(aivw, Mapping) else None
    gates: dict[str, str] = {}
    source_generation: str | None = None
    if isinstance(details, Mapping) and isinstance(details.get("gate_results"), Mapping):
        for gate_id, raw in details["gate_results"].items():
            if isinstance(gate_id, str) and isinstance(raw, Mapping) and isinstance(raw.get("status"), str):
                gates[gate_id] = raw["status"]
                outputs = raw.get("outputs")
                if (
                    isinstance(outputs, Mapping)
                    and isinstance(outputs.get("source_generation"), str)
                    and _DIGEST.fullmatch(outputs["source_generation"])
                ):
                    if source_generation is not None and source_generation != outputs["source_generation"]:
                        raise CandidateLedgerError("recipe gate source_generation values disagree")
                    source_generation = outputs["source_generation"]
    if source_generation != expected_source_generation:
        raise CandidateLedgerError("recipe feedback source_generation does not match candidate")
    evidence = {
        "run_id": aivw.get("run_id") if isinstance(aivw, Mapping) and isinstance(aivw.get("run_id"), str) else None,
        "status": normalized_status,
        "gate_statuses": gates,
        "source_generation": source_generation,
    }
    return {"status": normalized_status, "code": "deterministic_gate_feedback", "evidence": evidence, "sha256": _digest(evidence)}


__all__ = ["CandidateLedger", "CandidateLedgerError", "CandidateRecord", "MAX_CANDIDATE_SOURCE_BYTES"]
