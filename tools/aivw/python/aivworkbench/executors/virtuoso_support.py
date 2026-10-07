"""Shared Virtuoso executor adapters, artifact IO, and blocked results."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from sicoentry import compiled_module
from typing import Any, Mapping, Sequence

from ..executor import ExecutorContext, ExecutorResult
from ..profiles import product_root
from ..cdns_ipc import McpToolCall, McpToolResult, call_read_only_tools
from ..workspace import write_json_once
from ..agent.runtime_info import production_python

_MAX_ITEMS = 100

def _digest(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)



def _call_snapshot_tools(
    calls: Sequence[McpToolCall],
    *,
    environment: Mapping[str, str],
    cad_ai_entry: Path,
    timeout: float,
) -> tuple[McpToolResult, ...]:
    return call_read_only_tools(
        calls,
        environment=environment,
        cad_ai_entry=cad_ai_entry,
        timeout=timeout,
    )


def _write_raw_payloads(
    context: ExecutorContext,
    calls: Sequence[McpToolCall],
    payloads: Sequence[Mapping[str, Any]],
) -> tuple[Path, ...]:
    raw_root = context.gate_root / "raw"
    raw_root.mkdir()
    counts: dict[str, int] = {}
    paths = []
    for call, payload in zip(calls, payloads):
        counts[call.name] = counts.get(call.name, 0) + 1
        suffix = counts[call.name]
        label = {
            ("get_context", 1): "context-before",
            ("get_context", 2): "context-after",
            ("inspect_library", 1): "library-before",
            ("inspect_library", 2): "library-after",
            ("inspect_schematic", 1): "schematic-before",
            ("inspect_schematic", 2): "schematic-after",
            ("inspect_symbol_ports", 1): "symbol-before",
            ("inspect_symbol_ports", 2): "symbol-after",
        }.get((call.name, suffix), f"{call.name}-{suffix}")
        path = raw_root / f"{label}.json"
        write_json_once(path, payload)
        paths.append(path)
    return tuple(paths)


def _run_normalizer(
    schematic: Path,
    symbol: Path,
    output: Path,
    manifest: Path,
    log: Path,
    context: ExecutorContext,
) -> dict[str, object]:
    if compiled_module(__file__):
        message = "Schematic normalizer is private development tooling and is unavailable in this runtime"
        log.write_text(message + "\n", encoding="utf-8")
        return {"command": [], "returncode": None, "timed_out": False,
                "status": "BLOCKED_ENVIRONMENT", "code": "normalizer_unavailable",
                "reason": message}
    script = (
        product_root().parent
        / "ai"
        / "skills"
        / "schematic-to-rnm"
        / "scripts"
        / "normalize_manifest.py"
    )
    command = (
        production_python(),
        str(script),
        "--schematic-json",
        str(schematic),
        "--symbol-json",
        str(symbol),
        "--output",
        str(output),
        "--manifest-output",
        str(manifest),
    )
    environment = {
        name: value
        for name, value in (dict(context.environment) or dict(os.environ)).items()
        if not name.startswith(("SICO_AI_", "CAD_AI_", "CAD_CODEX_"))
    }
    try:
        completed = subprocess.run(
            command,
            cwd=context.gate_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=context.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output_text = exc.stdout if isinstance(exc.stdout, str) else ""
        log.write_text(output_text, encoding="utf-8")
        return {"command": list(command), "returncode": None, "timed_out": True, "status": "BLOCKED_TIMEOUT"}
    log.write_text(completed.stdout, encoding="utf-8")
    return {
        "command": list(command),
        "returncode": completed.returncode,
        "timed_out": False,
        "status": "PASS" if completed.returncode == 0 else "FAIL_NORMALIZER",
    }


def _blocked(
    context: ExecutorContext,
    status: str,
    reason: str,
    *,
    code: str = "incomplete_snapshot",
    artifacts: Sequence[Path] = (),
    extra: Mapping[str, Any] | None = None,
) -> ExecutorResult:
    evidence = context.gate_root / "snapshot-evidence.json"
    write_json_once(
        evidence,
        {
            "schema_version": 1,
            "status": status,
            "code": code,
            "reason": reason,
            "authority": "authenticated-cdns-ipc-read-only",
            **dict(extra or {}),
        },
    )
    return ExecutorResult(
        status,
        {"code": code, "reason": reason, **dict(extra or {})},
        artifacts=(*artifacts, evidence),
    )
