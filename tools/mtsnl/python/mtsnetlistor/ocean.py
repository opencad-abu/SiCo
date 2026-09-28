"""Validated OCEAN script rendering and isolated raw-netlist discovery."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Iterable, Optional, Sequence

from .errors import RequestValidationError
from .model import ModelEntry, NetlistRequest, SimulatorOption


def _skill_string(value: str) -> str:
    """Quote a path/string as a SKILL string literal."""

    if any(ord(character) < 32 for character in value):
        raise RequestValidationError("OCEAN string contains a control character")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _skill_value(option: SimulatorOption) -> str:
    value_type = option.value_type.casefold()
    if value_type == "boolean":
        return "t" if option.value.casefold() in {"true", "yes", "1"} else "nil"
    if value_type in {"integer", "real"}:
        return option.value
    return _skill_string(option.value)


def _skill_symbol(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        raise RequestValidationError(f"invalid OCEAN option name: {name!r}")
    return "'" + name


def _pair(entry: ModelEntry) -> str:
    path = _skill_string(str(entry.file))
    if entry.section:
        return f"({path} {_skill_string(entry.section)})"
    return path


def _option_pairs(request: NetlistRequest) -> list[str]:
    pairs: list[str] = []
    process = request.process_options
    # OCEAN's ``option`` table is simulator/dialect specific.  The common
    # process values are emitted only where the current netlister accepts
    # them. Unsupported requested values are rejected below so they can never
    # disappear between the resolved request and the generated raw deck.
    names = ("tnom", "scale", "scalem", "reltol", "gmin")
    if request.dialect == "hspiceD":
        # The IC23.10 hspiceD adapter is qualified for ``scale`` and ``gmin``.
        # Silently dropping a value entered in the GUI is unsafe: the
        # resulting deck would not represent the request the user approved.
        unsupported = {
            name
            for name in ("tnom", "scalem", "reltol")
            if getattr(process, name) is not None
        }
        unsupported.update(
            option.name.casefold()
            for option in request.simulator_options
            if option.enabled
            and option.name.casefold() in {"tnom", "scalem", "reltol"}
        )
        if unsupported:
            names_text = ", ".join(sorted(unsupported))
            raise RequestValidationError(
                "hspiceD option(s) are not qualified for OCEAN netlisting and "
                f"must be cleared or mapped explicitly: {names_text}"
            )
        names = ("scale", "gmin")
    for name in names:
        value = getattr(process, name)
        if value is not None:
            # Preserve the user's gmin spelling so a non-default value is
            # auditable in the OCEAN script; the hspiceD netlister emits the
            # canonical ``GMIN=...`` deck token.  It intentionally elides
            # values equal to its built-in default (1e-12).
            rendered = value if name == "gmin" else repr(float(value))
            pairs.extend((_skill_symbol(name), rendered))
    for option in request.simulator_options:
        if option.enabled:
            pairs.extend((_skill_symbol(option.name), _skill_value(option)))
    return pairs


def _render_model_file_call(request: NetlistRequest) -> str:
    entries = [entry for entry in request.models if entry.enabled]
    if not entries:
        return ""
    return "modelFile(\n    " + "\n    ".join(
        (f"'{_pair(entry)}" if entry.section else _pair(entry)) for entry in entries
    ) + "\n)"


@dataclass(frozen=True)
class OceanScript:
    """Rendered OCEAN source and expected output dialect."""

    text: str
    dialect: str
    expected_filename: str


def render_ocean_script(
    request: NetlistRequest,
    *,
    workdir: str | Path,
    result_dir: Optional[str | Path] = None,
) -> OceanScript:
    """Render a source worker script from a validated request.

    The script deliberately does not call ``run()``.  IC23.10's hspiceD
    integration rejects ``createFinalNetlist`` (ADE-3030) even though its
    netlister can produce a final ``input.ckt`` through ``createNetlist``;
    this adapter is therefore selected for hspiceD and is covered by the
    site qualification probe.
    """

    value = request.validate()
    root = Path(workdir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    result = Path(result_dir or root).expanduser().resolve()
    result.mkdir(parents=True, exist_ok=True)
    private_netlist_dir = result / "netlist"
    # ``netlistDir`` is documented as taking an existing directory.  Current
    # IC23.10 adapters create it on demand, but creating it here keeps the
    # worker deterministic across Cadence releases and makes the isolation
    # boundary explicit before OCEAN starts.
    private_netlist_dir.mkdir(parents=True, exist_ok=True)
    simulator = value.dialect
    expected = "input.scs" if simulator == "spectre" else "input.ckt"
    lines = [
        f"envSetVal(\"asimenv.startup\" \"projectDir\" 'string {_skill_string(str(root))})",
        f"simulator('{simulator})",
        f"design({_skill_string(value.source.library)} {_skill_string(value.source.cell)} {_skill_string(value.source.view)} \"r\")",
        # ``resultsDir`` is an OCEAN command, not a registered asimenv
        # variable in current IC23.x.  Setting it after ``design`` keeps all
        # generated netlisting artifacts below the private worker root.
        f"resultsDir({_skill_string(str(result))})",
        "ddsRefresh(?cellview t ?cdf t)",
    ]
    # Keep the generated input and auxiliary netlisting files in a predictable
    # private directory.  Without this, Cadence may choose a design-dependent
    # directory outside the current MTS run and incremental state can leak
    # between source workers.  The command is supported by both adapters.
    lines.insert(4, f"netlistDir({_skill_string(str(private_netlist_dir))})")
    if value.source.startup_file is not None:
        lines.append(f"load({_skill_string(str(value.source.startup_file))})")
    if value.source.simrc is not None:
        lines.append(f"load({_skill_string(str(value.source.simrc))})")
    model_call = _render_model_file_call(value)
    if model_call:
        lines.append(model_call)
    lines.append("envOption('setTopLevelAsSubckt t)")
    process_temp = value.process_options.temp
    if process_temp is not None:
        lines.append(f"temp({process_temp!r})")
    options = _option_pairs(value)
    if options:
        lines.append("option(" + " ".join(options) + ")")
    # Reassert the destination after startup/simrc and PDK callbacks have
    # completed.  Those files are allowed to alter ADE environment state;
    # the final call makes the private run boundary authoritative at the point
    # where createNetlist snapshots its output location.
    lines.append(f"netlistDir({_skill_string(str(private_netlist_dir))})")
    lines.extend(
        [
            'printf("MTS_NETLISTOR_RESULT_BEGIN\\n")',
            "mtsNetlist = createNetlist(?recreateAll t ?display nil)",
            'if(mtsNetlist then printf("MTS_NETLISTOR_RAW=%s\\n" mtsNetlist) else printf("MTS_NETLISTOR_ERROR\\n"))',
            "exit()",
        ]
    )
    return OceanScript("\n".join(lines) + "\n", simulator, expected)


# ``ocean -log`` prefixes replay output with markers such as ``\\o `` while
# stdout mode emits the sentinel at column zero.  Match the protocol token
# anywhere on a transcript line, then apply the run-root/path checks below.
# Require an absolute/home-relative path immediately after the protocol
# marker.  The replay transcript also contains the source ``printf`` call
# itself (``...RAW=%s...``); accepting arbitrary text there would select that
# source line instead of the emitted path.
_RAW_SENTINEL = re.compile(r"(?:^|[\r\n])[^\r\n]*?MTS_NETLISTOR_RAW=((?:/|~[/\\])[^\r\n]+)")


def raw_path_from_output(stdout: str, *, run_root: str | Path) -> Path:
    """Extract and constrain the raw netlist path printed by the worker."""

    match = _RAW_SENTINEL.search(stdout)
    if match is None:
        raise RequestValidationError("OCEAN did not print a raw netlist sentinel")
    candidate = Path(match.group(1).strip()).expanduser().resolve()
    root = Path(run_root).expanduser().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RequestValidationError(
            f"OCEAN raw netlist escaped run root: {candidate} not below {root}"
        ) from exc
    if not candidate.is_file() or candidate.stat().st_size == 0:
        raise RequestValidationError(f"OCEAN raw netlist is missing or empty: {candidate}")
    return candidate
