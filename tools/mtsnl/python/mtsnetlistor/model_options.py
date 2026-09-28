"""Typed simulator/process options and their qualified dialect constraints."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional, Sequence
from .errors import RequestValidationError
from .model_validation import SIMULATORS, _finite_real


_OPTION_TYPES = frozenset({"boolean", "integer", "real", "enum", "string"})


_GMIN_RE = re.compile(
    r"^[+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)


HSPICED_UNQUALIFIED_PROCESS_OPTIONS = frozenset(
    {"tnom", "scalem", "reltol"}
)


HSPICED_UNQUALIFIED_SIMULATOR_OPTIONS = frozenset(
    {"tnom", "scalem", "reltol"}
)


@dataclass(frozen=True)
class SimulatorOption:
    """A typed simulator option; raw OCEAN/netlist fragments are forbidden."""

    name: str
    value: str
    value_type: str = "string"
    enabled: bool = True
    enum_values: tuple[str, ...] = ()

    def validate(self, dialect: str) -> "SimulatorOption":
        if dialect not in SIMULATORS:
            raise RequestValidationError(f"unsupported simulator dialect: {dialect!r}")
        name = str(self.name).strip()
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
            raise RequestValidationError(f"invalid simulator option name: {self.name!r}")
        value_type = str(self.value_type).strip().lower()
        if value_type not in _OPTION_TYPES:
            raise RequestValidationError(f"unsupported option value type: {value_type!r}")
        value = str(self.value)
        if any(ord(char) < 32 or char in "\r\n" for char in value):
            raise RequestValidationError(f"option {name!r} contains a control character")
        if value_type == "boolean" and value.lower() not in {"true", "false", "yes", "no", "1", "0"}:
            raise RequestValidationError(f"option {name!r} must be boolean")
        if value_type == "integer":
            try:
                int(value, 10)
            except ValueError as exc:
                raise RequestValidationError(f"option {name!r} must be integer") from exc
        if value_type == "real":
            _finite_real(value, f"option {name!r}")
        if value_type == "enum" and value not in self.enum_values:
            raise RequestValidationError(
                f"option {name!r} must be one of {self.enum_values!r}"
            )
        # A quoted value would need a dialect-specific escaping policy. Keep
        # advanced strings deliberately conservative in v1.
        if value_type == "string" and any(char in value for char in '"(){};'):
            raise RequestValidationError(f"option {name!r} contains unsafe punctuation")
        if dialect == "hspiceD" and name.casefold() == "parhier" and value.upper() != "LOCAL":
            raise RequestValidationError("hspiceD PARHIER is fixed to LOCAL for MTS scope")
        return SimulatorOption(name, value, value_type, bool(self.enabled), tuple(self.enum_values))


@dataclass(frozen=True)
class ProcessOptions:
    """Common process values rendered by the selected simulator adapter."""

    temp: Optional[float] = None
    tnom: Optional[float] = None
    scale: Optional[float] = None
    scalem: Optional[float] = None
    reltol: Optional[float] = None
    # ``gmin`` is a simulator tolerance option exposed as a first-class field
    # because PDK defaults commonly provide it through ASI.  Keep it last so
    # existing positional construction of the older five-field dataclass
    # remains source-compatible.
    gmin: Optional[str] = None

    def validate(self) -> "ProcessOptions":
        values: dict[str, Optional[float]] = {}
        for name in ("temp", "tnom"):
            raw = getattr(self, name)
            values[name] = None if raw is None else _finite_real(raw, name)
        for name in ("scale", "scalem", "reltol"):
            raw = getattr(self, name)
            values[name] = None if raw is None else _finite_real(raw, name, positive=True)
        raw_gmin = self.gmin
        if raw_gmin is None or str(raw_gmin).strip() == "":
            gmin = None
        else:
            text = str(raw_gmin).strip()
            if not _GMIN_RE.fullmatch(text):
                raise RequestValidationError(
                    "gmin must be an ASCII decimal or scientific-notation number"
                )
            _finite_real(text, "gmin", positive=True)
            gmin = text
        return ProcessOptions(**values, gmin=gmin)


def _validate_temperature(mode: str, process: "ProcessOptions", options: Sequence["SimulatorOption"], dialect: str) -> str:
    if mode not in {"fixed", "inherit"}:
        raise RequestValidationError("temperature_mode must be fixed or inherit")
    if mode == "inherit":
        if dialect != "spectre":
            raise RequestValidationError("inherited temperature requires Spectre")
        if process.temp is not None or any(o.enabled and o.name.casefold() == "temp" for o in options):
            raise RequestValidationError("inherited temperature cannot also specify a local temp")
    return mode


def _validate_process_options(process_options: ProcessOptions, dialect: str) -> ProcessOptions:
    value = process_options.validate()
    if dialect == "hspiceD":
        unsupported_process = tuple(
            name
            for name in HSPICED_UNQUALIFIED_PROCESS_OPTIONS
            if getattr(value, name) is not None
        )
        if unsupported_process:
            names = ", ".join(sorted(unsupported_process))
            raise RequestValidationError(
                "hspiceD process option(s) are not qualified for OCEAN "
                f"netlisting: {names}; clear them or use a qualified mapping"
            )
    return value


def _validate_simulator_options(
    options: Sequence[SimulatorOption], dialect: str
) -> tuple[SimulatorOption, ...]:
    values = tuple(option.validate(dialect) for option in options)
    reserved_gmin = tuple(
        option.name
        for option in values
        if option.enabled and option.name.casefold() == "gmin"
    )
    if reserved_gmin:
        raise RequestValidationError(
            "gmin is reserved for the dedicated process option; "
            "remove it from Advanced Options"
        )
    if dialect == "hspiceD":
        unsupported_simulator = tuple(
            option.name
            for option in values
            if option.enabled and option.name.casefold() in HSPICED_UNQUALIFIED_SIMULATOR_OPTIONS
        )
        if unsupported_simulator:
            names = ", ".join(sorted(set(name.casefold() for name in unsupported_simulator)))
            raise RequestValidationError(
                "hspiceD simulator option(s) are not qualified for OCEAN "
                f"netlisting: {names}; clear them or use a qualified mapping"
            )
    names: set[str] = set()
    for option in values:
        if option.enabled and option.name.casefold() in names:
            raise RequestValidationError(f"duplicate enabled simulator option: {option.name}")
        names.add(option.name.casefold())
    return values
