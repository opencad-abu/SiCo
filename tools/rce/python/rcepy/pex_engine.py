"""Calibre parasitic-extraction engine (xRC/xACT) identity and invocation facts.

One RCE request selects one Calibre PEX engine.  The engine owns everything
that differs between Calibre xRC and Calibre xACT for the shared PHDB/PDB
three-stage flow: the ``calibre`` subcommand, the SVDB query keyword, the
parasitic-model switches, and which RC types the engine can extract.

Evidence (Calibre xRC/xACT User's Manuals and the Calibre command line):

* ``calibre -xrc -pdb <mode>`` extracts the PDB for xRC, while xACT needs
  ``calibre -xact -3d -pdb <mode>`` (R-only uses the standalone
  ``calibre -xact -r``).
* ``MASK SVDB DIRECTORY`` accepts ``XRC`` or ``XACT``, mutually exclusive, so
  the generated control file must name the selected engine.
* xACT has no capacitance-only PDB mode; capacitance-only output is produced
  from an ``-rc`` PDB by formatting with ``-c``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class PexMode:
    """One parasitic model of one Calibre PEX engine.

    ``pdb_arguments`` is the complete engine argument list that creates the
    PDB, or ``None`` for the parasitic-free (simple netlist) mode.  The
    formatter switches are passed after ``<engine> -fmt``.
    """

    key: str
    pdb_arguments: tuple[str, ...] | None
    formatter_switches: tuple[str, ...]
    capacitance_only: bool

    @property
    def simple_netlist(self) -> bool:
        return self.pdb_arguments is None


@dataclass(frozen=True)
class PexEngine:
    """One Calibre PEX engine and the argument shapes RCE runs it with."""

    key: str
    summary: str
    formatter: tuple[str, ...]
    svdb_query_keyword: str
    modes: Mapping[str, PexMode]

    @property
    def label(self) -> str:
        return f"Calibre {self.summary}"

    def summary_errors(self, text: str) -> int | None:
        """Return the error count of the engine transcript summary, if present."""
        match = re.search(
            rf"\b{self.summary} Errors\s*=\s*(\d+)", text, re.IGNORECASE
        )
        return int(match.group(1)) if match else None

    def mode(self, rc_type: str) -> PexMode:
        """Return the parasitic model for one normalized RC type."""
        try:
            return self.modes[rc_type]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported {self.label} RC type: {rc_type}"
            ) from exc

    def selection_argument(self, mode: PexMode) -> str | None:
        """Return the net-selection switch, or ``None`` without a PDB stage.

        ``-cselect`` is an xRC PDB switch for capacitive and R-coupled-C
        extraction; the xACT PDB flow takes ``-select`` instead.
        """
        if mode.pdb_arguments is None:
            return None
        if self.key == "xrc" and mode.key in {"RCC", "C", "CG"}:
            return "-cselect"
        return "-select"


def _mode(
    key: str,
    pdb_arguments: tuple[str, ...] | None,
    formatter_switches: tuple[str, ...],
    *,
    capacitance_only: bool = False,
) -> PexMode:
    return PexMode(key, pdb_arguments, formatter_switches, capacitance_only)


XRC_ENGINE = PexEngine(
    key="xrc",
    summary="xRC",
    formatter=("-xrc", "-fmt"),
    svdb_query_keyword="XRC",
    modes=MappingProxyType(
        {
            "RCC": _mode("RCC", ("-xrc", "-pdb", "-rcc"), ("-all",)),
            "RC": _mode("RC", ("-xrc", "-pdb", "-rc"), ("-all",)),
            "R": _mode("R", ("-xrc", "-pdb", "-r"), ("-r",)),
            "C": _mode(
                "C", ("-xrc", "-pdb", "-c"), ("-c",), capacitance_only=True
            ),
            "CG": _mode(
                "CG",
                ("-xrc", "-pdb", "-c"),
                ("-c", "-g"),
                capacitance_only=True,
            ),
            "NONE": _mode("NONE", None, ("-simple",)),
        }
    ),
)

XACT_ENGINE = PexEngine(
    key="xact",
    summary="xACT",
    formatter=("-xact", "-fmt"),
    svdb_query_keyword="XACT",
    modes=MappingProxyType(
        {
            "RCC": _mode("RCC", ("-xact", "-3d", "-pdb", "-rcc"), ("-all",)),
            "RC": _mode("RC", ("-xact", "-3d", "-pdb", "-rc"), ("-all",)),
            "R": _mode("R", ("-xact", "-r"), ("-r",)),
            # xACT has no capacitance-only PDB mode: extract the distributed
            # RC model, then write capacitance models only.  ``-g`` grounds
            # the coupling capacitors for Cg output.
            "C": _mode(
                "C",
                ("-xact", "-3d", "-pdb", "-rc"),
                ("-c",),
                capacitance_only=True,
            ),
            "CG": _mode(
                "CG",
                ("-xact", "-3d", "-pdb", "-rc"),
                ("-c", "-g"),
                capacitance_only=True,
            ),
            "NONE": _mode("NONE", None, ("-simple",)),
        }
    ),
)

_ENGINES = MappingProxyType({"xrc": XRC_ENGINE, "xact": XACT_ENGINE})

# Accepted ``extract.tool`` spellings.  "XRC" is the pre-xACT RCE spelling.
TOOL_ENGINES = MappingProxyType(
    {
        "CALXRC": "xrc",
        "XRC": "xrc",
        "CALIBRE-XRC": "xrc",
        "CALIBRE_XRC": "xrc",
        "CALXACT": "xact",
        "CALIBRE-XACT": "xact",
        "CALIBRE_XACT": "xact",
    }
)

# ``extract.rc_type`` spellings of the RCE form and of hand-written TOML.
RC_TYPES = MappingProxyType(
    {
        "RCC": "RCC",
        "R+CG+CC": "RCC",
        "RC": "RC",
        "R+C": "RC",
        "R+CG": "RC",
        "R": "R",
        "C": "C",
        "CC": "C",
        "CG+CC": "C",
        "CG": "CG",
        "NONE": "NONE",
        "NO-RC": "NONE",
        "NORC": "NONE",
    }
)


def calibre_pex_engine(tool: str) -> PexEngine | None:
    """Return the Calibre PEX engine for a tool name, or ``None``."""
    key = TOOL_ENGINES.get(tool.strip().upper())
    return _ENGINES[key] if key else None


def require_calibre_pex_engine(tool: str) -> PexEngine:
    engine = calibre_pex_engine(tool)
    if engine is None:
        raise ValueError(f"Unsupported extraction tool for Calibre PEX: {tool}")
    return engine


def calibre_pex_tool(tool: str) -> bool:
    """Return whether ``tool`` selects a Calibre PEX engine."""
    return calibre_pex_engine(tool) is not None


def normalize_rc_type(rc_type: str) -> str:
    """Return the canonical parasitic model key for an RCE RC type."""
    key = RC_TYPES.get(rc_type.strip().upper().replace(" ", ""))
    if key is None:
        raise ValueError(f"Unsupported Calibre PEX RC type: {rc_type}")
    return key
