"""Linkify CAD reference tokens and user-selected @ object inputs."""

from __future__ import annotations

import json
import re

from .cad_ref import (
    _FILE_BLOCK,
    _OBJECT_BLOCK,
    _SESSION,
    canonical,
    normalize_ref,
    normalize_target,
    object_link,
    object_ref,
    parse_object_link,
)

_CAD_TOKEN = re.compile(
    r"(?<![\w/])(inst|net|term|cv):/"
    r"([A-Za-z0-9_.@\[\]<>:-]+(?:/[A-Za-z0-9_.@\[\]<>:-]+)*)"
)
_TOKEN_KIND = {"inst": "instance", "net": "net", "term": "terminal"}
MAX_OBJECT_REFS = 256
_PROTECTED = re.compile(
    r"`[^`]*`|\[[^\]]*\]\([^)]*\)|https?://[^\s)<>]+"
)


def _protected_ranges(line):
    return [(match.start(), match.end()) for match in _PROTECTED.finditer(line)]


def _cellview_target(path, current):
    parts = path.split("/")
    if len(parts) != 3:
        return None
    current_key = None
    if isinstance(current, dict):
        current_key = (current.get("library"), current.get("cell"), current.get("view_name"))
    if current_key == tuple(parts):
        oa_view_type = current.get("oa_view_type")
        oa_view_type_source = current.get("oa_view_type_source", "unknown")
    else:
        oa_view_type = None
        oa_view_type_source = "unknown"
    try:
        return normalize_target({
            "library": parts[0],
            "cell": parts[1],
            "view_name": parts[2],
            "oa_view_type": oa_view_type,
            "oa_view_type_source": oa_view_type_source,
        })
    except ValueError:
        return None


def linkify_cad_refs(text, target, session):
    """Linkify strict CAD reference tokens outside fences and inline code."""
    if not isinstance(text, str) or not text or not isinstance(target, dict):
        return text
    try:
        target = normalize_target(target)
        if not isinstance(session, str) or not _SESSION.fullmatch(session):
            return text
    except ValueError:
        return text
    output, link_count = [], 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue
        parts, position = [], 0
        protected = _protected_ranges(line)
        for match in _CAD_TOKEN.finditer(line):
            if link_count >= 256:
                break
            if any(
                match.start() < end and match.end() > start
                for start, end in protected
            ):
                continue
            name = match.group(2)
            try:
                if match.group(1) == "cv":
                    token_target = _cellview_target(name, target)
                    if token_target is None:
                        continue
                    ref = object_ref(token_target, {
                        "kind": "cellview", "name": name, "hierarchy": [],
                    }, require_master=False)
                else:
                    ref = object_ref(target, {
                        "kind": _TOKEN_KIND[match.group(1)], "name": name, "hierarchy": [],
                    }, require_master=False)
                href = object_link(session, ref)
            except ValueError:
                continue
            parts.append(line[position:match.start()])
            parts.append("[" + name + "](" + href + ")")
            position = match.end()
            link_count += 1
        parts.append(line[position:])
        output.append("".join(parts))
    return "".join(output)


def _object_input_refs(inputs, *, require_type=True):
    refs = {}
    for index, row in enumerate(inputs or []):
        if index >= MAX_OBJECT_REFS:
            break
        if (not isinstance(row, dict)
                or (require_type and row.get("type") != "object_ref")):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name or len(name) > 512:
            continue
        try:
            refs.setdefault(name, normalize_ref(row.get("ref")))
        except ValueError:
            continue
    return refs


def _object_block_refs(text):
    index = text.find(_OBJECT_BLOCK)
    if index < 0:
        return {}
    try:
        rows = json.loads(text[index + len(_OBJECT_BLOCK):])
    except (TypeError, ValueError):
        return {}
    return _object_input_refs(
        rows if isinstance(rows, list) else [], require_type=False
    )


def linkify_object_inputs(text, inputs, session):
    """Hide internal input blocks and linkify exact @name input references."""
    if not isinstance(text, str) or not text:
        return text
    if not isinstance(session, str) or not _SESSION.fullmatch(session):
        return text
    cut = len(text)
    for marker in (_FILE_BLOCK, _OBJECT_BLOCK):
        index = text.find(marker)
        if index >= 0:
            cut = min(cut, index)
    visible = text[:cut]
    refs = _object_input_refs(inputs) or _object_block_refs(text)
    if not refs:
        return visible
    names = sorted(refs, key=len, reverse=True)
    pattern = re.compile(
        r"@(?:" + "|".join(re.escape(name) for name in names) + r")"
        r"(?![A-Za-z0-9_./@\[\]<>:-])"
    )
    output, in_fence = [], False
    for line in visible.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence:
            output.append(line)
            continue
        protected = _protected_ranges(line)
        parts, position = [], 0
        for match in pattern.finditer(line):
            if any(match.start() < end and match.end() > start
                   for start, end in protected):
                continue
            name = match.group(0)[1:]
            try:
                href = object_link(session, refs[name])
            except ValueError:
                continue
            label = "@" + name.replace("\\", "\\\\").replace("]", "\\]")
            parts.append(line[position:match.start()])
            parts.append("[" + label + "](" + href + ")")
            position = match.end()
        parts.append(line[position:])
        output.append("".join(parts))
    return "".join(output)


def extract_cad_refs(text, target, session):
    """Return the durable references represented by linkified CAD tokens."""
    linked = linkify_cad_refs(text, target, session)
    references, seen = [], set()
    for match in re.finditer(
        r"copilot://cad/[A-Za-z0-9_-]{1,96}/[A-Za-z0-9_-]{1,4096}", linked
    ):
        try:
            found_session, ref = parse_object_link(match.group(0))
        except ValueError:
            continue
        if found_session != session:
            continue
        key = canonical(ref)
        if key not in seen:
            seen.add(key)
            references.append(ref)
        if len(references) >= 256:
            break
    return references


__all__ = [
    "MAX_OBJECT_REFS", "extract_cad_refs", "linkify_cad_refs",
    "linkify_object_inputs",
]
