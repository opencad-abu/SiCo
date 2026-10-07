"""File-only schematic previews. SVG source never enters the agent context."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path
from xml.etree import ElementTree

from .inspection import decode_read_only_result
from sicostate import project_directory

MAX_SVG_BYTES = 2 * 1024 * 1024
SCHEMA = "cad.schematic.svg-preview.v1"


def _directory(workspace, *, create=False):
    if workspace is None:
        raise ValueError("SVG preview requires a workspace")
    directory = project_directory(workspace, "ai/circuit_previews", create=create)
    if directory.resolve() != directory:
        raise ValueError("SVG preview directory must stay inside the workspace")
    return directory


def _checked_path(path, workspace):
    path = Path(path)
    if not path.is_absolute() or path.suffix != ".svg":
        raise ValueError("Invalid SVG preview path")
    if workspace is None or not Path(workspace).is_dir():
        raise ValueError("SVG preview is outside an available workspace")
    if path.parent.resolve() != _directory(workspace).resolve():
        raise ValueError("SVG preview is outside the workspace preview directory")
    return path


def _read_svg(path, expected_size):
    if type(expected_size) is not int or not 0 < expected_size <= MAX_SVG_BYTES:
        raise ValueError("SVG size is outside the preview budget")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
            raise ValueError("SVG artifact size changed")
        raw = stream.read(MAX_SVG_BYTES + 1)
    if len(raw) != expected_size:
        raise ValueError("SVG artifact size changed during read")
    try:
        svg_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("SVG preview must be UTF-8") from exc
    if "<!ENTITY" in svg_text.upper():
        raise ValueError("SVG entities are unsupported")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise ValueError("SVG preview XML is invalid") from exc
    if root.tag != "{http://www.w3.org/2000/svg}svg":
        raise ValueError("Native preview is not SVG")
    # Render only self-contained exported geometry/text, without external resources.
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] in {"script", "foreignObject", "image"}:
            raise ValueError("SVG contains unsupported active or external content")
        for key, value in element.attrib.items():
            if key.rsplit("}", 1)[-1] == "href" and not value.startswith("#"):
                raise ValueError("SVG references an external resource")
        if "url(" in (element.text or "").lower() or any(
                "url(" in v.lower() for v in element.attrib.values()):
            raise ValueError("SVG resource URLs are unsupported")
    return raw


def read_svg_artifact(artifact, workspace):
    """SiCo's display worker reads the file and verifies the report's checksum."""
    path = _checked_path(artifact["path"], workspace)
    raw = _read_svg(path, artifact["size_bytes"])
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
            directory = _directory(workspace, create=True)
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
        path = _checked_path(metadata["path"], workspace)
        raw = (read_svg_artifact(metadata, workspace) if "sha256" in metadata
               else _read_svg(path, metadata["size_bytes"]))
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
