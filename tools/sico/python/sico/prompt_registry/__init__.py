"""Runtime system-prompt fragments and assembly.

Single source of truth for all INSTRUCTIONS constants. Callers use
``assemble(role)`` rather than importing individual fragments; old
per-module INSTRUCTIONS exports are re-exported from here for
backward compatibility and are marked as legacy.
"""

from .registry import assemble

__all__ = ["assemble"]
