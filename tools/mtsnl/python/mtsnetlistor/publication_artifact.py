"""Verify the netlist belongs to the succeeded generation run."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
from .errors import RequestValidationError
from .model_request import NetlistRequest
from .model_design import TargetSelection
from .request_data import canonical_request_digest
from .artifacts import sha256_file


def validate_run_artifact(
    request: NetlistRequest,
    netlist: str | Path,
    run_dir: str | Path,
    *,
    strict_ownership: bool = False,
) -> None:
    """Require a netlist to belong to the supplied generation run.

    A caller may publish an already-generated stable file, but it must be the
    stable output for the same source design and dialect, or a scoped artifact
    below the supplied run directory.  Arbitrary paths are rejected before
    any target overlay or importer is created.
    """

    source = Path(netlist).expanduser().resolve()
    run = Path(run_dir).expanduser().resolve()
    if not source.is_file() or source.stat().st_size == 0:
        raise RequestValidationError(f"MTS netlist is missing or empty: {source}")

    # Test/dry-run callers may use an unmanaged staging directory.  The CLI
    # and GUI always set strict_ownership, so an arbitrary caller cannot use
    # the compatibility path in the product workflow.
    marker = run / ".mts-netlistor-run.json"
    manifest_path = run / "manifest.json"
    managed = marker.is_file() or manifest_path.is_file()
    if not managed:
        if strict_ownership:
            raise RequestValidationError(
                f"MTS run ownership marker is missing: {marker}"
            )
        return

    if not marker.is_file() or not manifest_path.is_file():
        raise RequestValidationError(
            "managed MTS run is missing its ownership marker or manifest"
        )
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RequestValidationError(
            f"invalid MTS run ownership metadata: {run}"
        ) from exc
    if not isinstance(metadata, dict) or not isinstance(manifest, dict):
        raise RequestValidationError(f"invalid MTS run ownership metadata: {run}")

    # Target library/cell/view publication is deliberately selectable after
    # generation.  It cannot affect the generated MTS text, so validate the
    # run against the generation request (target disabled) while target
    # authority is checked separately by preflight_publication().
    generation_request = replace(request, target=TargetSelection()).validate()
    expected_digests = {
        canonical_request_digest(request),
        canonical_request_digest(generation_request),
    }
    if metadata.get("schema_version") != 1:
        raise RequestValidationError("unsupported MTS run ownership marker version")
    if metadata.get("request_digest") not in expected_digests:
        raise RequestValidationError("MTS run marker request digest does not match the request")
    if (
        metadata.get("source_library") != generation_request.source.library
        or metadata.get("source_cell") != generation_request.source.cell
        or metadata.get("source_view") != generation_request.source.view
    ):
        raise RequestValidationError("MTS run marker does not match the requested source design")
    if manifest.get("schema_version") != 1:
        raise RequestValidationError("unsupported MTS run manifest version")
    if manifest.get("status") != "succeeded":
        raise RequestValidationError(
            f"MTS run is not publishable; manifest status is {manifest.get('status')!r}"
        )
    if manifest.get("request_digest") not in expected_digests:
        raise RequestValidationError("MTS run manifest request digest does not match the request")
    if manifest.get("request_digest") != metadata.get("request_digest"):
        raise RequestValidationError("MTS run marker and manifest request digests do not match")
    if manifest.get("dialect") != generation_request.dialect:
        raise RequestValidationError("MTS run manifest simulator dialect does not match the request")

    def _manifest_path(key: str) -> Path:
        raw = manifest.get(key)
        if not isinstance(raw, str) or not raw.strip():
            raise RequestValidationError(f"MTS run manifest is missing {key}")
        return Path(raw).expanduser().resolve()

    def _manifest_digest(key: str, path: Path) -> None:
        digest = manifest.get(key)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            raise RequestValidationError(f"MTS run manifest is missing a valid {key}")
        if not path.is_file() or path.stat().st_size == 0:
            raise RequestValidationError(f"MTS run artifact is missing: {path}")
        if sha256_file(path) != digest:
            raise RequestValidationError(f"MTS run artifact digest mismatch: {path}")

    scoped = _manifest_path("scoped_netlist")
    stable = _manifest_path("stable_output")
    _manifest_digest("scoped_sha256", scoped)
    _manifest_digest("stable_sha256", stable)

    selected = source
    if selected == scoped:
        expected_scoped = run / "scoped" / f"{request.source.cell}{request.output_suffix}"
        if selected != expected_scoped.resolve():
            raise RequestValidationError("MTS scoped artifact is outside the run scoped directory")
    elif selected == stable:
        expected_stable = run.parent.parent.parent / f"{request.source.cell}{request.output_suffix}"
        if selected != expected_stable.resolve():
            raise RequestValidationError("MTS stable artifact is outside the run namespace")
    else:
        raise RequestValidationError(
            f"MTS netlist is not the succeeded scoped/stable artifact for the supplied run: {source}"
        )
