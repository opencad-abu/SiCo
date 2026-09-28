"""Select the existing stdlib/external/vendored TOML parser in order."""

try:
    import tomllib
    _TOML_BACKEND = "stdlib"
except ModuleNotFoundError:  # pragma: no cover - Python 3.9 compatibility path
    try:
        import tomli as tomllib  # type: ignore[no-redef]
        _TOML_BACKEND = "external"
    except ModuleNotFoundError:
        # The command wrapper deliberately disables user-site packages. Keep a
        # pure-Python parser with the application so Python 3.9 installations
        # remain self-contained and deterministic.
        from ._vendor import tomli as tomllib  # type: ignore[no-redef]

        _TOML_BACKEND = "vendored"


__all__ = ["tomllib"]
