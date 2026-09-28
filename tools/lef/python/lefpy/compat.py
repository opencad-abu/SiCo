"""Python 3.9 compatibility imports."""

try:
    import tomllib
except ImportError:  # pragma: no cover - production Python 3.9
    import tomli as tomllib


__all__ = ["tomllib"]
