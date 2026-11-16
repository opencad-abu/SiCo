from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

AI_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = AI_ROOT / "terminal/scripts"
RG = "runtime/ripgrep/x86_64-unknown-linux-musl/bin/rg"
MANIFEST = "runtime/ripgrep/x86_64-unknown-linux-musl/MANIFEST.txt"


def _copy_rg(root):
    shutil.copytree(AI_ROOT / "runtime/ripgrep", root / "runtime/ripgrep")
    (root / "LICENSES").mkdir(exist_ok=True)
    for notice in (AI_ROOT / "LICENSES").glob("ripgrep-*"):
        shutil.copy2(notice, root / "LICENSES" / notice.name)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "manifest", "license", "symlink"])
def test_verifier_rejects_missing_or_corrupt_runtime(tmp_path, damage):
    _copy_rg(tmp_path)
    binary = tmp_path / RG
    if damage == "missing":
        binary.unlink()
    elif damage == "corrupt":
        with binary.open("r+b") as handle:
            handle.seek(-1, 2)
            handle.write(b"x")
    elif damage == "manifest":
        (tmp_path / MANIFEST).write_text("format=unknown\n")
    elif damage == "license":
        (tmp_path / "LICENSES/ripgrep-PCRE2-LICENCE.txt").unlink()
    else:
        binary.unlink()
        binary.symlink_to(AI_ROOT / RG)
    result = subprocess.run(
        [str(SCRIPTS / "verify_ripgrep_runtime.sh"), str(tmp_path),
         str(AI_ROOT / "terminal/ripgrep-runtime.lock")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0 and "ripgrep" in result.stderr


def test_cmake_installs_verified_rg_and_rejects_corruption(tmp_path):
    if not shutil.which("cmake"):
        pytest.skip("cmake unavailable")
    source = tmp_path / "ai"
    _copy_rg(source)
    terminal = source / "terminal"
    (terminal / "scripts").mkdir(parents=True)
    for relative in (
        "ripgrep-runtime.cmake",
        "ripgrep-runtime.lock",
        "scripts/verify_ripgrep_runtime.sh",
    ):
        shutil.copy2(AI_ROOT / "terminal" / relative, terminal / relative)
    (terminal / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.16)\nproject(test LANGUAGES NONE)\n"
        'include("${CMAKE_CURRENT_SOURCE_DIR}/ripgrep-runtime.cmake")\n'
    )
    build = tmp_path / "build"
    install = tmp_path / "installed"
    subprocess.run(
        ["cmake", "-S", str(terminal), "-B", str(build), "-DCMAKE_INSTALL_PREFIX=" + str(install)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["cmake", "--install", str(build)], check=True, capture_output=True)
    assert (install / RG).read_bytes() == (AI_ROOT / RG).read_bytes()
    assert os.access(install / RG, os.X_OK)
    assert (install / MANIFEST).read_bytes() == (
        AI_ROOT / "terminal/ripgrep-runtime.lock"
    ).read_bytes()
    (source / RG).write_bytes(b"wrong payload")
    result = subprocess.run(
        ["cmake", "-S", str(terminal), "-B", str(build)], capture_output=True, text=True
    )
    assert result.returncode != 0 and "ripgrep validation failed" in result.stderr
