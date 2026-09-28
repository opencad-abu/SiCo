"""Runtime compatibility helpers for the production Python 3.9 tree."""

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.9 runtime
    import tomli as tomllib


__all__ = ["tomllib"]
