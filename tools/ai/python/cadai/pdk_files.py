"""Read-only index of the PDK installation directory (workspace file layer).

This is the "source B" half of the collection chain (see
``ai/docs/PDK_COLLECTION_SPEC_V1.md`` §2.0/§2.4): the runtime SKILL capture owns DD/CDF/DB
identity, while the shipped PDK files own model decks, documented minima, rule-deck paths and
PDK document paths. Everything here is bounded, read-only and carries ``file``/``line`` evidence;
nothing is evaluated and no deck content is interpreted beyond declared names and keys.

The PDK install root always comes from the resolved library path of the live session
(``<library>/..``); a missing or unreadable root degrades to ``available: False`` instead of
failing the collection.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

MAX_DECK_FILES = 256
MAX_DECK_BYTES = 8 * 1024 * 1024
MAX_DECK_TOTAL_BYTES = 32 * 1024 * 1024
MAX_DECK_SITES = 8
MAX_LIMIT_KEYS = 64
MAX_DOC_ENTRIES = 512
MAX_RULE_DECKS = 256

CATEGORY_FILE_RE = re.compile(r'^([^/\s]+)/(\S+)\s+type="([^"]+)"')
# readme tables carry a human description next to the cell/name column; only
# identifiers can be cell names, so stray descriptions never become "documented".
CELL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
LIMIT_KEYS = frozenset({
    "lmin", "lmax", "wmin", "wmax", "nfmin", "nfmax", "rmin", "rmax",
    "cmin", "cmax", "amin", "amax", "pmin", "pmax", "segmin", "segmax",
    "mrmin", "mrmax", "dfmmin", "dfmmax",
})
# The literal (including any metric/unit suffix) is kept as evidence; the parsed value is
# kept separately, so `lmin=2.7e-008` and `lmin=0.027u` stay distinguishable.
LIMIT_VALUE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
                            r"([-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?[A-Za-z]*)")
# `w=w` style pairs mark the instance interface of a wrapped model (numbers are model parameters).
INSTANCE_ARG_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z_][A-Za-z0-9_]*)\b")
CARD_RE = re.compile(r"^(model|\.model|subckt|\.subckt)\s+(\S+)", re.IGNORECASE)
INLINE_SUBCKT_RE = re.compile(r"^inline\s+subckt\s+(\S+)", re.IGNORECASE)
END_RE = re.compile(r"^(ends|end)\b", re.IGNORECASE)
TERMINAL_RE = re.compile(r"\(([^)]*)\)")
SI_SUFFIX = {"meg": 1e6, "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6,
             "m": 1e-3, "k": 1e3, "g": 1e9}
DECK_GLOBS = ("models/spectre/**/*.mdl", "models/spectre/**/*.lib", "models/spectre/**/*.ckt",
              "models/spectre/**/*.txt", "models/hspice/**/*.mdl", "models/hspice/**/*.lib",
              "models/hspice/**/*.ckt", "models/hspice/**/*.txt")
RULE_DECK_DIRS = ("Calibre", "QRC", "StarRC", "Assura", "PVS", "Hercules", "techfile")
DOC_DIR = "docs"


def parse_si(text):
    """Parse a numeric literal with an optional metric prefix and unit suffix ("0.03um")."""
    if not isinstance(text, str):
        return None
    value = text.strip().lower()
    if not value:
        return None
    for drop in (0, 1, 2):  # drop trailing unit letters (m, F, s, Hz, ...)
        head = value[: len(value) - drop] if drop else value
        if not head:
            continue
        for suffix, scale in SI_SUFFIX.items():
            if head.endswith(suffix) and len(head) > len(suffix):
                try:
                    return float(head[: -len(suffix)]) * scale
                except ValueError:
                    continue
        try:
            return float(head)
        except ValueError:
            continue
    return None


def install_root(resolved_path) -> Path | None:
    """PDK install root = parent of the resolved library directory (source of the file layer)."""
    if not isinstance(resolved_path, str) or not resolved_path:
        return None
    path = Path(resolved_path).expanduser()
    root = path.parent if path.name else path
    try:
        return root if root.is_dir() else None
    except OSError:
        return None


def library_root(root: Path | None, library) -> Path | None:
    """Guard rails for the install root: never walk a filesystem root or a non-PDK parent."""
    if root is None:
        return None
    if root.parent == root:
        return None
    try:
        if isinstance(library, str) and library and not (root / library).is_dir():
            return None
    except OSError:
        return None
    return root


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def library_cells(root: Path, library: str) -> dict:
    """One-level listing of ``<root>/<library>/<cell>/<view>`` for exclusion reasons."""
    result = {}
    if root is None or not isinstance(library, str) or not library:
        return result
    base = root / library
    try:
        entries = sorted(entry for entry in base.iterdir() if entry.is_dir())
    except OSError:
        return result
    for entry in entries[:20000]:
        try:
            views = sorted(child.name for child in entry.iterdir() if child.is_dir())
        except OSError:
            views = []
        result[entry.name] = views
    return result


def categories_from_files(root: Path, library: str) -> dict:
    """Fallback registry from ``<library>/<Category>.Cat`` (TDM form of the ddCat* registry)."""
    result = {"cells": {}, "categories": {}, "files": []}
    if root is None or not isinstance(library, str) or not library:
        return result
    base = root / library
    try:
        paths = sorted(base.glob("*.Cat"))
    except OSError:
        return result
    for path in paths:
        name = path.stem
        entry = {"file": str(path), "cells": {}, "children": []}
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            match = CATEGORY_FILE_RE.match(line.strip())
            if not match:
                continue
            if match.group(3) == "cell":
                entry["cells"][match.group(2)] = {"line": number}
                result["cells"].setdefault(match.group(2), []).append(name)
            else:
                entry["children"].append({"name": match.group(2), "type": match.group(3), "line": number})
        entry["count"] = len(entry["cells"])
        result["categories"][name] = entry
        result["files"].append({"file": str(path), "category": name, "entries": entry["count"]})
    return result


def model_index(root: Path) -> dict:
    """Index model/subcircuit cards, declared limits, instance arguments and readme minima."""
    index = {"available": False, "root": str(root), "files": [], "models": {},
             "documented": {}, "readme_rows": {}, "digest": None, "truncated": False}
    if root is None or not root.is_dir():
        return index
    candidates = []
    for pattern in DECK_GLOBS:
        candidates.extend(sorted(root.glob(pattern)))
    if not candidates:
        return index
    index["available"] = True
    total_bytes = 0
    skipped = 0
    for path in candidates[:MAX_DECK_FILES]:
        try:
            size = path.stat().st_size
            if size > MAX_DECK_BYTES or total_bytes + size > MAX_DECK_TOTAL_BYTES:
                skipped += 1
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            skipped += 1
            continue
        total_bytes += size
        simulator = "hspice" if "hspice" in str(path) else "spectre" if "spectre" in str(path) else "other"
        readme = path.suffix == ".txt"
        index["files"].append({"file": str(path), "simulator": simulator})
        current, current_kind = None, None
        table_columns = None
        table_cells = set()
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            card = CARD_RE.match(stripped)
            inline = INLINE_SUBCKT_RE.match(stripped)
            declaration = True
            if card:
                current, current_kind = card.group(2), ("model" if "model" in card.group(1).lower() else "subckt")
            elif inline:
                current, current_kind = inline.group(1), "subckt"
            elif END_RE.match(stripped):
                current, current_kind = None, None
            else:
                declaration = False
            if current is not None:
                entry = index["models"].setdefault(current, {
                    "decks": [], "limits": {}, "terminals": [], "instance_args": [], "kind": current_kind})
                # Declaration sites only (a corner-sectioned deck declares one model
                # several times); comment/parameter lines are not declaration sites.
                if declaration and len(entry["decks"]) < MAX_DECK_SITES:
                    entry["decks"].append({"simulator": simulator, "kind": current_kind,
                                           "file": str(path), "line": number})
                if current_kind == "subckt" and not entry["terminals"]:
                    match = TERMINAL_RE.search(stripped)
                    entry["terminals"] = [t for t in (match.group(1).replace(",", " ") if match else "").split() if t]
                for key, raw in LIMIT_VALUE_RE.findall(line):
                    key = key.lower()
                    if key in LIMIT_KEYS and key not in entry["limits"] and len(entry["limits"]) < MAX_LIMIT_KEYS:
                        value = parse_si(raw)
                        if value is not None:
                            entry["limits"][key] = {"value": value, "as_entered": raw,
                                                    "file": str(path), "line": number}
                for left, right in INSTANCE_ARG_RE.findall(line):
                    for token in (left, right):
                        if token != current and token not in entry["instance_args"] and len(entry["instance_args"]) < 64:
                            entry["instance_args"].append(token)
            if readme and stripped.startswith("|"):
                cells = [cell.strip() for cell in stripped.strip("|").split("|")]
                for cell in cells:
                    # Every cell named anywhere in the readme tables is a candidate for
                    # raw line evidence, not only the cells of the minima table.
                    if len(table_cells) < 512 and cell and CELL_NAME_RE.match(cell):
                        table_cells.add(cell)
                if "min" in stripped.lower() and "name" in stripped.lower():
                    table_columns = cells
                elif table_columns and len(cells) == len(table_columns):
                    headers = {name.lower(): index_ for index_, name in enumerate(table_columns)}
                    for cell in cells:
                        if not cell or not CELL_NAME_RE.match(cell) or cell in index["documented"]:
                            continue
                        parsed = {}
                        for key, header in (("l", "lmin"), ("w", "wmin")):
                            position = headers.get(header)
                            if position is not None and position < len(cells) and cells[position]:
                                value = parse_si(cells[position].lower())
                                if value is not None:
                                    parsed[key] = {"min": value, "as_entered": cells[position]}
                        if parsed:
                            index["documented"][cell] = {"limits": parsed, "headers": table_columns,
                                                         "file": str(path), "line": number, "row": cells}
        if readme and table_cells:
            # Raw, uninterpreted readme evidence per named cell (spec §2.2 C17/C19:
            # "readme 其它表格/分类矩阵行，≤6 行/器件，带 file+line"). One bounded pass
            # over the lines buckets them by identifier, so no per-cell rescanning.
            buckets = {}
            for number, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                for token in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", stripped)):
                    if token in table_cells:
                        rows = buckets.setdefault(token, [])
                        if len(rows) < 6:
                            rows.append({"text": stripped[:200], "file": str(path), "line": number})
            for token, rows in buckets.items():
                index["readme_rows"][token] = rows
    # A truncated deck index cannot prove that a model is *absent* (D4), so the
    # resolution keeps that uncertainty instead of excluding a device.
    index["truncated"] = bool(skipped) or len(candidates) > MAX_DECK_FILES
    index["digest"] = index_digest(index)
    return index


def index_digest(index: dict) -> str:
    """Content digest of the file-layer index; excludes nothing (paths are part of the evidence)."""
    return _digest({"files": index.get("files", []),
                    "models": {name: {"decks": entry.get("decks", []),
                                      "limits": entry.get("limits", {}),
                                      "terminals": entry.get("terminals", []),
                                      "instance_args": entry.get("instance_args", [])}
                               for name, entry in (index.get("models") or {}).items()},
                    "documented": index.get("documented", {})})


def resolve_model(index: dict, model, cell):
    """Resolve a CDF model default against the deck index (cell-name fallback for passives)."""
    models = index.get("models") or {}
    for candidate in (model, cell):
        if isinstance(candidate, str) and candidate in models:
            entry = models[candidate]
            return {"queried": model, "resolved": candidate, "status": "found",
                    "decks": entry["decks"][:4], "terminals": entry["terminals"],
                    "instance_args": entry["instance_args"], "limits": entry["limits"],
                    "documented": (index.get("documented") or {}).get(candidate),
                    "readme": (index.get("readme_rows") or {}).get(candidate, []),
                    "basis": "model_name" if candidate == model else "cell_name"}
    if not index.get("available"):
        return {"queried": model, "resolved": None, "status": "decks_unavailable",
                "decks": [], "terminals": [], "instance_args": [], "limits": {}, "documented": None,
                "readme": [], "basis": None}
    if index.get("truncated"):
        return {"queried": model, "resolved": None, "status": "decks_truncated",
                "decks": [], "terminals": [], "instance_args": [], "limits": {}, "documented": None,
                "readme": [], "basis": None}
    return {"queried": model, "resolved": None, "status": "not_found_in_shipped_decks",
            "decks": [], "terminals": [], "instance_args": [], "limits": {}, "documented": None,
            "readme": [], "basis": None}


def candidate_terminal_map(cdf_ports, deck_terminals):
    """Name-matched pairs first (high confidence), positional pairs for the rest (low)."""
    if not deck_terminals or len(deck_terminals) != len(cdf_ports):
        return []
    if [p.casefold() for p in cdf_ports] == [t.casefold() for t in deck_terminals]:
        return []
    pairs, remaining = [], list(cdf_ports)
    pool = list(deck_terminals)
    for port in list(remaining):
        match = next((t for t in pool if t.casefold() == port.casefold()), None)
        if match is not None:
            pairs.append({"cdf": port, "deck": match, "basis": "name", "confidence": "high"})
            remaining.remove(port)
            pool.remove(match)
    for port, terminal in zip(remaining, pool):
        pairs.append({"cdf": port, "deck": terminal, "basis": "position", "confidence": "low"})
    return pairs


def rule_decks(root: Path) -> list:
    """Rule-deck paths only (content is owned by EDA tools and never read here)."""
    rows = []
    if root is None or not root.is_dir():
        return rows
    for name in RULE_DECK_DIRS:
        base = root / name
        try:
            exists = base.is_dir()
        except OSError:
            exists = False
        if exists:
            rows.append({"kind": name, "path": str(base),
                         "description": name + " rule/setup deck directory (content not indexed)"})
    for pattern, description in (("**/calibre*", "Calibre deck"), ("**/*.drc*", "DRC rule file"),
                                 ("**/*.lvs*", "LVS rule file"), ("**/techfile*", "technology file")):
        try:
            found = sorted(root.glob(pattern))[:MAX_RULE_DECKS]
        except OSError:
            found = []
        for path in found:
            if len(rows) >= MAX_RULE_DECKS:
                break
            rows.append({"kind": "file", "path": str(path), "description": description})
    return rows


def documents(root: Path) -> list:
    """PDK document index (paths first; text extraction with page anchors is a later stage)."""
    rows = []
    if root is None or not root.is_dir():
        return rows
    candidates = []
    try:
        candidates.extend(sorted((root / DOC_DIR).rglob("*")) if (root / DOC_DIR).is_dir() else [])
        candidates.extend(sorted(root.glob("*.pdf")))
    except OSError:
        candidates = []
    for path in candidates:
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        if len(rows) >= MAX_DOC_ENTRIES:
            break
        suffix = path.suffix.lower()
        rows.append({"kind": "document", "path": str(path), "format": suffix.lstrip(".") or "unknown",
                    "size": stat.st_size, "relative": str(path.relative_to(root)),
                    "text_anchors": "pending"})
    return rows


def file_index(root: Path | None, library=None) -> dict:
    """One bounded, read-only snapshot of the install-directory layer (spec §2.4).

    Merges the model-deck index, the rule-deck path list, the document index and the
    ``<library>/<cell>/<view>`` listing used for exclusion reasons.  A missing root, a
    filesystem root or a root that does not contain the library degrades to
    ``available: False`` instead of failing or walking unrelated directories.
    """
    root = library_root(root, library)
    models = model_index(root)
    result = {"available": models["available"], "root": models["root"], "digest": models["digest"],
              "models": models["models"], "documented": models["documented"],
              "readme_rows": models["readme_rows"], "deck_files": models["files"],
              "rule_decks": rule_decks(root), "documents": documents(root),
              "library_cells": library_cells(root, library), "library": library,
              "scope": "front_end_models_docs_paths" if models["available"] else "unavailable",
              "note": "models/index read-only; rule decks keep paths only (D3)"}
    result["document_entries"] = len(result["documents"])
    return result
