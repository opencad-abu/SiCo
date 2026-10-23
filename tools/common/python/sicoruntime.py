"""Validate the selected Python runtime and its TLS trust store."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_verify_paths():
    """Return the TLS trust locations without importing ``ssl`` at module load."""
    import ssl

    return ssl.get_default_verify_paths()


def report(environment=None):
    """Describe the current interpreter and TLS trust without raising."""
    environment = os.environ if environment is None else environment
    result = {
        "python": os.path.realpath(sys.executable),
        "version": ".".join(str(item) for item in sys.version_info[:3]),
        "openssl": "",
        "cafile": "",
        "capath": "",
        "home": str(environment.get("SICO_HOME", "")),
        "bundled": False,
        "ok": False,
        "errors": [],
    }
    try:
        import _ssl  # noqa: F401
        import ssl
    except Exception as exc:  # pragma: no cover - depends on the interpreter
        result["errors"].append("Python TLS support is unavailable: " + str(exc))
        return result
    result["openssl"] = str(ssl.OPENSSL_VERSION)
    paths = _default_verify_paths()
    result["cafile"] = str(paths.cafile or "")
    result["capath"] = str(paths.capath or "")
    home = result["home"].strip()
    if home:
        runtime_python = (Path(home) / "runtime/python").resolve()
        try:
            result["bundled"] = Path(result["python"]).is_relative_to(runtime_python)
        except (OSError, ValueError):
            result["bundled"] = False
    if not result["openssl"]:
        result["errors"].append("Python reports no OpenSSL version")
    if not (
        (result["cafile"] and Path(result["cafile"]).is_file())
        or (result["capath"] and Path(result["capath"]).is_dir())
    ):
        result["errors"].append(
            "No readable TLS CA file or directory is configured"
        )
    result["ok"] = not result["errors"]
    return result


def validate(environment=None):
    """Raise a bounded diagnostic when model HTTPS cannot verify certificates."""
    result = report(environment)
    if result["errors"]:
        raise RuntimeError(
            "SiCo Python TLS runtime is incomplete: "
            + "; ".join(result["errors"])
            + ". Python="
            + result["python"]
            + " SICO_HOME="
            + (result["home"] or "<unset>")
        )
    return result
