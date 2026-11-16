"""Runtime-info report contracts for the fixed production prefix."""

import os
from pathlib import Path
import sys

import pytest

from aivworkbench.runtime_info import collect_runtime_info, detect_capabilities

RUNTIME_PYTHON = Path(os.environ.get("SICO_HOME", "")) / "runtime/python/bin/python3"

pytestmark = pytest.mark.skipif(
    not RUNTIME_PYTHON.is_file()
    or RUNTIME_PYTHON.resolve() != Path(sys.executable).resolve(),
    reason="the process is not running from $SICO_HOME/runtime/python",
)


def test_report_is_explicit_about_python_prefix_libraries_and_shims():
    report = collect_runtime_info()
    assert report["sys_version_info"] == [3, 9, 13]
    assert report["backports"] == []
    assert report["runtime_library_policy"] == "cad-python-root"
    assert report["python_root_libraries_allowed"]
    assert not report["approved_module_probes"]["toml"]["available"]
    assert report["approved_module_probes"]["tomli"]["available"]
    assert report["approved_module_probes"]["tomli"]["origin_inside_CAD_PYTHON_ROOT"]
    assert not report["approved_module_probes"]["tomllib"]["available"]
    assert report["approved_module_probes"]["PyQt5"]["available"]
    assert report["approved_module_probes"]["PyQt5"]["origin_inside_CAD_PYTHON_ROOT"]
    distribution_names = {item["name"] for item in report["installed_distributions"]}
    assert "tomli" in distribution_names
    assert "PyQt5" in distribution_names
    assert all(item["location_inside_CAD_PYTHON_ROOT"] for item in report["installed_distributions"])
    assert not report["user_site_packages_enabled"]
    assert set(report["capabilities"]) == set(detect_capabilities())
    assert report["capabilities"]["tomli"]
    assert not report["capabilities"]["toml"]
    assert report["capabilities"]["PyQt5"]
    assert not report["capabilities"]["tomllib"]
    assert set(("LICENSE", "NOTICE")) == set(report["bundle"]["license_files"])
