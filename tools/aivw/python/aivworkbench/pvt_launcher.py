"""Code-owned physical PVT launcher registration."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable, Sequence

from .pvt_plan_models import PVTLaunchContext

_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9_.+-]+$")
_SAFE_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_SIMULATORS = {"spectre", "ams"}
_RAW_OUTPUTS = {"psf", "shm", "fsdb"}


class PVTAdapterError(ValueError):
    """Raised when a code-owned physical PVT launcher is malformed."""


CommandBuilder = Callable[["PVTLaunchContext"], Sequence[str]]


@dataclass(frozen=True)
class PhysicalPVTLauncher:
    """Code-owned launcher definition stored in a physical-PVT registry.

    ``command_builder`` is intentionally supplied by Python code rather than
    by a recipe.  It receives already validated absolute paths and must return
    an argv sequence suitable for ``subprocess`` with ``shell=False``.  The
    registry does not provide a default Spectre or AMS command because the
    exact launcher syntax and approved deck generation are project-specific.
    """

    name: str
    version: str
    simulator: str
    raw_output: str
    required_tools: tuple[str, ...]
    command_tool: str
    command_builder: CommandBuilder


class PhysicalPVTLauncherRegistry:
    """Explicit registry for physical-corner launchers.

    Keeping this registry separate from the generic executor registry makes a
    physical adapter opt-in.  ``builtin_registry()`` in the main kernel does
    not populate it, so merely declaring ``verification.physical_pvt`` can
    never start a simulator.
    """

    def __init__(self) -> None:
        self._launchers: dict[str, PhysicalPVTLauncher] = {}

    def register(self, launcher: PhysicalPVTLauncher) -> None:
        if not isinstance(launcher, PhysicalPVTLauncher):
            raise PVTAdapterError("physical PVT launcher must be a PhysicalPVTLauncher")
        if not isinstance(launcher.name, str) or not _SAFE_ID.fullmatch(launcher.name):
            raise PVTAdapterError("physical PVT launcher name is invalid")
        if (
            not isinstance(launcher.version, str)
            or not _SAFE_VERSION.fullmatch(launcher.version)
        ):
            raise PVTAdapterError("physical PVT launcher version must be non-empty")
        if not isinstance(launcher.simulator, str) or launcher.simulator not in _SIMULATORS:
            raise PVTAdapterError("physical PVT launcher simulator is unsupported")
        if not isinstance(launcher.raw_output, str) or launcher.raw_output not in _RAW_OUTPUTS:
            raise PVTAdapterError("physical PVT launcher raw_output is unsupported")
        if isinstance(launcher.required_tools, (str, bytes)):
            raise PVTAdapterError(
                "physical PVT launcher required_tools must be an array"
            )
        try:
            required = tuple(launcher.required_tools)
        except TypeError as exc:
            raise PVTAdapterError(
                "physical PVT launcher required_tools must be an array"
            ) from exc
        if not required:
            raise PVTAdapterError("physical PVT launcher required_tools must be non-empty")
        if any(
            not isinstance(item, str) or not _SAFE_TOOL.fullmatch(item)
            for item in required
        ):
            raise PVTAdapterError("physical PVT launcher tool IDs are invalid")
        if len(required) != len(set(required)):
            raise PVTAdapterError("physical PVT launcher required_tools must be unique")
        expected = ("spectre",) if launcher.simulator == "spectre" else ("runams", "spectre")
        if not set(expected).issubset(required):
            raise PVTAdapterError(
                "physical PVT launcher required_tools omit simulator prerequisites"
            )
        if (
            not isinstance(launcher.command_tool, str)
            or launcher.command_tool not in required
        ):
            raise PVTAdapterError("physical PVT launcher command_tool is not required")
        if not callable(launcher.command_builder):
            raise PVTAdapterError("physical PVT launcher command_builder is not callable")
        if launcher.name in self._launchers:
            raise PVTAdapterError(f"duplicate physical PVT launcher: {launcher.name}")
        self._launchers[launcher.name] = launcher

    def get(self, name: object) -> PhysicalPVTLauncher | None:
        if not isinstance(name, str):
            return None
        return self._launchers.get(name)

    def require(self, name: str) -> PhysicalPVTLauncher:
        launcher = self.get(name)
        if launcher is None:
            raise PVTAdapterError(f"unknown physical PVT launcher: {name}")
        return launcher

    def describe(self) -> list[dict[str, object]]:
        return [
            {
                "name": launcher.name,
                "version": launcher.version,
                "simulator": launcher.simulator,
                "raw_output": launcher.raw_output,
                "required_tools": list(launcher.required_tools),
                "command_tool": launcher.command_tool,
                "executable": True,
            }
            for launcher in sorted(self._launchers.values(), key=lambda item: item.name)
        ]


def empty_physical_pvt_registry() -> PhysicalPVTLauncherRegistry:
    """Return the intentionally empty default registry."""

    return PhysicalPVTLauncherRegistry()
