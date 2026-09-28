"""Normalize provider model paths and option snapshots without running EDA."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence
from .defaults_values import _normalise, _normalize_named_options


def normalize_model_files(
    value: Any,
    *,
    base_dirs: Sequence[str | Path] = (),
    model_paths: Sequence[str | Path] = (),
) -> tuple[dict[str, Any], ...]:
    """Normalize ASI ``modelFiles`` pairs, including disabled ``#`` rows."""

    if isinstance(value, Mapping):
        value = value.get("model_files", value.get("entries", ()))
    if not isinstance(value, (list, tuple)):
        return ()
    rows: list[dict[str, Any]] = []
    for entry in value:
        if isinstance(entry, Mapping):
            row = dict(entry)
            raw_file = str(row.get("file", row.get("path", "")))
            section = str(row.get("section", ""))
            enabled = bool(row.get("enabled", True))
        elif isinstance(entry, (list, tuple)) and entry:
            row = {}
            # Cadence uses both ``(path section)`` and
            # ``("#" path section)`` for disabled model corners.
            if str(entry[0]) == "#" and len(entry) > 1:
                raw_file = "#" + str(entry[1])
                section = str(entry[2]) if len(entry) > 2 else ""
                enabled = False
            else:
                raw_file = str(entry[0])
                section = str(entry[1]) if len(entry) > 1 else ""
                enabled = True
        else:
            row = {}
            raw_file = str(entry)
            section = ""
            enabled = True
        original = raw_file
        if raw_file.startswith("#"):
            raw_file = raw_file[1:]
            enabled = False
        row.update({"file": raw_file, "section": section, "enabled": enabled})
        row["raw_file"] = original
        candidate = Path(raw_file).expanduser() if raw_file else None
        if candidate is not None and not candidate.is_absolute():
            # A relative model file is normally interpreted against either
            # the source project or a model/include directory.  Relative
            # include directories in a Maestro setup must in turn be rooted
            # at the source project; resolving them directly would incorrectly
            # use this Python process's current directory.
            base_roots: list[Path] = []
            for base in base_dirs:
                root = Path(base).expanduser().resolve()
                base_roots.append(root)
            resolved_roots = list(base_roots)
            for model_path in model_paths:
                search = Path(model_path).expanduser()
                if search.is_absolute():
                    resolved_roots.append(search.resolve())
                else:
                    resolved_roots.extend(
                        root / search for root in base_roots
                    )
            for root in resolved_roots:
                resolved = root / candidate
                if resolved.is_file():
                    candidate = resolved
                    break
        if candidate is not None and candidate.is_absolute():
            row["resolved_file"] = str(candidate.resolve())
        rows.append(_normalise(row))
    return tuple(rows)


def _model_search_paths(options: Mapping[str, Any]) -> tuple[str | Path, ...]:
    """Extract model/include search roots from normalized environment data."""

    paths: list[str | Path] = []

    def append_value(value: Any, *, enabled_rows: bool = False) -> None:
        if isinstance(value, str):
            # Cadence path lists are conventionally colon-separated.  A
            # single path containing a colon is not a supported OA path, so
            # splitting here matches the simulator's interpretation.
            paths.extend(part for part in value.split(":") if part.strip())
        elif isinstance(value, Mapping):
            # _normalize_named_options turns a list of (enabled path) rows
            # into a mapping, e.g. {"True": "./models"}.  Recover that
            # representation here without treating disabled rows as search
            # roots.  A non-boolean mapping is retained for compatibility
            # with releases that use named path entries instead.
            for key, item in value.items():
                enabled = str(key).casefold()
                if enabled_rows and enabled in {"false", "nil", "0", "no", "off"}:
                    continue
                append_value(item, enabled_rows=enabled_rows)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, (list, tuple)):
                    # allIncludedPaths rows are usually (enabled path), but
                    # tolerate a plain (path ...) shape too.
                    if len(item) >= 2 and isinstance(item[0], bool):
                        if item[0]:
                            append_value(item[1], enabled_rows=enabled_rows)
                    elif item:
                        append_value(item[-1], enabled_rows=enabled_rows)
                else:
                    append_value(item, enabled_rows=enabled_rows)

    folded = {str(key).casefold(): value for key, value in options.items()}
    for name in ("modelpath", "includepath", "allincludedpaths"):
        append_value(folded.get(name), enabled_rows=name == "allincludedpaths")
    # Preserve order but avoid duplicate roots; this matters when a PDK
    # reports the same include directory in both includePath and its expanded
    # allIncludedPaths list.
    unique: list[str | Path] = []
    seen: set[str] = set()
    for path in paths:
        text = str(path).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(path)
    return tuple(unique)


def normalize_snapshot(
    snapshot: Any, *, base_dirs: Sequence[str | Path] = ()
) -> dict[str, Any]:
    if not isinstance(snapshot, Mapping):
        return {"model_files": [], "environment_options": {}, "simulator_options": {}}
    result = {str(key): _normalise(value) for key, value in snapshot.items()}
    result["environment_options"] = _normalize_named_options(
        snapshot.get("environment_options", {})
    )
    # Cadence providers use several names for the directory that owns a
    # relative model file.  ASI generally exposes ``modelPath`` while MAE
    # environments commonly expose ``includePath`` and
    # ``allIncludedPaths``.  Keep the raw environment options intact, but
    # make all of these search roots available to the model-file resolver.
    model_paths = _model_search_paths(result["environment_options"])
    result["model_files"] = list(
        normalize_model_files(
            snapshot.get("model_files"),
            base_dirs=base_dirs,
            model_paths=model_paths,
        )
    )
    if "raw_model_files" in snapshot:
        result["raw_model_files"] = list(
            normalize_model_files(
                snapshot.get("raw_model_files"),
                base_dirs=base_dirs,
                model_paths=model_paths,
            )
        )
    result["simulator_options"] = _normalize_named_options(
        snapshot.get("simulator_options", {})
    )
    for name in ("environment_options", "simulator_options"):
        result[name] = _normalise(result[name])
    return result
