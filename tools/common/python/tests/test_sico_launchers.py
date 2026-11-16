"""Exercise public dispatch in a relocated, isolated product installation."""

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / "version with spaces"
    for relative in ("bin/sico-tool-dispatch", "tools/common/sh/sico-installation.sh",
                     "etc/config/sico-install.json"):
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    for name in ("drc", "lvs", "rce", "aiassistant", "mts-netlistor", "aivw"):
        (root / "bin" / name).symlink_to("sico-tool-dispatch")
    interpreter = root / "runtime/python/bin/python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    interpreter.chmod(0o755)
    modules = {"drc": "drc", "lvs": "lvs", "rce": "rce", "aiassistant": "ai",
               "mts-netlistor": "mtsnl", "aivw": "aivw"}
    for name, module in modules.items():
        target = root / "tools" / module / "bin" / name
        target.parent.mkdir(parents=True)
        target.write_text(
            '#!/bin/sh\n'
            '. "$SICO_HOME/tools/common/sh/sico-installation.sh"\n'
            'sico_runtime_python || exit 2\n'
            'printf "%s\\n" "$SICO_HOME" "$0" "$SICO_PYTHON" "$@"\n'
        )
        target.chmod(0o755)
    return root


def run(entry, cwd, **environment):
    env = {"PATH": "/usr/bin:/bin", **environment}
    return subprocess.run([str(entry), "argument with spaces", "$(literal)"],
                          cwd=cwd, env=env, text=True, capture_output=True, timeout=10)


@pytest.mark.parametrize("command", ["drc", "lvs", "rce", "aiassistant", "mts-netlistor", "aivw"])
def test_public_commands_resolve_one_installation_after_relocation(installed, tmp_path, command):
    relocated = tmp_path / "moved installation"
    installed.rename(relocated)
    alias = tmp_path / "site-current"
    alias.symlink_to(relocated)
    result = run(alias / "bin" / command, tmp_path, SICO_HOME=str(alias),
                 SICO_PYTHON="/stale/python", SICO_PYTHON_ROOT="/stale")
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == str(relocated)
    assert lines[1].startswith(str(relocated / "tools") + "/")
    assert lines[2:] == [
        str(relocated / "runtime/python/bin/python3"),
        "argument with spaces",
        "$(literal)",
    ]


@pytest.mark.parametrize("configured", ["", "relative", "/unavailable"])
def test_bad_explicit_root_never_falls_back(installed, tmp_path, configured):
    result = run(installed / "bin/drc", tmp_path, SICO_HOME=configured, CAD_HOME=str(installed))
    assert result.returncode == 2
    assert not result.stdout


def test_other_marked_root_is_rejected(installed, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(installed, other, symlinks=True)
    result = run(installed / "bin/drc", tmp_path, SICO_HOME=str(other))
    assert result.returncode == 2
    assert "conflicts" in result.stderr
    assert not result.stdout


def test_root_marker_and_tools_cannot_escape(installed, tmp_path):
    common = installed / "tools/common"
    outside = tmp_path / "outside"
    common.rename(outside)
    common.symlink_to(outside)
    result = run(installed / "bin/drc", tmp_path)
    assert result.returncode == 2
    assert "escapes" in result.stderr


def test_stale_python_selector_is_never_interpreted_as_shell(installed, tmp_path):
    sentinel = tmp_path / "must-not-exist"
    value = "$(touch " + str(sentinel) + ")"
    result = run(installed / "bin/drc", tmp_path, SICO_PYTHON=value)
    assert result.returncode == 0
    assert result.stdout.splitlines()[2] == str(
        installed / "runtime/python/bin/python3"
    )
    assert not sentinel.exists()


def test_empty_python_selectors_are_ignored(installed, tmp_path):
    result = run(installed / "bin/drc", tmp_path, SICO_PYTHON="")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[2] == str(
        installed / "runtime/python/bin/python3"
    )


def test_noncanonical_marker_is_rejected(installed, tmp_path):
    marker = installed / "etc/config/sico-install.json"
    marker.write_bytes(marker.read_bytes() + b"\n")
    result = run(installed / "bin/drc", tmp_path)
    assert result.returncode == 2
    assert "identity" in result.stderr


def test_primary_launcher_derives_the_bundled_interpreter(tmp_path):
    root = tmp_path / "sico"
    for relative in ("bin", "tools/common", "tools/common/sh", "tools/sico/bin", "tools/sico/python",
                     "runtime/python/bin", "etc/config"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "tools/sico/bin/sico", root / "tools/sico/bin/sico")
    shutil.copy2(
        ROOT / "tools/common/sh/sico-installation.sh",
        root / "tools/common/sh/sico-installation.sh",
    )
    shutil.copy2(ROOT / "etc/config/sico-install.json", root / "etc/config")
    (root / "tools/sico/python/sico-entry").write_text("", encoding="ascii")
    interpreter = root / "runtime/python/bin/python3"
    interpreter.write_text('#!/bin/sh\nprintf "python=%s\\n" "$0"\nprintf "%s\\n" "$@"\n')
    interpreter.chmod(0o755)

    result = subprocess.run(
        [str(root / "tools/sico/bin/sico"), "--help"],
        env={"PATH": "/usr/bin:/bin", "SICO_HOME": str(root)},
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"python={interpreter}"
    assert result.stdout.splitlines()[1].endswith("tools/sico/python/sico-entry")
    assert result.stdout.splitlines()[2] == "--help"


def test_primary_launcher_uses_the_bundled_python_after_stale_site_selection(tmp_path):
    root = tmp_path / "sico"
    for relative in ("bin", "tools/common", "tools/common/sh", "tools/sico/bin", "tools/sico/python",
                     "runtime/python/bin", "etc/config"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "tools/sico/bin/sico", root / "tools/sico/bin/sico")
    shutil.copy2(
        ROOT / "tools/common/sh/sico-installation.sh",
        root / "tools/common/sh/sico-installation.sh",
    )
    shutil.copy2(ROOT / "etc/config/sico-install.json", root / "etc/config")
    (root / "tools/sico/python/sico-entry").write_text("", encoding="ascii")
    interpreter = root / "runtime/python/bin/python3"
    interpreter.write_text(
        '#!/bin/sh\nprintf "python=%s\\n" "$0"\n'
        'printf "root=%s\\n" "$SICO_PYTHON_ROOT"\n'
        'printf "system-lib=%s\\n" "$SICO_SYSTEM_LD_LIBRARY_PATH"\n'
        'printf "ai-lib=%s\\n" "$SICO_AI_PYTHON_LIBRARY_PATH"\n',
        encoding="ascii",
    )
    interpreter.chmod(0o755)

    result = subprocess.run(
        [str(root / "tools/sico/bin/sico"), "--help"],
        env={
            "PATH": "/usr/bin:/bin",
            "SICO_HOME": str(root),
            "SICO_PYTHON": "/stale/site/python",
        },
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"python={interpreter}",
        f"root={root / 'runtime/python'}",
        f"system-lib={root / 'runtime/python/lib'}:{root / 'lib'}",
        f"ai-lib={root / 'runtime/python/lib'}:{root / 'lib'}",
    ]


def test_primary_launcher_rejects_missing_runtime_despite_fallbacks(tmp_path):
    root = tmp_path / "sico"
    for relative in ("bin", "tools/common", "tools/common/sh", "tools/sico/bin",
                     "tools/sico/python", "etc/config"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "tools/sico/bin/sico", root / "tools/sico/bin/sico")
    shutil.copy2(
        ROOT / "tools/common/sh/sico-installation.sh",
        root / "tools/common/sh/sico-installation.sh",
    )
    shutil.copy2(ROOT / "etc/config/sico-install.json", root / "etc/config")
    (root / "tools/sico/python/sico-entry").write_text("", encoding="ascii")
    (root / "etc/config/dev-runtime").write_text("/site/python/bin/python3\n")

    result = subprocess.run(
        [str(root / "tools/sico/bin/sico"), "--help"],
        env={
            "PATH": "/usr/bin:/bin",
            "SICO_HOME": str(root),
            "SICO_PYTHON": "/site/python/bin/python3",
            "SICO_PYTHON_ROOT": "/site/python",
        },
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "SiCo Python is unavailable" in result.stderr
