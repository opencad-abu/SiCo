"""Ordered model files and complete corner-library bundles."""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from pathlib import Path
from typing import Sequence
from .errors import RequestValidationError
from .model_validation import _path, validate_oa_name, MTS_DESIGN_CIRCUIT_SECTION


@dataclass(frozen=True)
class ModelEntry:
    """One ordered, user-editable model file entry."""

    file: Path
    section: str = ""
    label: str = ""
    enabled: bool = True

    def validate(self) -> "ModelEntry":
        path = _path(self.file, "model file", file=True)
        if not os.access(path, os.R_OK):
            raise RequestValidationError(f"model file is not readable: {path}")
        section = str(self.section)
        label = str(self.label)
        if any(ord(char) < 32 or char in "\r\n" for char in section):
            raise RequestValidationError("model section cannot contain control characters")
        if any(ord(char) < 32 or char in "\r\n" for char in label):
            raise RequestValidationError("model label cannot contain control characters")
        return ModelEntry(path, section, label, bool(self.enabled))


@dataclass(frozen=True)
class CornerProfile:
    """A complete ordered model bundle, never a section-name substitution."""

    name: str
    models: tuple[ModelEntry, ...] = ()

    def validate(self) -> "CornerProfile":
        name = validate_oa_name(self.name, "corner section")
        if name == MTS_DESIGN_CIRCUIT_SECTION:
            raise RequestValidationError(f"corner section {name!r} is reserved for the design circuit")
        models = _validate_models(self.models)
        if not any(entry.enabled for entry in models):
            raise RequestValidationError(f"corner {name!r} requires enabled model entries")
        for entry in models:
            if entry.enabled and entry.section and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$.-]*", entry.section):
                raise RequestValidationError(f"corner {name!r} requires literal model sections")
        return CornerProfile(name, models)


@dataclass(frozen=True)
class CornerExport:
    mode: str = "fixed"
    variable: str = "mts_corner"
    profiles: tuple[CornerProfile, ...] = ()

    def validate(self, dialect: str) -> "CornerExport":
        if self.mode not in {"fixed", "library"}:
            raise RequestValidationError("corner export mode must be fixed or library")
        variable = validate_oa_name(self.variable, "ADE corner variable")
        profiles = tuple(profile.validate() for profile in self.profiles)
        names = [profile.name for profile in profiles]
        if len(set(names)) != len(names):
            raise RequestValidationError("duplicate corner section names")
        if self.mode == "library":
            if dialect != "spectre":
                raise RequestValidationError("corner libraries require Spectre")
            if not profiles:
                raise RequestValidationError("corner library requires at least one corner profile")
        return CornerExport(self.mode, variable, profiles)


def _validate_models(models: Sequence[ModelEntry]) -> tuple[ModelEntry, ...]:
    entries = tuple(entry.validate() for entry in models)
    keys: set[tuple[Path, str]] = set()
    for entry in entries:
        key = (entry.file, entry.section)
        if key in keys:
            raise RequestValidationError(
                f"duplicate model file + section: {entry.file} [{entry.section}]"
            )
        keys.add(key)
    return entries
