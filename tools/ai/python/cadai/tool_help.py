"""Installed Cadence manual discovery, Search-backed lookup, and HTML reading."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote

from .search import search
from .search_tools import _text
from .socket_server import RequestFailure
from .tool_help_html import read_page
from .tool_help_index import query_index
from .tool_help_schema import TOOL_ENV, TOOL_HELP_TOOL, discover, installation


def options(arguments):
    properties = TOOL_HELP_TOOL["inputSchema"]["properties"]
    if set(arguments) - set(properties):
        raise RequestFailure("invalid_params", "unknown tool_help argument")
    action = arguments.get("action", "discover")
    if action not in ("discover", "index", "search", "files", "read"):
        raise RequestFailure(
            "invalid_params", "action must be discover, index, search, files or read"
        )
    allowed = {"action", "tool", "install_root"}
    if action in ("search", "files"):
        allowed |= {"manual", "max_results", "timeout_seconds"}
    if action == "index":
        allowed |= {"query", "kind", "offset", "max_results"}
    if action == "search":
        allowed.add("query")
    if action == "read":
        allowed |= {"page", "offset", "max_chars", "sha256"}
    if set(arguments) - allowed:
        raise RequestFailure("invalid_params", "argument does not apply to selected action")
    for name in ("tool", "install_root", "query", "manual", "page", "sha256"):
        if name in arguments:
            _text(arguments[name], name, properties[name].get("maxLength", 64))
    if "kind" in arguments and arguments["kind"] not in (
        "all",
        "manual",
        "help_mapping",
        "support",
    ):
        raise RequestFailure("invalid_params", "invalid index kind")
    if "tool" in arguments and arguments["tool"] not in TOOL_ENV:
        raise RequestFailure("invalid_params", "unknown tool; use explicit install_root")
    if "tool" in arguments and "install_root" in arguments:
        raise RequestFailure("invalid_params", "select tool OR install_root")
    for name, maximum in (
        ("max_results", 100),
        ("timeout_seconds", 30),
        ("max_chars", 6000),
        ("offset", 1024 * 1024),
    ):
        if name in arguments:
            value = arguments[name]
            if type(value) is not int or not (0 if name == "offset" else 1) <= value <= maximum:
                raise RequestFailure("invalid_params", f"invalid {name}")
    if "sha256" in arguments and not re.fullmatch("[a-f0-9]{64}", arguments["sha256"]):
        raise RequestFailure("invalid_params", "invalid sha256")
    if action == "search" and "query" not in arguments:
        raise RequestFailure("invalid_params", "search requires query")
    if action == "read" and "page" not in arguments:
        raise RequestFailure("invalid_params", "read requires page")
    return action


def tool_help(arguments, workspace=None):
    action = options(arguments)
    if action == "index":
        return query_index(arguments)
    if "install_root" in arguments:
        root, doc = installation(arguments["install_root"])
        found = [
            {
                "install_root": str(root),
                "doc_root": str(doc),
                "installation": root.name,
                "discovered_by": ["explicit"],
            }
        ]
    else:
        found = discover()
        if "tool" in arguments:
            found = [item for item in found if item["tool"] == arguments["tool"]]
    if action == "discover":
        result = {
            "ok": True,
            "installations": found,
            "truncated": False,
            "hint": "tool selects PATH first; use install_root to select another listed version. "
            "For Virtuoso, action=index helps when the manual is unknown or results are noisy.",
        }
        while len(json.dumps(result, ensure_ascii=True).encode()) > 65536 and found:
            found.pop()
            result["truncated"] = True
        return result
    if "tool" in arguments:
        path_selected = [
            item
            for item in found
            if any(value.startswith("PATH:") for value in item["discovered_by"])
        ]
        if path_selected:
            found = path_selected[:1]
    if len(found) != 1:
        raise RequestFailure(
            "manual_selection_required",
            "Select install_root or an unambiguous tool; use discover first",
        )
    selected = found[0]
    root, doc = installation(selected["install_root"])
    if action == "read":
        result = read_page(
            doc,
            arguments["page"],
            arguments.get("offset", 0),
            arguments.get("max_chars", 6000),
            arguments.get("sha256"),
        )
    else:
        scope = doc
        if "manual" in arguments:
            try:
                relative = Path(arguments["manual"])
                if relative.is_absolute():
                    raise ValueError("manual must be doc-relative")
                scope = (doc / relative).resolve(strict=True)
                scope.relative_to(doc)
                if not scope.is_dir():
                    raise ValueError("manual must be a directory")
            except (OSError, ValueError, RuntimeError) as exc:
                raise RequestFailure("invalid_page", str(exc)) from exc
        filters = ["*.html", "*.htm", "*.xhtml", "*.HTML", "*.HTM", "*.XHTML"]
        request = {
            "mode": "files" if action == "files" else "content",
            "path": str(scope),
            "globs": filters,
            "respect_ignore": False,
            "max_results": arguments.get("max_results", 20),
            "timeout_seconds": arguments.get("timeout_seconds", 10),
        }
        if action == "search":
            request.update(pattern=arguments["query"], fixed_strings=True, case_sensitive=False)
        result = search(request, doc)
        for item in result["results"]:
            item["page"] = quote((scope.relative_to(doc) / item["path"]).as_posix(), safe="/")
            if "text" in item:
                item["html_source_excerpt"] = item.pop("text")
        result["search_basis"] = "HTML source; markup/entities may split visible phrases"
    result["source"] = selected
    result["action"] = action
    # Keep local documentation output within the existing Search response budget.
    while len(json.dumps(result, ensure_ascii=True).encode()) > 65536:
        if result.get("links"):
            result["links"].pop()
            result["links_truncated"] = True
        elif result.get("results"):
            result["results"].pop()
            result.update(
                returned=len(result["results"]),
                complete=False,
                truncated=True,
                truncation_reason="output_limit",
            )
        else:
            raise RequestFailure("manual_output_limit", "Narrow the request to reduce output")
    return result
