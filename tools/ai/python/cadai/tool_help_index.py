"""Small metadata index for narrowing Virtuoso manual searches without scanning HTML."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .socket_server import RequestFailure
from .tool_help_index_data import ROWS, SOURCE_DATE, SOURCE_SHA256, SOURCE_VERSION
from .tool_help_schema import discover, installation


def _kind(description):
    if "帮助上下文映射表" in description:
        return "help_mapping"
    return "manual" if "《" in description else "support"


def _present(doc, directory):
    if doc is None:
        return None
    try:
        candidate = (doc / directory).resolve(strict=True)
        candidate.relative_to(doc)
        return candidate.is_dir()
    except (OSError, ValueError, RuntimeError):
        return False


def query_index(arguments):
    if arguments.get("tool", "virtuoso") != "virtuoso":
        raise RequestFailure(
            "index_unavailable", "The bundled index currently covers Virtuoso only"
        )
    selected = None
    doc = None
    if "install_root" in arguments:
        root, doc = installation(arguments["install_root"])
        selected = {"install_root": str(root), "installation": root.name, "doc_root": str(doc)}
    else:
        installations = [item for item in discover() if item["tool"] == "virtuoso"]
        path_selected = [
            item
            for item in installations
            if any(value.startswith("PATH:") for value in item["discovered_by"])
        ]
        if path_selected or len(installations) == 1:
            selected = (path_selected or installations)[0]
            doc = Path(selected["doc_root"])
    query = arguments.get("query", "").casefold()
    words = query.split()
    kind = arguments.get("kind", "all")
    matches = []
    for directory, product, description in ROWS:
        entry_kind = _kind(description)
        if kind != "all" and kind != entry_kind:
            continue
        haystack = " ".join((directory, product, description)).casefold()
        if not all(word in haystack for word in words):
            continue
        score = 100 if query and query == directory.casefold() else 0
        score += sum(10 for word in words if word in directory.casefold())
        score += sum(3 for word in words if word in product.casefold())
        title = description.split("》", 1)[0].casefold() if "《" in description else ""
        score += 30 if query and query in title else 0
        score += sum(
            5
            for word in words
            if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", haystack)
        )
        score += int(entry_kind == "manual")
        entry = {
            "manual": directory,
            "product": product,
            "description": description,
            "kind": entry_kind,
            "directory_exists": _present(doc, directory),
        }
        if entry_kind == "help_mapping":
            targets = re.findall(r"`([^`]+)`", description)
            entry["listed_targets"] = [
                {"manual": target, "directory_exists": _present(doc, target)} for target in targets
            ]
            entry["targets_exhaustive"] = False
        matches.append((score, entry))
    matches.sort(key=lambda value: (-value[0], value[1]["manual"].casefold(), value[1]["manual"]))
    offset = arguments.get("offset", 0)
    if offset > len(matches):
        raise RequestFailure("invalid_params", "offset exceeds matching index entries")
    entries = [entry for _, entry in matches[offset : offset + arguments.get("max_results", 20)]]
    result = {
        "ok": True,
        "action": "index",
        "tool": "virtuoso",
        "query": arguments.get("query", ""),
        "index": {
            "version": SOURCE_VERSION,
            "generated": SOURCE_DATE,
            "entries": len(ROWS),
            "sha256": SOURCE_SHA256,
            "source": "docs/tool-help/IC_doc_index.md",
        },
        "source": selected,
        "entries": entries,
        "total_matches": len(matches),
        "offset": offset,
        "hint": "Use a manual entry or listed mapping target as manual in files/search, then read "
        "a returned page. This version-specific index is a navigation hint; directory_exists "
        "does not verify HTML availability or version compatibility. Null means unchecked.",
    }
    while True:
        end = offset + len(entries)
        result.update(
            returned=len(entries),
            next_offset=end if end < len(matches) else None,
            complete=end == len(matches),
            truncated=end < len(matches),
        )
        if len(json.dumps(result, ensure_ascii=True).encode()) <= 65536:
            return result
        if not entries:
            raise RequestFailure(
                "manual_output_limit", "Index source metadata exceeds output limit"
            )
        entries.pop()
