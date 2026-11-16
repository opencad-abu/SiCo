"""Build and verify reviewed, create-only stream import plans.

The plan is data plus generated SKILL. It never contacts Virtuoso and never
writes OA. Execution remains a separate reviewed host action.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from ..skill_check import check_source

SCHEMA = "sico.layout.import_plan.v1"
STATUS_SCHEMA = "sico.layout.import_status.v1"
NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.$@+-]{0,127}\Z")
PLAN_ID = re.compile(r"import-[a-f0-9]{32}\Z")
UNSAFE_COMPLETION_CLEANUP = re.compile(rb'xstInOnCompletionCB\s*\(\s*"d"')
MAX_LAYER_MAP_BYTES = 8 * 1024 * 1024
MAX_PLAN_BYTES = 1024 * 1024
MAX_MARKER_BYTES = 4096


class ImportPlanError(RuntimeError):
    pass


def _reject_unsafe_completion(skill_bytes: bytes) -> None:
    if UNSAFE_COMPLETION_CLEANUP.search(skill_bytes):
        raise ImportPlanError(
            "import SKILL removes its completion callback from inside the callback; "
            "regenerate the plan with the current generator"
        )


def _safe_name(value: str, label: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ImportPlanError(label + " is not a safe cellview name")
    return value


def _path_text(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ImportPlanError(label + " must be an absolute path")
    if any(ord(char) < 32 for char in value):
        raise ImportPlanError(label + " contains a control character")
    return Path(value)


def _skill_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _inside(path: Path, root: Path) -> bool:
    return root == path or root in path.parents


def _layout_artifact(
    result: dict[str, Any], workspace: Path, expected_job_id: str | None
) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("state") != "succeeded":
        raise ImportPlanError("layout job is not succeeded")
    job_id = result.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ImportPlanError("layout result has no job_id")
    if expected_job_id is not None and job_id != expected_job_id:
        raise ImportPlanError("layout result job_id differs from the requested job")
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, list):
        raise ImportPlanError("layout result has no artifacts")
    layouts = [row for row in artifacts
               if isinstance(row, dict) and row.get("role") == "layout"]
    if len(layouts) != 1:
        raise ImportPlanError("layout result must contain exactly one layout artifact")
    layout = layouts[0]
    relative = layout.get("path")
    if not isinstance(relative, str) or not relative:
        raise ImportPlanError("layout artifact is missing path")
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any(
            part in {"", ".", ".."} for part in relative.split("/")):
        raise ImportPlanError("layout artifact path escapes workspace")
    source = (workspace / pure).resolve()
    if not _inside(source, workspace) or not source.is_file():
        raise ImportPlanError("layout artifact is missing or outside workspace")
    source_format = layout.get("format")
    if not isinstance(source_format, str) or not source_format:
        suffix = source.suffix.lower()
        if suffix == ".gz" and source.stem:
            suffix = source.stem.lower()
        source_format = {".gds": "gds", ".gds2": "gds2", ".gdsii": "gdsii"}.get(
            suffix, ""
        )
    if source_format.lower() not in {"gds", "gds2", "gdsii"}:
        raise ImportPlanError("v1 import plan supports GDS stream artifacts only")
    digest = _sha256_file(source)
    expected = layout.get("sha256")
    if not isinstance(expected, str) or digest != expected:
        raise ImportPlanError("layout artifact digest differs from the collected result")
    size = source.stat().st_size
    if "size" in layout and layout["size"] != size:
        raise ImportPlanError("layout artifact size differs from the collected result")
    return {
        "job_id": job_id,
        "role": "layout",
        "path": str(source),
        "workspace_path": relative,
        "format": source_format.lower(),
        "sha256": digest,
        "size": size,
    }


def _layer_map(value: str) -> dict[str, Any]:
    path = _path_text(value, "layer_map").resolve()
    if not path.is_file():
        raise ImportPlanError("layer_map is missing or not a regular file")
    size = path.stat().st_size
    if size <= 0:
        raise ImportPlanError("layer_map must not be empty")
    if size > MAX_LAYER_MAP_BYTES:
        raise ImportPlanError("layer_map exceeds the 8 MiB limit")
    data = path.read_bytes()
    if b"\x00" in data:
        raise ImportPlanError("layer_map contains a NUL byte")
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": size,
    }


def _skill_source(
    *,
    plan_id: str,
    source: dict[str, Any],
    target: dict[str, str],
    layer_map: dict[str, Any],
    marker: Path,
    summary: Path,
) -> tuple[str, str]:
    callback = "sicoLayoutImport_" + plan_id.removeprefix("import-") + "_done"

    def set_field(field: str, value: str) -> str:
        return (
            f'  unless(xstInSetField("{field}" {_skill_string(value)}) '
            f'error("SiCo import: field {field} was rejected"))'
        )

    lines = [
        "/* Generated by SiCo; reviewed write plan. Do not edit. */",
        f"procedure({callback}(@rest sicoCompletionArgs)",
        "  let((sicoStatusPort)",
        f"    sicoStatusPort=outfile({_skill_string(str(marker))})",
        "    when(sicoStatusPort",
        '      fprintf(sicoStatusPort "completed\\n")',
        "      close(sicoStatusPort)",
        "    )",
        "  )",
        ")",
        "let((sicoImportStatus)",
        f"  unless(ddGetObj({_skill_string(target['library'])}) "
        'error("SiCo import: target library does not exist"))',
        f"  when(ddGetObj({_skill_string(target['library'])} "
        f"{_skill_string(target['cell'])} {_skill_string(target['view'])}) "
        'error("SiCo import: target cellview already exists"))',
        set_field("strmFile", source["path"]),
        set_field("library", target["library"]),
        set_field("topCell", target["cell"]),
        set_field("view", target["view"]),
        set_field("layerMap", layer_map["path"]),
        set_field("writeMode", "noOverwrite"),
        set_field("showCompletionMsgBox", "false"),
        set_field("summaryFile", str(summary)),
        # Never unregister from inside the callback: Cadence iterates that
        # callback list while dispatching the XStream completion.
        f"  unless(xstInOnCompletionCB(\"r\" '{callback}) "
        'error("SiCo import: completion callback was rejected"))',
        "  xstInDoTranslate()",
        '  "submitted"',
        ")",
        "",
    ]
    skill = "\n".join(lines)
    report = check_source(skill.encode("utf-8"))
    if not report["ok"]:
        first = report["errors"][0]
        raise ImportPlanError(
            "generated import SKILL failed preflight: " + first["message"]
        )
    return skill, callback


def _approval_binding(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": plan["schema"],
        "plan_id": plan["plan_id"],
        "job_id": plan["source"]["job_id"],
        "source": plan["source"],
        "target": plan["target"],
        "layer_map": plan["layer_map"],
        "skill_sha256": plan["skill_sha256"],
        "policy": plan["policy"],
    }


def build_import_plan(
    result: dict[str, Any],
    *,
    workspace: Path,
    target_library: str,
    target_cell: str,
    target_view: str,
    layer_map: str,
    expected_job_id: str | None = None,
) -> dict[str, Any]:
    workspace = workspace.resolve()
    source = _layout_artifact(result, workspace, expected_job_id)
    target = {
        "library": _safe_name(target_library, "target_library"),
        "cell": _safe_name(target_cell, "target_cell"),
        "view": _safe_name(target_view, "target_view"),
    }
    mapped = _layer_map(layer_map)
    plan_id = "import-" + uuid.uuid4().hex
    root = (workspace / "import_plans" / plan_id).resolve()
    if not _inside(root, workspace):
        raise ImportPlanError("import plan root escapes workspace")
    root.mkdir(parents=True, exist_ok=False)
    marker = root / "import_done.marker"
    summary = root / "xstream_summary.txt"
    skill, callback = _skill_source(
        plan_id=plan_id,
        source=source,
        target=target,
        layer_map=mapped,
        marker=marker,
        summary=summary,
    )
    skill_path = root / "import.il"
    skill_path.write_text(skill, encoding="utf-8")
    policy = {
        "target": "create_only",
        "write_mode": "noOverwrite",
        "execution": "serialized_per_virtuoso_session",
        "requires_review": True,
    }
    plan = {
        "schema": SCHEMA,
        "plan_id": plan_id,
        "source": source,
        "target": target,
        "layer_map": mapped,
        "skill_file": str(skill_path),
        "skill_sha256": hashlib.sha256(skill.encode("utf-8")).hexdigest(),
        "skill_preflight": "passed",
        "completion": {
            "callback": callback,
            "marker_file": str(marker),
            "summary_file": str(summary),
            "state": "not_started",
        },
        "policy": policy,
        "execution": "not_started",
        "review_required": True,
    }
    plan["approval_plan_digest"] = _canonical_digest(_approval_binding(plan))
    plan_path = root / "import_plan.json"
    plan_path.write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {**plan, "plan_file": str(plan_path)}


def _checked_plan_file(plan_file: str, workspace: Path) -> tuple[Path, dict[str, Any]]:
    path = _path_text(plan_file, "plan_file").resolve()
    if not _inside(path, workspace) or not path.is_file():
        raise ImportPlanError("import plan is missing or outside workspace")
    if path.stat().st_size > MAX_PLAN_BYTES:
        raise ImportPlanError("import plan exceeds the 1 MiB limit")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImportPlanError("import plan is not readable JSON") from exc
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ImportPlanError("unsupported import plan schema")
    return path, value


def verify_import_plan(plan_file: str, *, workspace: Path) -> dict[str, Any]:
    """Recheck every reviewed input immediately before a host dispatches it."""
    workspace = workspace.resolve()
    path, plan = _checked_plan_file(plan_file, workspace)
    try:
        source = plan["source"]
        target = plan["target"]
        layer_map = plan["layer_map"]
        policy = plan["policy"]
        completion = plan["completion"]
        skill_file = Path(plan["skill_file"]).resolve()
        marker = Path(completion["marker_file"]).resolve()
        summary = Path(completion["summary_file"]).resolve()
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportPlanError("import plan is incomplete") from exc
    if not all(isinstance(item, dict) for item in (source, target, layer_map, policy, completion)):
        raise ImportPlanError("import plan is incomplete")
    if not isinstance(source.get("path"), str) or not isinstance(layer_map.get("path"), str):
        raise ImportPlanError("import plan source paths are invalid")
    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id):
        raise ImportPlanError("import plan id is invalid")
    if path.parent.name != plan_id:
        raise ImportPlanError("import plan is stored outside its plan directory")
    if plan.get("review_required") is not True or plan.get("execution") != "not_started":
        raise ImportPlanError("import plan review or execution state is invalid")
    try:
        expected_approval = _canonical_digest(_approval_binding(plan))
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportPlanError("import plan approval binding is invalid") from exc
    if not isinstance(plan.get("approval_plan_digest"), str) or (
            plan["approval_plan_digest"] != expected_approval):
        raise ImportPlanError("import plan approval digest is invalid")
    if policy != {
        "target": "create_only",
        "write_mode": "noOverwrite",
        "execution": "serialized_per_virtuoso_session",
        "requires_review": True,
    }:
        raise ImportPlanError("import plan policy is invalid")
    for key in ("library", "cell", "view"):
        _safe_name(target.get(key), "target_" + key)
    if not _inside(skill_file, path.parent) or not skill_file.is_file():
        raise ImportPlanError("import SKILL is missing or outside the plan directory")
    if not _inside(marker, path.parent) or not _inside(summary, path.parent):
        raise ImportPlanError("import completion path escapes the plan directory")
    if marker.exists() or marker.is_symlink() or summary.exists() or summary.is_symlink():
        raise ImportPlanError("import completion path already exists before dispatch")
    source_path = Path(source["path"]).resolve()
    if not _inside(source_path, workspace) or not source_path.is_file():
        raise ImportPlanError("layout artifact is missing or outside workspace")
    if _sha256_file(source_path) != source.get("sha256"):
        raise ImportPlanError("layout artifact changed after review")
    if source_path.stat().st_size != source.get("size"):
        raise ImportPlanError("layout artifact size changed after review")
    mapped = _layer_map(layer_map.get("path"))
    if mapped["sha256"] != layer_map.get("sha256") or mapped["size"] != layer_map.get("size"):
        raise ImportPlanError("layer map changed after review")
    skill_bytes = skill_file.read_bytes()
    if hashlib.sha256(skill_bytes).hexdigest() != plan.get("skill_sha256"):
        raise ImportPlanError("import SKILL changed after review")
    _reject_unsafe_completion(skill_bytes)
    report = check_source(skill_bytes, source_name=str(skill_file))
    if not report["ok"]:
        raise ImportPlanError("import SKILL no longer passes preflight")
    if completion.get("state") != "not_started":
        raise ImportPlanError("import completion state is invalid")
    return plan


def import_status(plan_file: str, *, workspace: Path) -> dict[str, Any]:
    """Read completion evidence without executing or rehashing a large layout."""
    workspace = workspace.resolve()
    path, plan = _checked_plan_file(plan_file, workspace)
    try:
        target = plan["target"]
        completion = plan["completion"]
        skill_file = Path(plan["skill_file"]).resolve()
        marker = Path(completion["marker_file"]).resolve()
        summary = Path(completion["summary_file"]).resolve()
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportPlanError("import plan is incomplete") from exc
    if not all(isinstance(item, dict) for item in (target, completion)):
        raise ImportPlanError("import plan is incomplete")
    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or not PLAN_ID.fullmatch(plan_id):
        raise ImportPlanError("import plan id is invalid")
    if path.parent.name != plan_id:
        raise ImportPlanError("import plan is stored outside its plan directory")
    try:
        expected_approval = _canonical_digest(_approval_binding(plan))
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportPlanError("import plan approval binding is invalid") from exc
    if plan.get("approval_plan_digest") != expected_approval:
        raise ImportPlanError("import plan approval digest is invalid")
    for key in ("library", "cell", "view"):
        _safe_name(target.get(key), "target_" + key)
    if not _inside(skill_file, path.parent) or not skill_file.is_file():
        raise ImportPlanError("import SKILL is missing or outside the plan directory")
    if hashlib.sha256(skill_file.read_bytes()).hexdigest() != plan.get("skill_sha256"):
        raise ImportPlanError("import SKILL changed after review")
    _reject_unsafe_completion(skill_file.read_bytes())
    if not _inside(marker, path.parent) or not _inside(summary, path.parent):
        raise ImportPlanError("import completion path escapes the plan directory")
    marker_present = marker.is_file()
    marker_size = marker.stat().st_size if marker_present else 0
    marker_text = None
    if marker_present and marker_size <= MAX_MARKER_BYTES:
        marker_text = marker.read_text(encoding="utf-8", errors="replace")
    return {
        "schema": STATUS_SCHEMA,
        "plan_id": plan_id,
        "plan_file": str(path),
        "approval_plan_digest": plan["approval_plan_digest"],
        "target": target,
        "source": plan.get("source"),
        "layer_map": plan.get("layer_map"),
        "completion": {
            "marker_file": str(marker),
            "marker_present": marker_present,
            "marker_size": marker_size,
            "marker_text": marker_text,
            "summary_file": str(summary),
            "summary_present": summary.is_file(),
        },
        "state": "completion_marker_present" if marker_present else "planned",
    }


__all__ = [
    "ImportPlanError", "build_import_plan", "import_status", "verify_import_plan",
]
