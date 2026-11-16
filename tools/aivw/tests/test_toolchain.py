from __future__ import annotations

from pathlib import Path

import pytest

from aivworkbench.errors import EnvironmentError
from aivworkbench.profiles import ToolSpec
from aivworkbench.toolchain import (
    detach_mps_environment,
    parse_setup_exports,
    parse_setup_modules,
    probe_tools,
)


def test_parse_setup_reads_only_valid_module_load_lines(tmp_path: Path) -> None:
    setup = tmp_path / "setup.bashrc"
    setup.write_text(
        "module load ic/23.10.130\n"
        "export PROJECT=/do/not/execute\n"
        "module load xcelium/23.09.002 spectre/23.10.538 # fixed\n",
        encoding="utf-8",
    )
    assert parse_setup_modules(setup) == (
        "ic/23.10.130",
        "xcelium/23.09.002",
        "spectre/23.10.538",
    )


def test_unsafe_module_name_is_rejected(tmp_path: Path) -> None:
    setup = tmp_path / "setup.bashrc"
    setup.write_text("module load 'ic/23;touch-bad'\n", encoding="utf-8")
    with pytest.raises(EnvironmentError, match="unsafe module"):
        parse_setup_modules(setup)


def test_parse_setup_reads_only_whitelisted_literal_exports(tmp_path: Path) -> None:
    setup = tmp_path / "setup.bashrc"
    setup.write_text(
        "export PROJECT=/ignored\n"
        "export PROJ_AMS_DB_DIR='/data/ams payload'\n",
        encoding="utf-8",
    )
    assert parse_setup_exports(setup, ("PROJ_AMS_DB_DIR",)) == {
        "PROJ_AMS_DB_DIR": "/data/ams payload"
    }


def test_dynamic_setup_export_is_rejected(tmp_path: Path) -> None:
    setup = tmp_path / "setup.bashrc"
    setup.write_text("export PROJ_AMS_DB_DIR=$HOME/ams\n", encoding="utf-8")
    with pytest.raises(EnvironmentError, match="literal setup value"):
        parse_setup_exports(setup, ("PROJ_AMS_DB_DIR",))


def test_mps_identity_is_removed_by_prefix() -> None:
    environment = {
        "PATH": "/bin",
        "CDS_MPS_SESSION": "host",
        "CDS_MPS_FUTURE": "future",
    }
    detach_mps_environment(environment)
    assert environment == {"PATH": "/bin"}


def test_tool_probe_preserves_symlink_name_for_dispatch(tmp_path: Path) -> None:
    wrapper = tmp_path / ".generic-wrapper"
    wrapper.write_text(
        "#!/bin/sh\n"
        "case $0 in\n"
        "  */virtuoso) printf 'IC23.1-test\\n'; exit 0 ;;\n"
        "  *) printf 'lost argv0: %s\\n' \"$0\" >&2; exit 9 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    launcher = tmp_path / "virtuoso"
    launcher.symlink_to(wrapper.name)
    result = probe_tools(
        (ToolSpec("virtuoso", "virtuoso", ("-W",), "IC23\\.1-test"),),
        {"PATH": str(tmp_path)},
    )[0]
    assert result.status == "PASS"
    assert result.path == str(launcher)
