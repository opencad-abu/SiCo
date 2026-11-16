"""Build private effective Calibre decks and derived flow configurations."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..codec import ArtifactRef
from ..errors import HubError
from .jobs import JobStore
from .runset import expand_tvf_runset

MAX_DECK_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class PreparedFlow:
    config: ArtifactRef
    effective_deck: ArtifactRef
    preparation: Mapping[str, object]


def _read_text(path):
    path = Path(path).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise HubError("invalid_arguments", "Calibre deck must name an absolute file")
    try:
        if path.stat().st_size > MAX_DECK_BYTES:
            raise HubError("result_too_large", "Calibre deck exceeds its limit")
        return path, path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HubError("read_failed", "Calibre deck cannot be read") from exc


def _quoted(value):
    return json.dumps(str(value))


def _replace_statement(text, pattern, replacement, *, flags=re.IGNORECASE | re.MULTILINE):
    regex = re.compile(pattern, flags)
    count = len(regex.findall(text))
    if count:
        text = regex.sub(lambda _match: replacement, text)
    return text, count


def _replace_mask_path(text, path):
    pattern = re.compile(
        r"^(\s*MASK\s+SVDB\s+DIRECTORY)\s+(?:\"[^\"]*\"|\S+)(.*)$",
        re.IGNORECASE | re.MULTILINE,
    )
    count = len(pattern.findall(text))
    if count:
        text = pattern.sub(
            lambda match: f'{match.group(1)} {_quoted(path)}{match.group(2)}',
            text,
        )
    return text, count


def _bind_paths(flow, text, *, layout_system="GDSII"):
    if not flow.layout_path:
        raise HubError("invalid_arguments", "Calibre layout path is unavailable")
    required = []
    applied = {}

    def apply(name, pattern, replacement):
        nonlocal text
        text, count = _replace_statement(text, pattern, replacement)
        applied[name] = count
        if not count:
            required.append(replacement)

    apply(
        "layout_primary",
        r"^\s*LAYOUT\s+PRIMARY\b.*$",
        f"LAYOUT PRIMARY {_quoted(flow.top_cell)}",
    )
    apply(
        "layout_path",
        r"^\s*LAYOUT\s+PATH\b.*$",
        f"LAYOUT PATH {_quoted(flow.layout_path)}",
    )
    apply(
        "layout_system",
        r"^\s*LAYOUT\s+SYSTEM\s+(?:GDSII|OASIS)\b.*$",
        f"LAYOUT SYSTEM {layout_system}",
    )
    if flow.kind == "drc":
        apply(
            "drc_results",
            r"^\s*DRC\s+RESULTS\s+DATABASE\b.*$",
            f"DRC RESULTS DATABASE {_quoted(flow.results)} ASCII",
        )
        apply(
            "drc_summary",
            r"^\s*DRC\s+SUMMARY\s+REPORT\b.*$",
            f"DRC SUMMARY REPORT {_quoted(flow.summary)}",
        )
    else:
        if not flow.source_path:
            raise HubError("invalid_arguments", "Calibre LVS source path is unavailable")
        if flow.svdb_path is None:
            raise HubError("invalid_arguments", "Calibre LVS SVDB path is unavailable")
        apply(
            "source_cell",
            r"^\s*SOURCE\s+PRIMARY\b.*$",
            f"SOURCE PRIMARY {_quoted(flow.source_cell)}",
        )
        apply(
            "source_path",
            r"^\s*SOURCE\s+PATH\b.*$",
            f"SOURCE PATH {_quoted(flow.source_path)}",
        )
        apply(
            "source_system",
            r"^\s*SOURCE\s+SYSTEM\s+SPICE\b.*$",
            "SOURCE SYSTEM SPICE",
        )
        apply(
            "lvs_report",
            r"^\s*LVS\s+REPORT\s+\"[^\"]*\"\s*$",
            f"LVS REPORT {_quoted(flow.report)}",
        )
        apply(
            "erc_results",
            r"^\s*ERC\s+RESULTS\s+DATABASE\b.*$",
            f"ERC RESULTS DATABASE {_quoted(flow.log_dir / 'erc.results')}",
        )
        apply(
            "erc_summary",
            r"^\s*ERC\s+SUMMARY\s+REPORT\b.*$",
            f"ERC SUMMARY REPORT {_quoted(flow.log_dir / 'erc.rep')}",
        )
        text, count = _replace_mask_path(text, flow.svdb_path)
        applied["svdb"] = count
        if not count:
            required.append(
                f"MASK SVDB DIRECTORY {_quoted(flow.svdb_path)} QUERY"
            )
    if required:
        text = text.rstrip("\n") + "\n\n// SiCo DataHub bound paths\n"
        text += "\n".join(required) + "\n"
    return text, applied


def _derive_config(config_path, kind, deck_path):
    config_path, text = _read_text(config_path)
    section = re.compile(
        r"^\s*\[" + re.escape(kind) + r"\]\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = section.search(text)
    if match is None:
        raise HubError(
            "invalid_arguments",
            f"Calibre config has no [{kind}] section",
        )
    next_section = re.compile(r"^\s*\[", re.MULTILINE).search(text, match.end())
    end = next_section.start() if next_section is not None else len(text)
    body = text[match.end():end]
    runset = re.compile(r"^[ \t]*runset_file\s*=\s*.*$", re.MULTILINE)
    line = "runset_file = " + _quoted(deck_path)
    if runset.search(body):
        body = runset.sub(lambda _match: line, body, count=1)
    else:
        body = "\n" + line + body
    return config_path, text[:match.end()] + body + text[end:]


def prepare_flow(
    flow,
    *,
    store: JobStore,
    run_ref,
    calibre_executable,
    timeout=180.0,
    layout_system="GDSII",
):
    """Create a private path-bound deck and derived TOML for one job."""
    if type(timeout) not in {int, float} or not 0 < timeout <= 1800:
        raise HubError("invalid_arguments", "Invalid Calibre deck timeout")
    if layout_system not in {"GDSII", "OASIS"}:
        raise HubError("invalid_arguments", "Invalid Calibre layout system")
    runset = Path(flow.runset.path)
    _path, original_text = _read_text(runset)
    first_line = next(
        (line.strip() for line in original_text.splitlines() if line.strip()),
        "",
    )
    expanded = False
    cached = False
    source_kind = "svrf"
    effective_text = original_text
    if first_line.lower().startswith("#!") and "tvf" in first_line.lower():
        source_kind = "tvf"
        expanded_path, cached = expand_tvf_runset(
            runset,
            calibre=str(calibre_executable),
            timeout=timeout,
            cache_dir=str(store.cache / "decks"),
            dependency_sha256=flow.runset_identity.dependencies_sha256,
        )
        _expanded_path, effective_text = _read_text(expanded_path)
        expanded = True
    effective_text, applied = _bind_paths(
        flow, effective_text, layout_system=layout_system
    )
    deck_path = store.prepared_deck_path(run_ref, flow.kind)
    deck_ref = store.write_text(deck_path, effective_text)
    _config_path, config_text = _derive_config(
        flow.config.path, flow.kind, deck_path
    )
    config_path = store.config_path(run_ref)
    config_ref = store.write_text(config_path, config_text)
    return PreparedFlow(
        config=config_ref,
        effective_deck=deck_ref,
        preparation={
            "source": source_kind,
            "expanded": expanded,
            "cached": cached,
            "directives": applied,
        },
    )


__all__ = ["MAX_DECK_BYTES", "PreparedFlow", "prepare_flow"]
