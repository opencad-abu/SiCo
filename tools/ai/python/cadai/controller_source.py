"""Materialize validated request source in the declared bridge spool."""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .inspection import build_config_binding_skill, build_snapshot_request, build_snapshot_skill
from .runtime import MAX_EVAL_BYTES, read_spool
from .skill_check import require_preflight
from .skill_check_tools import read_skill_file
from .socket_server import RequestFailure
from .template_schema import arguments as template_arguments
from .template_schema import capture_skill


def eval_source(params: dict[str, Any], spool: Path) -> bytes:
    if set(params) == {"code"} and isinstance(params["code"], str):
        data = params["code"].encode("utf-8")
    elif set(params) == {"source_spool"} and isinstance(params["source_spool"], Mapping):
        reference = dict(params["source_spool"])
        name = reference.get("name")
        if not isinstance(name, str):
            raise RequestFailure("invalid_params", "source_spool.name must be a string")
        try:
            data = read_spool(spool, name)
        except (OSError, ValueError, RuntimeError) as exc:
            raise RequestFailure("invalid_spool", str(exc)) from exc
        finally:
            if Path(name).name == name:
                (spool / name).unlink(missing_ok=True)
        expected_size = reference.get("size")
        expected_hash = reference.get("sha256")
        if expected_size != len(data) or expected_hash != hashlib.sha256(data).hexdigest():
            raise RequestFailure("invalid_spool", "source spool metadata does not match")
    else:
        raise RequestFailure("invalid_params", "eval_skill requires exactly one code string")
    if not data.strip():
        raise RequestFailure("invalid_params", "SKILL expression must not be empty")
    if len(data) > MAX_EVAL_BYTES:
        raise RequestFailure("payload_too_large", "SKILL expression exceeds the size limit")
    require_preflight(data)
    return data


def materialize_skill_file(params: dict[str, Any], workspace: Path, spool: Path, *, write_spool) -> Path:
    if set(params) != {"path"} or not isinstance(params.get("path"), str):
        raise RequestFailure("invalid_params", "load_skill_file requires exactly one path")
    path, source = read_skill_file(params["path"], workspace)
    require_preflight(source, source_name=str(path))
    metadata = write_spool(
        spool,
        "load-source",
        source,
        suffix=path.suffix.lower(),
    )
    return Path(str(metadata["path"]))


def snapshot_source(params: dict[str, Any], spool: Path, *, write_spool) -> tuple[Path, Path]:
    try:
        build_snapshot_request(params)
    except ValueError as exc:
        raise RequestFailure("invalid_params", str(exc)) from exc
    snapshot_path = spool / (
        f"snapshot-data-{secrets.token_hex(12)}.jsonl"
    )
    code = build_snapshot_skill(params, str(snapshot_path))
    data = code.encode("utf-8")
    if len(data) > MAX_EVAL_BYTES:
        raise RequestFailure("payload_too_large", "snapshot SKILL exceeds the size limit")
    metadata = write_spool(spool, "snapshot-source", data)
    return Path(str(metadata["path"])), snapshot_path


def template_source(params: dict[str, Any], spool: Path, *, write_spool) -> tuple[Path, Path]:
    try:
        checked = template_arguments("extract_circuit_templates", params)
    except ValueError as exc:
        raise RequestFailure("invalid_params", str(exc)) from exc
    artifact_path = spool / f"template-data-{secrets.token_hex(12)}.jsonl"
    code = capture_skill(checked, str(artifact_path))
    data = code.encode("utf-8")
    if len(data) > MAX_EVAL_BYTES:
        raise RequestFailure(
            "payload_too_large", "template capture SKILL exceeds the size limit"
        )
    metadata = write_spool(spool, "template-source", data, suffix=".il")
    return Path(str(metadata["path"])), artifact_path


def config_binding_source(params: dict[str, Any], spool: Path, *, write_spool) -> Path:
    try:
        code = build_config_binding_skill(params)
    except ValueError as exc:
        raise RequestFailure("invalid_params", str(exc)) from exc
    metadata = write_spool(
        spool,
        "config-binding-source",
        code.encode("utf-8"),
        suffix=".il",
    )
    return Path(str(metadata["path"]))
