from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "bin" / "mts-netlistor"


def _python39_without_user_site() -> str:
    """Return a Python 3.9 interpreter that has no user-site TOML dependency."""

    candidates = [Path("/usr/bin/python3"), Path(sys.executable)]
    for candidate in candidates:
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        probe = subprocess.run(
            [
                str(candidate),
                "-s",
                "-c",
                "import importlib.util, sys; "
                "print(sys.version_info[:2]); "
                "raise SystemExit(0 if not any(importlib.util.find_spec(name) "
                "for name in ('tomllib', 'tomli')) else 1)",
            ],
            check=False,
            text=True,
            capture_output=True,
        )
        if probe.returncode == 0:
            version = probe.stdout.splitlines()[0] if probe.stdout else ""
            if version.startswith("(3, 9)"):
                return str(candidate)
    pytest.skip("no Python 3.9 interpreter without tomllib is available")


def test_python39_uses_bundled_tomli_with_user_site_disabled() -> None:
    interpreter = sys.executable if sys.version_info[:2] == (3, 9) else _python39_without_user_site()
    env = dict(os.environ)
    env.pop("CAD_HOME", None)
    env.pop("SICO_HOME", None)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            interpreter,
            "-s",
            "-c",
            # Model a Python 3.9 installation without external TOML parsers even
            # when the test host supplies tomli in its approved Python prefix.
            "import importlib.abc, site, sys\n"
            "assert not site.ENABLE_USER_SITE\n"
            "class WithoutExternalToml(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        if fullname in ('tomllib', 'tomli'):\n"
            "            raise ModuleNotFoundError(fullname)\n"
            "sys.meta_path.insert(0, WithoutExternalToml())\n"
            "import mtsnetlistor.config as c; "
            "from mtsnetlistor.toml_backend import tomllib; "
            "assert tomllib.loads('answer = 42')['answer'] == 42; "
            "print(c._TOML_BACKEND)",
        ],
        cwd=ROOT,
        env={**env, "PYTHONPATH": os.pathsep.join((
            str(ROOT / "python"), str(ROOT.parent / "common/python"),
        ))},
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "vendored"


def test_wrapper_validate_works_with_python39_and_no_user_site(tmp_path: Path) -> None:
    interpreter = _python39_without_user_site()
    source = tmp_path / "source"
    source.mkdir()
    cds_lib = source / "cds.lib"
    cds_lib.write_text("DEFINE work ./work\n", encoding="ascii")
    model = source / "model.lib"
    model.write_text("model\n", encoding="ascii")
    request = tmp_path / "request.toml"
    request.write_text(
        f'''format = "sico-mts-netlistor-request"
schema_version = 1

[source]
cds_lib = "{cds_lib}"
library = "work"
cell = "top"
view = "schematic"

[simulator]
dialect = "spectre"

[[models]]
file = "{model}"
section = "tt"

''',
        encoding="ascii",
    )
    env = dict(os.environ)
    env.pop("CAD_HOME", None)
    env.pop("SICO_HOME", None)
    env.update({"SICO_PYTHON": interpreter, "PYTHONNOUSERSITE": "1"})
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [str(WRAPPER), "validate", str(request), "--json"],
        cwd=ROOT,
        env=env,
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["source"]["library"] == "work"
    assert len(payload["request_digest"]) == 64


def test_wrapper_checks_for_bundled_parser_before_starting_python(tmp_path: Path) -> None:
    # Stage only the launcher and its expected package root. This exercises the
    # deployment diagnostic without invoking a real interpreter or mutating the
    # source tree.
    staged = tmp_path / "tools/mtsnl"
    wrapper = staged / "bin" / "mts-netlistor"
    wrapper.parent.mkdir(parents=True)
    (staged / "python" / "mtsnetlistor").mkdir(parents=True)
    (staged / "python" / "mtsnetlistor" / "__init__.py").write_text("", encoding="ascii")
    wrapper.write_text(WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
    wrapper.chmod(0o755)
    (tmp_path / "bin").mkdir()
    for relative in ("tools/common/sh/sico-installation.sh", "etc/config/sico-install.json"):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT.parents[1] / relative, destination)
    result = subprocess.run(
        [str(wrapper), "--help"],
        env={"PATH": os.environ["PATH"], "SICO_PYTHON": sys.executable},
        check=False,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "bundled Python 3.9 TOML parser is missing" in result.stderr


def test_wrapper_and_cli_detach_from_inherited_mps_session(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("CAD_HOME", None)
    environment.pop("SICO_HOME", None)
    environment.update(
        {
            "SICO_PYTHON": sys.executable,
            "CDS_MPS_SESSION": "virtuoso405942",
            "CDS_MPS_HOST": "work-srv",
            "CDS_MPS_PORT": "405942",
            "CDS_MPS_FUTURE_SELECTOR": "future",
        }
    )
    probe = tmp_path / "environment-probe.py"
    actual_package = ROOT / "python"
    probe.write_text(
        "import os, sys\n"
        f"sys.path.insert(0, {str(actual_package)!r})\n"
        "from mtsnetlistor.environment import detach_mps_environment\n"
        "detach_mps_environment(os.environ)\n"
        "leaked = sorted(name for name in os.environ if name.startswith('CDS_MPS_'))\n"
        "print(','.join(leaked))\n"
        "raise SystemExit(9 if leaked else 0)\n",
        encoding="ascii",
    )
    fake_python = tmp_path / "python-probe"
    fake_python.write_text(
        "#!/bin/sh\n"
        f"exec {sys.executable!s} {probe!s}\n",
        encoding="ascii",
    )
    fake_python.chmod(0o755)
    environment["SICO_PYTHON"] = str(fake_python)
    completed = subprocess.run(
        [
            str(WRAPPER),
            "--help",
        ],
        env=environment,
        check=False,
        text=True,
        capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == ""
    # The shell wrapper removes the currently known selectors. Python then
    # performs prefix-wide cleanup before the real command dispatch.
    wrapper = WRAPPER.read_text(encoding="utf-8")
    assert "unset CDS_MPS_SESSION CDS_MPS_HOST CDS_MPS_PORT" in wrapper
    assert "detach_mps_environment(os.environ)" in (
        ROOT / "python" / "mtsnetlistor" / "cli.py"
    ).read_text(encoding="utf-8")
