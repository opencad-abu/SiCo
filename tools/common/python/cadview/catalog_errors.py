"""Errors raised by catalog providers."""

from .errors import Nl2ViewError


class CatalogError(Nl2ViewError):
    """Raised when a catalog provider cannot produce a valid catalog."""


class CatalogCancelled(CatalogError):
    """Raised when a catalog request is cancelled by its caller."""


class CatalogTimeout(CatalogError):
    """Raised when a catalog provider exceeds its time limit."""
