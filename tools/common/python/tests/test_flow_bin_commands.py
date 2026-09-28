from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]
COMMANDS = (
    ("drc", CAD_ROOT / "drc/bin/drc", CAD_ROOT / "drc/python/drc"),
    ("lvs", CAD_ROOT / "lvs/bin/lvs", CAD_ROOT / "lvs/python/lvs"),
    ("rce", CAD_ROOT / "rce/bin/rce", CAD_ROOT / "rce/python/rce"),
    ("lefgen", CAD_ROOT / "lef/bin/lefgen", CAD_ROOT / "lef/python/lef"),
    (
        "aiassistant",
        CAD_ROOT / "ai/bin/aiassistant",
        CAD_ROOT / "ai/python/sico-ai",
    ),
)
TOP_LEVEL_COMMANDS = (
    ("rce", CAD_ROOT.parent / "bin/rce", CAD_ROOT / "rce/python/rce", ()),
    ("drc", CAD_ROOT.parent / "bin/drc", CAD_ROOT / "drc/python/drc", ()),
    ("lvs", CAD_ROOT.parent / "bin/lvs", CAD_ROOT / "lvs/python/lvs", ()),
    ("lefgen", CAD_ROOT.parent / "bin/lefgen", CAD_ROOT / "lef/python/lef", ()),
    (
        "dspfana",
        CAD_ROOT.parent / "bin/dspfana",
        CAD_ROOT / "rce/python/rce",
        ("dspf-gui",),
    ),
    (
        "gdsout",
        CAD_ROOT.parent / "bin/gdsout",
        CAD_ROOT / "lvs/python/lvs",
        ("stream-gds",),
    ),
    (
        "cdlout",
        CAD_ROOT.parent / "bin/cdlout",
        CAD_ROOT / "lvs/python/lvs",
        ("export-cdl",),
    ),
    (
        "aiassistant",
        CAD_ROOT.parent / "bin/aiassistant",
        CAD_ROOT / "ai/python/sico-ai",
        (),
    ),
)
FLOW_OVERRIDES = {
    "drc": "DRC_PYTHON",
    "lvs": "LVS_PYTHON",
    "rce": "RCE_PYTHON",
    "lefgen": "LEF_PYTHON",
}
ORIGINAL_LD_NAMES = {
    "drc": "DRC_ORIG_LD_LIBRARY_PATH",
    "lvs": "LVS_ORIG_LD_LIBRARY_PATH",
    "rce": "RCE_ORIG_LD_LIBRARY_PATH",
    "lefgen": "LEF_ORIG_LD_LIBRARY_PATH",
}


def _entry_arguments(name: str, entry: Path, *arguments: str) -> list[str]:
    python_flags = ["-s"] if name != "aiassistant" else []
    return [*(f"argument={flag}" for flag in python_flags), f"argument={entry}", *arguments]


def _environment(**overrides: str) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "CAD_AI_PYTHON_LIBRARY_PATH",
        "CAD_CODEX_PYTHON_LIBRARY_PATH",
        "CAD_PYTHON",
        "CAD_PYTHON_ROOT",
        "CAD_SYSTEM_LD_LIBRARY_PATH",
        "DRC_PYTHON",
        "LEF_PYTHON",
        "LVS_PYTHON",
        "RCE_PYTHON",
        "RCE_ANALYZER_PYTHON",
    ):
        environment.pop(name, None)
        if name.startswith("CAD_"):
            environment.pop("SICO_" + name[4:], None)
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(overrides)
    return environment


@pytest.mark.parametrize(
    ("name", "launcher", "entry", "prefix"), TOP_LEVEL_COMMANDS
)
def test_top_level_command_links_dispatch_to_module_launchers(
    tmp_path: Path,
    name: str,
    launcher: Path,
    entry: Path,
    prefix: tuple[str, ...],
) -> None:
    interpreter = _write_interpreter(tmp_path / "configured-python")

    assert launcher.is_symlink()
    assert os.readlink(launcher) == "sico-tool-dispatch"
    result = subprocess.run(
        [str(launcher), "argument with spaces", "$(literal)"],
        env=_environment(SICO_PYTHON=str(interpreter)),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={interpreter}",
        *_entry_arguments(
            name,
            entry,
            *(f"argument={argument}" for argument in prefix),
            "argument=argument with spaces",
            "argument=$(literal)",
        ),
    ]


def test_dspfana_prefers_analyzer_python_override(tmp_path: Path) -> None:
    selected = _write_interpreter(tmp_path / "analyzer-python")
    ignored = _write_interpreter(tmp_path / "shared-python")

    result = subprocess.run(
        [str(CAD_ROOT.parent / "bin/dspfana"), "--help"],
        env=_environment(
            SICO_PYTHON=str(ignored),
            RCE_ANALYZER_PYTHON=str(selected),
        ),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={selected}",
        "argument=-s",
        f"argument={CAD_ROOT / 'rce/python/rce'}",
        "argument=dspf-gui",
        "argument=--help",
    ]


def _write_interpreter(path: Path, *, exit_code: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "printf 'interpreter=%s\\n' \"$0\"\n"
        "for argument in \"$@\"; do\n"
        "    printf 'argument=%s\\n' \"$argument\"\n"
        "done\n"
        f"exit {exit_code}\n",
        encoding="ascii",
    )
    path.chmod(0o755)
    return path


def _write_environment_interpreter(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "printf 'pythonhome=%s\\n' \"${PYTHONHOME-unset}\"\n"
        "printf 'pythonpath=%s\\n' \"${PYTHONPATH-unset}\"\n"
        "printf 'ld=%s\\n' \"${LD_LIBRARY_PATH-unset}\"\n"
        "printf 'ld_preload=%s\\n' \"${LD_PRELOAD-unset}\"\n"
        "printf 'ld_audit=%s\\n' \"${LD_AUDIT-unset}\"\n"
        "printf 'dontwrite=%s\\n' \"${PYTHONDONTWRITEBYTECODE-unset}\"\n"
        "printf 'nousersite=%s\\n' \"${PYTHONNOUSERSITE-unset}\"\n"
        "for name in DRC_ORIG_LD_LIBRARY_PATH LEF_ORIG_LD_LIBRARY_PATH "
        "LVS_ORIG_LD_LIBRARY_PATH RCE_ORIG_LD_LIBRARY_PATH; do\n"
        "    eval value=\\\"\\${$name-unset}\\\"\n"
        "    printf '%s=%s\\n' \"$name\" \"$value\"\n"
        "done\n",
        encoding="ascii",
    )
    path.chmod(0o755)
    return path


@pytest.mark.parametrize(("name", "launcher", "entry"), COMMANDS)
def test_standard_flow_command_uses_sico_python_and_forwards_arguments(
    tmp_path: Path, name: str, launcher: Path, entry: Path
) -> None:
    interpreter = _write_interpreter(tmp_path / "configured-python", exit_code=17)

    result = subprocess.run(
        [str(launcher), "argument with spaces", "--", "$(literal)"],
        env=_environment(
            SICO_PYTHON=str(interpreter),
            SICO_PYTHON_ROOT=str(tmp_path / "ignored-root"),
        ),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 17, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={interpreter}",
        *_entry_arguments(
            name,
            entry,
            "argument=argument with spaces",
            "argument=--",
            "argument=$(literal)",
        ),
    ]
    assert result.stderr == ""


@pytest.mark.parametrize(("_name", "launcher", "entry"), COMMANDS)
def test_standard_flow_command_uses_sico_python_root(
    tmp_path: Path, _name: str, launcher: Path, entry: Path
) -> None:
    python_root = tmp_path / "python-root"
    interpreter = _write_interpreter(python_root / "bin/python3")

    result = subprocess.run(
        [str(launcher), "probe"],
        env=_environment(SICO_PYTHON_ROOT=str(python_root)),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={interpreter}",
        *_entry_arguments(_name, entry, "argument=probe"),
    ]


@pytest.mark.parametrize(
    ("_name", "launcher", "entry"),
    tuple(command for command in COMMANDS if command[0] != "aiassistant"),
)
def test_standard_flow_command_falls_back_to_path_python3(
    tmp_path: Path, _name: str, launcher: Path, entry: Path
) -> None:
    interpreter = _write_interpreter(tmp_path / "bin/python3")
    path = os.pathsep.join((str(interpreter.parent), "/usr/bin", "/bin"))

    result = subprocess.run(
        [str(launcher)],
        env=_environment(PATH=path),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={interpreter}",
        *_entry_arguments(_name, entry),
    ]


def test_aiassistant_prefers_system_python_before_path(tmp_path: Path) -> None:
    if not Path("/usr/bin/python3").is_file():
        pytest.skip("/usr/bin/python3 is unavailable")
    launcher = CAD_ROOT / "ai/bin/aiassistant"
    path_python = _write_interpreter(tmp_path / "bin/python3")

    result = subprocess.run(
        [str(launcher), "--help"],
        env=_environment(PATH=str(path_python.parent)),
        check=False,
        text=True,
        capture_output=True,
    )

    assert f"interpreter={path_python}" not in result.stdout


@pytest.mark.parametrize(("name", "launcher", "_entry"), COMMANDS)
def test_standard_flow_command_rejects_invalid_sico_python(
    tmp_path: Path, name: str, launcher: Path, _entry: Path
) -> None:
    missing = tmp_path / "missing-python"
    fallback = _write_interpreter(tmp_path / "python-root/bin/python3")

    result = subprocess.run(
        [str(launcher)],
        env=_environment(
            SICO_PYTHON=str(missing),
            SICO_PYTHON_ROOT=str(fallback.parents[1]),
        ),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == f"{name}: Python is not executable: {missing}"


@pytest.mark.parametrize(("name", "launcher", "entry"), COMMANDS)
def test_standard_flow_command_reports_missing_python_entry(
    tmp_path: Path, name: str, launcher: Path, entry: Path
) -> None:
    installed_launcher = tmp_path / "tools" / entry.parents[1].name / "bin" / launcher.name
    installed_launcher.parent.mkdir(parents=True)
    shutil.copy2(launcher, installed_launcher)
    (tmp_path / "bin").mkdir()
    support = tmp_path / "tools/common/sh/sico-installation.sh"
    support.parent.mkdir(parents=True)
    shutil.copy2(CAD_ROOT / "common/sh/sico-installation.sh", support)
    marker = tmp_path / "etc/config/sico-install.json"
    marker.parent.mkdir(parents=True)
    shutil.copy2(CAD_ROOT.parent / "etc/config/sico-install.json", marker)
    missing_entry = installed_launcher.parents[1] / "python" / entry.name

    result = subprocess.run(
        [str(installed_launcher)],
        env=_environment(SICO_PYTHON="/bin/sh"),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip() == (
        f"{name}: Python entry is unavailable: {missing_entry}"
    )


@pytest.mark.parametrize(
    ("name", "launcher", "entry"),
    tuple(command for command in COMMANDS if command[0] != "aiassistant"),
)
def test_flow_override_precedes_shared_python(
    tmp_path: Path, name: str, launcher: Path, entry: Path
) -> None:
    selected = _write_interpreter(tmp_path / "flow-python")
    ignored = _write_interpreter(tmp_path / "shared-python")

    result = subprocess.run(
        [str(launcher)],
        env=_environment(
            SICO_PYTHON=str(ignored),
            **{FLOW_OVERRIDES[name]: str(selected)},
        ),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={selected}",
        *_entry_arguments(name, entry),
    ]


@pytest.mark.parametrize(("name", "launcher", "_entry"), COMMANDS)
def test_standard_flow_command_isolates_python_environment(
    tmp_path: Path, name: str, launcher: Path, _entry: Path
) -> None:
    interpreter = _write_environment_interpreter(tmp_path / "python")
    result = subprocess.run(
        [str(launcher)],
        env=_environment(
            SICO_AI_PYTHON_LIBRARY_PATH="/ai/python/lib",
            SICO_PYTHON=str(interpreter),
            SICO_SYSTEM_LD_LIBRARY_PATH="/cad/python/lib",
            LD_LIBRARY_PATH="/eda/tool/lib",
            PYTHONHOME="/bad/python/home",
            PYTHONPATH="/bad/python/path",
            LD_PRELOAD="/bad/preload.so",
            LD_AUDIT="/bad/audit.so",
        ),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert values["pythonhome"] == "unset"
    assert values["pythonpath"] == "unset"
    assert values["dontwrite"] == "1"
    assert values["ld_preload"] == "unset"
    assert values["ld_audit"] == "unset"
    if name == "aiassistant":
        assert values["ld"] == "/ai/python/lib"
        assert values["nousersite"] == "1"
    else:
        assert values["ld"] == "/cad/python/lib"
        assert values["nousersite"] == "1"
        assert values[ORIGINAL_LD_NAMES[name]] == "/eda/tool/lib"


@pytest.mark.parametrize(
    ("name", "launcher", "_entry"),
    tuple(command for command in COMMANDS if command[0] != "aiassistant"),
)
def test_standard_flow_command_preserves_existing_original_eda_library_path(
    tmp_path: Path, name: str, launcher: Path, _entry: Path
) -> None:
    interpreter = _write_environment_interpreter(tmp_path / "python")
    original_name = ORIGINAL_LD_NAMES[name]

    result = subprocess.run(
        [str(launcher)],
        env=_environment(
            SICO_PYTHON=str(interpreter),
            SICO_SYSTEM_LD_LIBRARY_PATH="/cad/python/lib",
            LD_LIBRARY_PATH="/already/sanitized/lib",
            **{original_name: "/eda/original/lib"},
        ),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert values[original_name] == "/eda/original/lib"


def test_aiassistant_empty_library_override_stays_empty(
    tmp_path: Path,
) -> None:
    launcher = CAD_ROOT / "ai/bin/aiassistant"
    interpreter = _write_environment_interpreter(tmp_path / "python")

    result = subprocess.run(
        [str(launcher)],
        env=_environment(
            SICO_AI_PYTHON_LIBRARY_PATH="",
            SICO_PYTHON=str(interpreter),
            SICO_SYSTEM_LD_LIBRARY_PATH="/cad/python/lib",
        ),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert values["ld"] == ""


def test_aiassistant_retired_library_override_is_rejected(
    tmp_path: Path,
) -> None:
    launcher = CAD_ROOT / "ai/bin/aiassistant"
    interpreter = _write_environment_interpreter(tmp_path / "python")

    result = subprocess.run(
        [str(launcher)],
        env=_environment(
            CAD_CODEX_PYTHON_LIBRARY_PATH="/legacy/python/lib",
            SICO_PYTHON=str(interpreter),
            SICO_SYSTEM_LD_LIBRARY_PATH="/cad/python/lib",
        ),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "CAD_CODEX_PYTHON_LIBRARY_PATH was removed" in result.stderr
