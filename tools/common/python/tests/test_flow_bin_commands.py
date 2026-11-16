from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]
COMMANDS = (
    ("drc", "drc", "drc", "drc"),
    ("lvs", "lvs", "lvs", "lvs"),
    ("rce", "rce", "rce", "rce"),
    ("lefgen", "lef", "lefgen", "lef"),
    ("aiassistant", "ai", "aiassistant", "sico-ai"),
)
TOP_LEVEL_COMMANDS = (
    ("rce", "rce", "rce", "rce", ()),
    ("drc", "drc", "drc", "drc", ()),
    ("lvs", "lvs", "lvs", "lvs", ()),
    ("lefgen", "lef", "lefgen", "lef", ()),
    ("dspfana", "rce", "rce", "rce", ("dspf-gui",)),
    ("gdsout", "lvs", "lvs", "lvs", ("stream-gds",)),
    ("cdlout", "lvs", "lvs", "lvs", ("export-cdl",)),
    ("aiassistant", "ai", "aiassistant", "sico-ai", ()),
)
ORIGINAL_LD_NAMES = {
    "drc": "DRC_ORIG_LD_LIBRARY_PATH",
    "lvs": "LVS_ORIG_LD_LIBRARY_PATH",
    "rce": "RCE_ORIG_LD_LIBRARY_PATH",
    "lefgen": "LEF_ORIG_LD_LIBRARY_PATH",
}


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
        "printf 'dontwrite=%s\\n' \"${PYTHONDONTWRITEBYTECODE-unset}\"\n"
        "printf 'nousersite=%s\\n' \"${PYTHONNOUSERSITE-unset}\"\n"
        "printf 'ld_preload=%s\\n' \"${LD_PRELOAD-unset}\"\n"
        "printf 'ld_audit=%s\\n' \"${LD_AUDIT-unset}\"\n"
        "for name in DRC_ORIG_LD_LIBRARY_PATH LEF_ORIG_LD_LIBRARY_PATH "
        "LVS_ORIG_LD_LIBRARY_PATH RCE_ORIG_LD_LIBRARY_PATH; do\n"
        "    eval value=\\\"\\${$name-unset}\\\"\n"
        "    printf '%s=%s\\n' \"$name\" \"$value\"\n"
        "done\n",
        encoding="ascii",
    )
    path.chmod(0o755)
    return path


def _stage(tmp_path: Path, tool: str, launcher: str, entry: str, *,
           alias: str | None = None, entry_present: bool = True,
           runtime: bool = True):
    root = tmp_path / "sico"
    for relative in ("bin", "tools/common/sh", f"tools/{tool}/bin",
                     f"tools/{tool}/python", "etc/config"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    shutil.copy2(CAD_ROOT.parent / "bin/sico-tool-dispatch", root / "bin/sico-tool-dispatch")
    shutil.copy2(CAD_ROOT / tool / "bin" / launcher, root / "tools" / tool / "bin" / launcher)
    shutil.copy2(
        CAD_ROOT / "common/sh/sico-installation.sh",
        root / "tools/common/sh/sico-installation.sh",
    )
    shutil.copy2(
        CAD_ROOT.parent / "etc/config/sico-install.json",
        root / "etc/config/sico-install.json",
    )
    if alias:
        (root / "bin" / alias).symlink_to("sico-tool-dispatch")
    entry_path = root / "tools" / tool / "python" / entry
    if entry_present:
        entry_path.write_text("", encoding="ascii")
    interpreter = None
    if runtime:
        interpreter = _write_interpreter(root / "runtime/python/bin/python3")
    return root, interpreter, root / "tools" / tool / "bin" / launcher, entry_path


def _environment(root: Path, **overrides: str) -> dict[str, str]:
    environment = {
        "PATH": "/usr/bin:/bin",
        "SICO_HOME": str(root),
        "SICO_PYTHON": "/stale/python3",
        "SICO_PYTHON_ROOT": "/stale/python",
        "LD_LIBRARY_PATH": "/eda/tool/lib",
    }
    environment.update(overrides)
    return environment


@pytest.mark.parametrize(
    ("alias", "tool", "launcher", "entry", "prefix"), TOP_LEVEL_COMMANDS
)
def test_top_level_commands_use_the_installation_runtime(
    tmp_path: Path, alias: str, tool: str, launcher: str, entry: str,
    prefix: tuple[str, ...],
) -> None:
    root, interpreter, _, entry_path = _stage(
        tmp_path, tool, launcher, entry, alias=alias
    )

    result = subprocess.run(
        [str(root / "bin" / alias), "argument with spaces", "$(literal)"],
        env=_environment(root),
        check=False,
        text=True,
        capture_output=True,
    )

    flags = ["-s"] if alias != "aiassistant" else []
    expected = [
        f"interpreter={interpreter}",
        *(f"argument={flag}" for flag in flags),
        f"argument={entry_path}",
        *(f"argument={item}" for item in prefix),
        "argument=argument with spaces",
        "argument=$(literal)",
    ]
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected


@pytest.mark.parametrize(("name", "tool", "launcher", "entry"), COMMANDS)
def test_flow_launchers_forward_arguments_with_the_installation_runtime(
    tmp_path: Path, name: str, tool: str, launcher: str, entry: str
) -> None:
    root, interpreter, launcher_path, entry_path = _stage(
        tmp_path, tool, launcher, entry
    )
    _write_interpreter(interpreter, exit_code=17)

    result = subprocess.run(
        [str(launcher_path), "argument with spaces", "--", "$(literal)"],
        env=_environment(root),
        check=False,
        text=True,
        capture_output=True,
    )

    flags = ["-s"] if name != "aiassistant" else []
    assert result.returncode == 17, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={interpreter}",
        *(f"argument={flag}" for flag in flags),
        f"argument={entry_path}",
        "argument=argument with spaces",
        "argument=--",
        "argument=$(literal)",
    ]
    assert result.stderr == ""


@pytest.mark.parametrize(("name", "tool", "launcher", "entry"), COMMANDS)
def test_flow_launchers_ignore_tool_specific_python_overrides(
    tmp_path: Path, name: str, tool: str, launcher: str, entry: str
) -> None:
    root, interpreter, launcher_path, _ = _stage(tmp_path, tool, launcher, entry)
    ignored = _write_interpreter(tmp_path / "ignored-python")
    override = {
        "drc": "DRC_PYTHON",
        "lvs": "LVS_PYTHON",
        "rce": "RCE_PYTHON",
        "lefgen": "LEF_PYTHON",
        "aiassistant": "RCE_ANALYZER_PYTHON",
    }[name]

    result = subprocess.run(
        [str(launcher_path), "probe"],
        env=_environment(root, **{override: str(ignored)}),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"interpreter={interpreter}"


@pytest.mark.parametrize(("name", "tool", "launcher", "entry"), COMMANDS)
def test_flow_launchers_require_the_installation_runtime(
    tmp_path: Path, name: str, tool: str, launcher: str, entry: str
) -> None:
    root, _, launcher_path, _ = _stage(
        tmp_path, tool, launcher, entry, runtime=False
    )

    result = subprocess.run(
        [str(launcher_path)],
        env=_environment(root, PATH="/usr/bin:/bin"),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "SiCo Python is unavailable" in result.stderr


@pytest.mark.parametrize(("name", "tool", "launcher", "entry"), COMMANDS)
def test_flow_launchers_report_missing_entries(
    tmp_path: Path, name: str, tool: str, launcher: str, entry: str
) -> None:
    root, _, launcher_path, entry_path = _stage(
        tmp_path, tool, launcher, entry, entry_present=False
    )

    result = subprocess.run(
        [str(launcher_path)],
        env=_environment(root),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.strip().endswith(
        f"{name}: Python entry is unavailable: {entry_path}"
    )


@pytest.mark.parametrize(
    ("name", "tool", "launcher", "entry"),
    tuple(command for command in COMMANDS if command[0] != "aiassistant"),
)
def test_flow_launchers_isolate_interpreter_environment(
    tmp_path: Path, name: str, tool: str, launcher: str, entry: str
) -> None:
    root, _, launcher_path, _ = _stage(tmp_path, tool, launcher, entry)
    interpreter = _write_environment_interpreter(root / "runtime/python/bin/python3")

    result = subprocess.run(
        [str(launcher_path)],
        env=_environment(
            root,
            SICO_AI_PYTHON_LIBRARY_PATH="/stale/ai/python/lib",
            SICO_SYSTEM_LD_LIBRARY_PATH="/stale/cad/python/lib",
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
    assert values["nousersite"] == "1"
    assert values["ld"] == f"{root}/runtime/python/lib:{root}/lib"
    assert values[ORIGINAL_LD_NAMES[name]] == "/eda/tool/lib"
    assert interpreter.is_file()


@pytest.mark.parametrize(
    ("name", "tool", "launcher", "entry"),
    tuple(command for command in COMMANDS if command[0] != "aiassistant"),
)
def test_flow_launchers_preserve_original_eda_library_path(
    tmp_path: Path, name: str, tool: str, launcher: str, entry: str
) -> None:
    root, _, launcher_path, _ = _stage(tmp_path, tool, launcher, entry)
    _write_environment_interpreter(root / "runtime/python/bin/python3")
    original_name = ORIGINAL_LD_NAMES[name]

    result = subprocess.run(
        [str(launcher_path)],
        env=_environment(
            root,
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


def test_aiassistant_uses_the_installation_runtime(tmp_path: Path) -> None:
    root, interpreter, launcher_path, entry_path = _stage(
        tmp_path, "ai", "aiassistant", "sico-ai"
    )

    result = subprocess.run(
        [str(launcher_path), "--help"],
        env=_environment(root, SICO_AI_PYTHON_LIBRARY_PATH=""),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"interpreter={interpreter}",
        f"argument={entry_path}",
        "argument=--help",
    ]


def test_aiassistant_rejects_retired_library_override(tmp_path: Path) -> None:
    root, _, launcher_path, _ = _stage(tmp_path, "ai", "aiassistant", "sico-ai")

    result = subprocess.run(
        [str(launcher_path)],
        env=_environment(root, CAD_CODEX_PYTHON_LIBRARY_PATH="/legacy/python/lib"),
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert "CAD_CODEX_PYTHON_LIBRARY_PATH was removed" in result.stderr
