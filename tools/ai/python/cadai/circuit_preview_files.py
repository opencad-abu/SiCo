"""Contained preview paths and self-contained SVG file reads."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from xml.etree import ElementTree

from sicostate import absolute, project_directory

MAX_SVG_BYTES = 2 * 1024 * 1024


def checked_size(expected_size):
    if type(expected_size) is not int or not 0 < expected_size <= MAX_SVG_BYTES:
        raise ValueError("SVG size is outside the preview budget")
    return expected_size


def preview_directory(workspace, *, create=False):
    if workspace is None:
        raise ValueError("SVG preview requires a workspace")
    # The native export compares the destination against the project directory
    # as simplifyFilename spells it (symlinks resolved); reaching the workspace
    # through a site alias must not change the spelling of the preview path.
    root = Path(absolute(workspace)).resolve()
    directory = project_directory(root, "ai/circuit_previews", create=create)
    # A launch directory may itself be reached through a symlink (a site's
    # mount alias); only a redirection below the root would move the preview
    # out of the state directory it was validated in.
    if directory.resolve() != root / directory.relative_to(root):
        raise ValueError("SVG preview directory must stay inside the workspace")
    return directory


def checked_path(path, workspace):
    path = Path(path)
    if not path.is_absolute() or path.suffix != ".svg":
        raise ValueError("Invalid SVG preview path")
    if workspace is None or not Path(workspace).is_dir():
        raise ValueError("SVG preview is outside an available workspace")
    if path.parent.resolve() != preview_directory(workspace).resolve():
        raise ValueError("SVG preview is outside the workspace preview directory")
    return path


def read_contained_file(path, maximum):
    """Read one regular file under the preview budget without following links."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("SVG preview must be a regular file")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("SVG preview is outside the preview budget")
    return raw


def read_svg_file(path, expected_size):
    raw = read_contained_file(path, checked_size(expected_size))
    if len(raw) != expected_size:
        raise ValueError("SVG artifact size changed during read")
    return parse_svg(raw)


def parse_svg(raw):
    """Accept only self-contained exported geometry, without external resources."""
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
