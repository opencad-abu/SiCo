"""File-only schematic previews. SVG source never enters the agent context."""

from __future__ import annotations

import hashlib
import json
import uuid
from xml.etree import ElementTree

# MAX_SVG_BYTES stays importable from this module: the export budget and the
# catalog budget are one limit, declared by the file owner.
from .circuit_preview_files import MAX_SVG_BYTES as MAX_SVG_BYTES
from .circuit_preview_files import checked_path, preview_directory, read_svg_file
from .inspection import decode_read_only_result

SCHEMA = "cad.schematic.svg-preview.v1"


def read_svg_artifact(artifact, workspace):
    """SiCo's display worker reads the file and verifies the report's checksum."""
    path = checked_path(artifact["path"], workspace)
    raw = read_svg_file(path, artifact["size_bytes"])
    if hashlib.sha256(raw).hexdigest() != artifact["sha256"]:
        raise ValueError("SVG preview checksum changed")
    return raw


def attach_svg_preview(result, client, workspace=None):
    """Add path/size/checksum only; native export is retained once per creation."""
    if not result.get("saved") or not result.get("readback_verified"):
        return result
    reference = result.get("circuit_ref")
    if not isinstance(reference, str):
        return result
    preview = dict(schema=SCHEMA, status="export_failed", media_type="image/svg+xml",
                   source="Virtuoso.schExportSVG", target=result.get("target"),
                   artifact=None, visual_review_status="not_assessed",
                   cadence_expressions="preserved_as_exported", acceptance_blocking=False,
                   message=None)
    try:
        native = result.get("svg_preview")
        if native is None:
            directory = preview_directory(workspace, create=True)
            candidate = directory / (uuid.uuid4().hex + ".svg")
            code = "aiCrExportSvg(" + json.dumps(reference) + " " + json.dumps(str(candidate)) + ")"
            try:
                ok, response = client.call("eval_skill_native", {"code": code})
            except Exception as exc:
                raise ValueError("SVG export transport unavailable: " + str(exc)[:512]) from exc
            if not ok:
                raise ValueError("SVG export transport failed")
            ok, native = decode_read_only_result(response)
            if not ok:
                raise ValueError(native.get("message") or "SVG export failed")
        if not isinstance(native, dict):
            raise ValueError("Invalid SVG preview metadata")
        if native.get("status") != "ready":
            raise ValueError(native.get("message") or "SVG export failed")
        metadata = native.get("artifact") or native
        path = checked_path(metadata["path"], workspace)
        raw = (read_svg_artifact(metadata, workspace) if "sha256" in metadata
               else read_svg_file(path, metadata["size_bytes"]))
        artifact = dict(path=str(path), media_type="image/svg+xml", size_bytes=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest())
        # Keep the original digest beside the SVG for repeat reports and offline UI reads.
        manifest = path.with_suffix(".json")
        if manifest.exists():
            if json.loads(manifest.read_text(encoding="utf-8")) != artifact:
                raise ValueError("SVG preview differs from its saved checksum")
        else:
            with manifest.open("x", encoding="utf-8") as stream:
                json.dump(artifact, stream, ensure_ascii=False)
        preview.update(status="ready", artifact=artifact)
    except (OSError, ValueError, KeyError, TypeError, ElementTree.ParseError) as exc:
        preview["message"] = str(exc)
    result["svg_preview"] = preview
    return result
