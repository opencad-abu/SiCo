"""Two-stage OA symbol transfer across the source/target process boundary.

The source and target domains are intentionally handled by separate
``dbAccess`` processes.  The Python layer only renders validated SKILL,
starts each process group, and verifies the line-oriented reports; it never
opens or copies OA files itself.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
from threading import Event
from typing import Mapping, Optional

from .artifacts import atomic_write_text, sha256_file
from .environment import SessionDescriptor, isolated_environment, write_cds_lib_overlay
from .errors import IsolationError, RequestValidationError, SymbolTransferError
from .model import NetlistRequest
from .process import ProcessResult, run_isolated


_REPORT_FIELDS = frozenset({
    "stage",
    "pid",
    "cds_lib",
    "source",
    "transfer",
    "target",
    "source_terminals",
    "transfer_terminals",
    "target_terminals",
    "overwrite",
    "target_existed",
    "copy_result",
})
_SAFE_TRANSFER_PREFIX = "MTS_XFER_"


def _skill_string(value: str) -> str:
    if any(ord(char) < 32 for char in value):
        raise RequestValidationError("symbol transfer SKILL value contains a control character")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _skill_name(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", value):
        raise RequestValidationError(f"invalid {label}: {value!r}")
    return _skill_string(value)


def _render_source_script() -> str:
    from cadcontext import worker_call
    return worker_call("mtsRuntimeSourceSymbol") + "exit()\n"


def _render_target_script() -> str:
    from cadcontext import worker_call
    return worker_call("mtsRuntimeTargetSymbol") + "exit()\n"


@dataclass(frozen=True)
class SymbolTransferResult:
    status: str
    source_library: str
    source_cell: str
    target_library: str
    target_cell: str
    transfer_library: str
    transfer_path: Path
    source_report: Path
    target_report: Path
    source_process: ProcessResult
    target_process: ProcessResult
    source_overlay: Path
    target_overlay: Path
    terminal_names: tuple[str, ...]
    target_existed_before: bool = False
    overwrote_existing: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status,
            "source_library": self.source_library,
            "source_cell": self.source_cell,
            "target_library": self.target_library,
            "target_cell": self.target_cell,
            "transfer_library": self.transfer_library,
            "transfer_path": str(self.transfer_path),
            "source_report": str(self.source_report),
            "target_report": str(self.target_report),
            "source_process": {
                "argv": list(self.source_process.argv),
                "pid": self.source_process.pid,
                "ppid": self.source_process.ppid,
                "pgid": self.source_process.pgid,
                "returncode": self.source_process.returncode,
                "environment_digest": self.source_process.environment_digest,
                "cwd": self.source_process.cwd,
            },
            "target_process": {
                "argv": list(self.target_process.argv),
                "pid": self.target_process.pid,
                "ppid": self.target_process.ppid,
                "pgid": self.target_process.pgid,
                "returncode": self.target_process.returncode,
                "environment_digest": self.target_process.environment_digest,
                "cwd": self.target_process.cwd,
            },
            "source_overlay": str(self.source_overlay),
            "target_overlay": str(self.target_overlay),
            "terminal_names": list(self.terminal_names),
            "target_existed_before": self.target_existed_before,
            "overwrote_existing": self.overwrote_existing,
        }


def _parse_report(path: Path) -> dict[str, str]:
    if not path.is_file() or path.stat().st_size == 0:
        raise SymbolTransferError(f"symbol transfer report is missing or empty: {path}")
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip() or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in _REPORT_FIELDS or key in values:
            raise SymbolTransferError(f"invalid symbol transfer report field: {key}")
        values[key] = value.strip()
    return values


def _report_terminals(value: str, label: str) -> tuple[str, ...]:
    # SKILL %L emits strings, not SKILL identifiers. OA terminals can contain
    # bus ranges/bits, global suffixes and other punctuation. Validate only
    # the report grammar; keep the exact OA names for copy-integrity checks.
    value = value.strip()
    if value == "nil":
        return ()
    quoted = r'"(?:\\["\\]|[^"\\\x00-\x1f\x7f])*"'
    if not re.fullmatch(rf'\(\s*(?:{quoted}\s*)*\)', value):
        raise SymbolTransferError(f"{label} has invalid terminal list")
    # Decode the string serialization once. Backslashes belonging to an OA
    # name (including escaped bus brackets) must survive this decoding.
    names = tuple(
        re.sub(r'\\(["\\])', r'\1', token[1:-1])
        for token in re.findall(quoted, value)
    )
    if any(not name for name in names):
        raise SymbolTransferError(f"{label} has invalid terminal names")
    if len(set(names)) != len(names):
        raise SymbolTransferError(f"{label} contains duplicate terminals")
    return names


def _require_report(values: Mapping[str, str], stage: str) -> None:
    required = {
        "stage",
        "cds_lib",
        "transfer",
        "transfer_terminals",
        "copy_result",
    }
    if stage == "source":
        required.add("source")
        required.add("source_terminals")
    else:
        required.add("target")
        required.add("target_terminals")
        required.add("overwrite")
        required.add("target_existed")
    missing = sorted(item for item in required if not values.get(item, "").strip())
    if missing:
        raise SymbolTransferError(
            f"{stage} symbol transfer report is missing: {', '.join(missing)}"
        )


def _ensure_process(result: ProcessResult, stage: str) -> None:
    if result.canceled:
        raise SymbolTransferError(f"{stage} symbol transfer canceled")
    if result.timed_out:
        raise SymbolTransferError(f"{stage} symbol transfer timed out")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise SymbolTransferError(f"{stage} symbol transfer failed{suffix}")


def transfer_symbol(
    request: NetlistRequest,
    session: SessionDescriptor,
    *,
    run_dir: str | Path,
    dbaccess: str = "dbAccess",
    timeout: float = 120.0,
    cancel_event: Optional[Event] = None,
    environment: Optional[Mapping[str, str]] = None,
    source_environment: Optional[Mapping[str, str]] = None,
    target_environment: Optional[Mapping[str, str]] = None,
) -> SymbolTransferResult:
    """Copy source ``symbol`` through a neutral transfer library.

    Source and target ``cds.lib`` files are passed in separate process
    environments.  The target process never receives source selectors or
    source paths.  Existing views are rejected by default; an explicit
    per-view request is passed to the documented ``dbCopyCellView`` overwrite
    argument in the target-only worker.
    """

    value = request.validate()
    validated_session = session.validate()
    if not value.target.generate_symbol_view:
        raise RequestValidationError("generate_symbol_view is not enabled")
    source_lib = value.source.library
    source_cell = value.source.cell
    target_lib = value.target.library
    target_cell = value.target.cell or source_cell
    if not target_lib:
        raise RequestValidationError("target library is required for symbol publication")
    if target_lib not in validated_session.target_library_paths:
        raise RequestValidationError(f"target library is not part of the current session: {target_lib}")
    if source_lib == target_lib and target_cell == source_cell:
        raise IsolationError("source and target symbol cell are identical; refusing self-publication")
    target_path = Path(validated_session.target_library_paths[target_lib]).expanduser().resolve()
    if not target_path.is_dir() or not os.access(target_path, os.W_OK):
        raise RequestValidationError(f"target library path is unavailable or read-only: {target_path}")
    target_view = target_path / target_cell / "symbol"
    target_existed_before = target_view.exists()
    if target_existed_before and not value.target.overwrite_symbol_view:
        raise RequestValidationError(
            f"target symbol view already exists (overwrite policy is reject): {target_lib}/{target_cell}/symbol"
        )

    root = Path(run_dir).expanduser().resolve()
    if not root.exists():
        root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    transfer_path = root / "transfer" / "oa"
    transfer_path.mkdir(parents=True, exist_ok=True)
    digest = sha256_file(value.source.cds_lib)[:16]
    transfer_library = f"{_SAFE_TRANSFER_PREFIX}{digest}_{os.getpid()}"
    transfer_cell = source_cell
    transfer_view = transfer_path / transfer_cell / "symbol"
    if transfer_view.exists():
        raise SymbolTransferError(f"transfer symbol destination already exists: {transfer_view}")
    source_overlay = write_cds_lib_overlay(
        value.source.cds_lib,
        root / "transfer" / "source-overlay.cds.lib",
        define=(transfer_library, transfer_path),
        forbidden_paths=(validated_session.target_cds_lib, target_path),
    )
    target_overlay = write_cds_lib_overlay(
        validated_session.target_cds_lib,
        root / "transfer" / "target-overlay.cds.lib",
        define=(transfer_library, transfer_path),
        forbidden_paths=(value.source.cds_lib,),
    )
    source_script = root / "transfer" / "source-symbol.il"
    target_script = root / "transfer" / "target-symbol.il"
    atomic_write_text(source_script, _render_source_script())
    atomic_write_text(target_script, _render_target_script())
    source_report = root / "transfer" / "source-symbol.report"
    target_report = root / "transfer" / "target-symbol.report"
    # ``environment`` is retained as a compatibility alias for direct callers.
    # Product code supplies only ``source_environment``; target publication
    # must remain in the host Virtuoso environment domain.
    source_base = environment if source_environment is None else source_environment
    target_base = environment if target_environment is None else target_environment
    source_child_environment = isolated_environment(
        source_base,
        cds_lib=source_overlay,
        workdir=root / "transfer",
        forbidden_paths=(validated_session.target_cds_lib, target_path),
        extra={
            "MTS_SOURCE_LIB": source_lib,
            "MTS_SOURCE_CELL": source_cell,
            "MTS_TRANSFER_LIB": transfer_library,
            "MTS_TRANSFER_CELL": transfer_cell,
            "MTS_TRANSFER_ROOT": str(transfer_path),
            "MTS_TRANSFER_REPORT": str(source_report),
        },
    )
    target_child_environment = isolated_environment(
        target_base,
        cds_lib=target_overlay,
        workdir=root / "transfer",
        forbidden_paths=(value.source.cds_lib,),
        extra={
            "MTS_TARGET_LIB": target_lib,
            "MTS_TARGET_CELL": target_cell,
            "MTS_TARGET_ROOT": str(target_path),
            "MTS_TRANSFER_LIB": transfer_library,
            "MTS_TRANSFER_CELL": transfer_cell,
            "MTS_TRANSFER_ROOT": str(transfer_path),
            "MTS_TRANSFER_REPORT": str(target_report),
            "MTS_TARGET_OVERWRITE": (
                "1" if value.target.overwrite_symbol_view else "0"
            ),
        },
    )
    source_executable = (
        shutil.which(dbaccess, path=source_child_environment.get("PATH"))
        if Path(dbaccess).parent == Path(".")
        else str(Path(dbaccess).expanduser().resolve())
    )
    target_executable = (
        shutil.which(dbaccess, path=target_child_environment.get("PATH"))
        if Path(dbaccess).parent == Path(".")
        else str(Path(dbaccess).expanduser().resolve())
    )
    if not source_executable or not Path(source_executable).is_file() or not os.access(source_executable, os.X_OK):
        raise RequestValidationError(f"dbAccess executable is not runnable: {dbaccess}")
    if not target_executable or not Path(target_executable).is_file() or not os.access(target_executable, os.X_OK):
        raise RequestValidationError(f"target dbAccess executable is not runnable: {dbaccess}")
    source_command = [source_executable, "-cdslib", str(source_overlay), "-load", str(source_script)]
    target_command = [target_executable, "-cdslib", str(target_overlay), "-load", str(target_script)]
    source_result = run_isolated(
        source_command,
        cwd=root / "transfer",
        environment=source_child_environment,
        timeout=timeout,
        cancel=cancel_event,
        log_file=root / "logs" / "source-symbol-worker.log",
    )
    _ensure_process(source_result, "source")
    source_values = _parse_report(source_report)
    _require_report(source_values, "source")
    if source_values.get("stage") != "source" or source_values.get("copy_result") != "t":
        raise SymbolTransferError("source symbol transfer report did not confirm copy")
    source_terms = _report_terminals(source_values.get("source_terminals", ""), "source terminals")
    transfer_terms = _report_terminals(source_values.get("transfer_terminals", ""), "transfer terminals")
    if source_terms != transfer_terms:
        raise SymbolTransferError("source and transfer symbol terminals differ")
    if not transfer_view.exists():
        raise SymbolTransferError(f"source worker did not create transfer symbol: {transfer_view}")
    target_result = run_isolated(
        target_command,
        cwd=root / "transfer",
        environment=target_child_environment,
        timeout=timeout,
        cancel=cancel_event,
        log_file=root / "logs" / "target-symbol-worker.log",
    )
    _ensure_process(target_result, "target")
    target_values = _parse_report(target_report)
    _require_report(target_values, "target")
    if target_values.get("stage") != "target" or target_values.get("copy_result") != "t":
        raise SymbolTransferError("target symbol transfer report did not confirm copy")
    expected_overwrite = "t" if value.target.overwrite_symbol_view else "nil"
    if target_values.get("overwrite") != expected_overwrite:
        raise SymbolTransferError("target symbol transfer report has an overwrite mismatch")
    target_existed_value = target_values.get("target_existed")
    if target_existed_value not in {"t", "nil"}:
        raise SymbolTransferError("target symbol transfer report has invalid existence evidence")
    target_existed = target_existed_value == "t"
    target_transfer_terms = _report_terminals(target_values.get("transfer_terminals", ""), "target transfer terminals")
    target_terms = _report_terminals(target_values.get("target_terminals", ""), "target terminals")
    if target_transfer_terms != source_terms or target_terms != source_terms:
        raise SymbolTransferError("target symbol terminals differ from source terminals")
    if not target_view.is_dir():
        raise SymbolTransferError(f"target worker did not create symbol view: {target_view}")
    if source_result.pid == target_result.pid:
        raise IsolationError("source and target symbol workers reused the same PID")
    return SymbolTransferResult(
        "succeeded",
        source_lib,
        source_cell,
        target_lib,
        target_cell,
        transfer_library,
        transfer_path,
        source_report,
        target_report,
        source_result,
        target_result,
        source_overlay,
        target_overlay,
        source_terms,
        target_existed,
        value.target.overwrite_symbol_view and target_existed,
    )
