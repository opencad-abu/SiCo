from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sicopaths import IDENTITY_BYTES

from cadai.launch import production_python


def _python_probe(path: Path, version: str = "3.9.13") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="ascii")
    path.chmod(0o755)
    return path


def _select_runtime(monkeypatch, python: Path | None) -> None:
    from cadai import launch_executable

    monkeypatch.setattr(
        launch_executable,
        "current",
        lambda environment=None: SimpleNamespace(runtime_python=python),
    )


def test_installation_runtime_ignores_stale_python_selectors(
        tmp_path: Path, monkeypatch) -> None:
    configured = _python_probe(tmp_path / "sico/runtime/python/bin/python3")
    _select_runtime(monkeypatch, configured)

    selected = production_python(
        {
            "SICO_PYTHON": str(tmp_path / "stale/bin/python3"),
            "SICO_PYTHON_ROOT": str(tmp_path / "stale-site-root"),
        }
    )

    assert selected == str(configured)


def test_installation_runtime_still_requires_qualified_version(
        tmp_path: Path, monkeypatch) -> None:
    configured = _python_probe(
        tmp_path / "sico/runtime/python/bin/python3", "3.9.12"
    )
    _select_runtime(monkeypatch, configured)

    with pytest.raises(RuntimeError, match="must be exactly 3.9.13"):
        production_python({})


def test_missing_runtime_fails_even_with_stale_selectors(monkeypatch) -> None:
    _select_runtime(monkeypatch, None)
    with pytest.raises(
        RuntimeError, match=r"\$SICO_HOME/runtime/python/bin/python3 is unavailable"
    ):
        production_python(
            {
                "SICO_PYTHON": "/stale/python3",
                "SICO_PYTHON_ROOT": "/stale/python",
            }
        )


def _installation(root: Path) -> Path:
    for relative in ("bin", "tools/common", "etc/config"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    (root / "etc/config/sico-install.json").write_bytes(IDENTITY_BYTES)
    return root


def test_bundled_runtime_is_used_without_site_overrides(tmp_path: Path, monkeypatch) -> None:
    from cadai import installation

    root = _installation(tmp_path / "sico")
    monkeypatch.setattr(
        installation, "__file__", str(root / "tools/ai/python/cadai/installation.py")
    )
    bundled = _python_probe(root / "runtime/python/bin/python3")

    selected = production_python({"SICO_HOME": str(root)})

    assert selected == str(bundled)


def test_bundled_runtime_ignores_stale_site_overrides(tmp_path: Path, monkeypatch) -> None:
    from cadai import installation

    root = _installation(tmp_path / "sico")
    monkeypatch.setattr(
        installation, "__file__", str(root / "tools/ai/python/cadai/installation.py")
    )
    _python_probe(root / "runtime/python/bin/python3")
    stale = _python_probe(tmp_path / "site-python/bin/python3")

    selected = production_python(
        {
            "SICO_HOME": str(root),
            "SICO_PYTHON": str(stale),
            "SICO_PYTHON_ROOT": str(stale.parents[1]),
        }
    )

    assert selected == str(root / "runtime/python/bin/python3")
