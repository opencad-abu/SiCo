"""Runtime compatibility helpers for the shared batch controller."""

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.9 runtime
    import tomli as tomllib


__all__ = ["tomllib"]
