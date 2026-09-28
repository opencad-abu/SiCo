"""Errors raised by the MTS Netlistor core.

The public exception is deliberately independent from Cadence-specific
modules so that request validation and catalog fallback work on hosts that do
not have Virtuoso installed.
"""

from __future__ import annotations


class MtsNetlistorError(ValueError):
    """Base class for user-actionable validation and workflow failures."""


class RequestValidationError(MtsNetlistorError):
    """A request cannot be executed safely."""


class CatalogError(MtsNetlistorError):
    """A source or target catalog could not be resolved."""


class IsolationError(MtsNetlistorError):
    """A source/target process boundary could not be established."""


class SymbolTransferError(MtsNetlistorError):
    """The two-stage OA symbol transfer did not complete and verify."""
