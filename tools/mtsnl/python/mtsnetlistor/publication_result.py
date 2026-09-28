"""Immutable target preflight and publication result records."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .symbol import SymbolTransferResult


@dataclass(frozen=True)
class PublicationResult:
    status: str
    target_library: str
    target_cell: str
    target_view: str
    source_netlist: Path
    log_file: Path
    created_view: bool
    replaced_view: bool = False
    target_overlay: Path | None = None
    message: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status,
            "target_library": self.target_library,
            "target_cell": self.target_cell,
            "target_view": self.target_view,
            "source_netlist": str(self.source_netlist),
            "log_file": str(self.log_file),
            "created_view": self.created_view,
            "replaced_view": self.replaced_view,
            "target_overlay": None if self.target_overlay is None else str(self.target_overlay),
            "message": self.message,
        }


@dataclass(frozen=True)
class PublicationPreflight:
    """Immutable destination checks performed before any OA mutation."""

    target_library: str
    target_cell: str
    target_library_path: Path
    symbol_view_path: Path | None
    text_view_path: Path | None
    netlist_path: Path | None
    text_executable: str | None
    symbol_existed_before: bool
    text_existed_before: bool


@dataclass(frozen=True)
class PublicationBundleResult:
    """Result of publishing the optional symbol/text pair as one operation."""

    status: str
    target_library: str
    target_cell: str
    text: PublicationResult | None = None
    symbol: SymbolTransferResult | None = None
    affected_views: tuple[str, ...] = ()
    rollback: tuple[str, ...] = ()
    message: str = ""
    # ``manual_cleanup_required`` is intentionally conservative: an OA
    # publisher may have mutated a cellview before its wrapper can observe a
    # result.  Keep the evidence paths and operator instructions alongside
    # the status so a GUI/CLI caller does not have to reconstruct them from a
    # transient exception string.
    diagnostics: tuple[str, ...] = ()
    log_files: tuple[str, ...] = ()
    cleanup_instructions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": self.status,
            "target_library": self.target_library,
            "target_cell": self.target_cell,
            "text": None if self.text is None else self.text.to_dict(),
            "symbol": None if self.symbol is None else self.symbol.to_dict(),
            "affected_views": list(self.affected_views),
            "rollback": list(self.rollback),
            "message": self.message,
            "diagnostics": list(self.diagnostics),
            "log_files": list(self.log_files),
            "cleanup_instructions": list(self.cleanup_instructions),
        }
