"""Rebind persisted observations after CAD moves an unchanged PDK installation."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

from .pdk_normalize import detail, device_ref, digest


def changed_paths(saved, current):
    """Accept path/stamp changes only; collector and technology identity still matter."""
    before, after = deepcopy(saved), deepcopy(current)
    changes = []
    for role, old, new in (
        ("library", before["library"], after["library"]),
        ("technology", before["library"]["technology_binding"], after["library"]["technology_binding"]),
    ):
        old_path, new_path = old.get("resolved_path"), new.get("resolved_path")
        if old_path != new_path:
            if not old_path or not new_path:
                return None
            changes.append({"role": role, "captured_path": old_path, "current_path": new_path})
            old["resolved_path"] = new_path
            stamp = "path_stamp" if role == "library" else "technology_stamp"
            before[stamp] = after[stamp]
    if not changes:
        return None
    for name in ("library_ref", "revision"):
        before["library"][name] = after["library"][name]
    if before != after:
        return None
    mappings = {row["captured_path"]: row["current_path"] for row in changes}
    # Model decks and rules typically live alongside the OA library.
    for row in changes:
        old, new = Path(row["captured_path"]).parent, Path(row["current_path"]).parent
        if old != new and str(old) != "/" and str(new) != "/":
            mappings.setdefault(str(old), str(new))
    return {"paths": changes, "mappings": mappings, "library": deepcopy(saved["library"])}


def replace_paths(value, mappings):
    # One pass handles multiple roots in a CDF expression without rewriting a
    # replacement again or matching a root inside an unrelated absolute path.
    roots = "|".join(re.escape(old) for old in sorted(mappings, key=len, reverse=True))
    pattern = re.compile(r"(?<![\w./~+-])(?:" + roots + r")(?=$|[/\s\"'():;,])")

    def replace(item):
        if isinstance(item, str):
            return pattern.sub(lambda match: mappings[match.group()], item)
        if isinstance(item, list):
            return [replace(row) for row in item]
        if isinstance(item, dict):
            return {key: replace(row) for key, row in item.items()}
        return item

    return replace(value) if mappings else deepcopy(value)


def device(value, library, relocation):
    result = replace_paths(value, relocation["mappings"])
    old_ref = result["device_ref"]
    ref = device_ref(result["identity"]["items"][0], library)
    refs = {old_ref: ref, result["library_ref"]: library["library_ref"]}
    for port in result["ports"]["items"]:
        refs[port["port_ref"]] = "port:" + digest([ref, port["name"]])
    for figure in result["geometry"]["items"]:
        port_ref = "port:" + digest([ref, figure["terminal"]])
        refs[figure["port_ref"]] = port_ref
        refs[figure["figure_ref"]] = "figure:" + digest([port_ref, figure["pin_index"], figure["figure_index"]])
    for callback in result["callbacks"]["items"]:
        owner = {**callback["owner"], "device_ref": ref}
        refs[callback["callback_ref"]] = "callback:" + digest([owner, callback["raw"]])
        callback["digest"] = digest(callback["raw"])

    def rebind(item):
        if isinstance(item, str):
            return refs.get(item, item)
        if isinstance(item, list):
            return [rebind(row) for row in item]
        if isinstance(item, dict):
            return {key: rebind(row) for key, row in item.items()}
        return item

    result = rebind(result)
    result["library"] = deepcopy(library)
    # CDF/database hashes retain the original observation scope; observed_device
    # reverse-maps live data to that scope before comparing them. Identity and
    # library hashes, along with every public reference, use the current root.
    result["dependency_digests"]["library"] = digest(library)
    result["dependency_digests"]["identity"] = digest(result["identity"]["items"][0])
    result["revision"] = digest(result["dependency_digests"])
    return result


def observed_device(capture, ctx, relocation):
    """Compare live metadata using the same original-path fingerprint scope."""
    library = next(row for row in ctx["libraries"] if row["name"] == relocation["library"]["name"])
    original_ctx = deepcopy(ctx)
    original_ctx["libraries"] = [deepcopy(relocation["library"]) if row["name"] == library["name"] else row
                                 for row in original_ctx["libraries"]]
    raw = replace_paths(capture["data"], {new: old for old, new in relocation["mappings"].items()})
    return device(detail(raw, original_ctx), library, relocation)


def notice(library, relocation):
    paths = relocation["paths"]
    rendered = list(dict.fromkeys(row["captured_path"] + " → " + row["current_path"] for row in paths))
    return {"code": "pdk_path_changed", "library": library, "paths": paths,
            "message": "PDK " + library + " 路径发生变更：" + "；".join(rendered)
                       + "。继续使用已采集数据，器件引用已适配到当前路径。",
            "action": "reuse_collected_data", "user_input_required": False}
