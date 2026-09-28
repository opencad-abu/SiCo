"""Versioned configuration profiles shared by CAD flow frontends."""

from .model import (
    FORMAT_NAME,
    SCHEMA_VERSION,
    SUPPORTED_FLOWS,
    ProfileDocument,
    ProfileError,
    load_profile,
)

__all__ = [
    "FORMAT_NAME",
    "SCHEMA_VERSION",
    "SUPPORTED_FLOWS",
    "ProfileDocument",
    "ProfileError",
    "load_profile",
]

__version__ = "1.0"
