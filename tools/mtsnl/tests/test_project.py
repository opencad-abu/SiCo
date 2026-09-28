from __future__ import annotations

import os
from pathlib import Path

import pytest

from mtsnetlistor.errors import RequestValidationError
from mtsnetlistor.project import (
    discover_projects,
    load_project_environment,
    resolve_project,
)


def _module_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "modulefiles"
    root.mkdir()
    user_home = tmp_path / "users"
    user_home.mkdir()
    module = root / "alpha"
    module.write_text(
        "#%Module 1.0\n"
        "setenv PROJ_NAME alpha\n"
        f"setenv PROJ_USER_HOME {user_home}\n",
        encoding="utf-8",
    )
    (root / ".version").write_text("hidden", encoding="utf-8")
    return root, user_home


def test_discover_projects_is_deterministic_and_excludes_metadata(tmp_path: Path) -> None:
    root, _ = _module_root(tmp_path)
    (root / "beta").write_text("#%Module 1.0\n", encoding="utf-8")
    (root / "editor~").write_text("backup", encoding="utf-8")
    (root / "README").write_text("project notes", encoding="utf-8")
    (root / "invalid\nname").write_text("#%Module 1.0\n", encoding="utf-8")
    assert discover_projects(root) == ("alpha", "beta")


@pytest.mark.parametrize("setting", [None, "", "   "])
def test_module_root_requires_explicit_configuration_when_not_passed(
    tmp_path: Path, setting: str | None,
) -> None:
    root, _ = _module_root(tmp_path)
    environment = {"SICO_HOME": str(tmp_path), "CAD_HOME": str(tmp_path),
                   "MODULEPATH": str(root)}
    if setting is not None:
        environment["MTS_NETLISTOR_MODULEFILES"] = setting
    with pytest.raises(RequestValidationError, match="MTS_NETLISTOR_MODULEFILES"):
        discover_projects(environment=environment)


def test_module_root_uses_only_the_explicit_environment_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _ = _module_root(tmp_path)
    monkeypatch.setenv("MTS_NETLISTOR_MODULEFILES", str(root))

    assert discover_projects() == ("alpha",)


def test_explicit_module_root_overrides_environment(tmp_path: Path) -> None:
    root, _ = _module_root(tmp_path)
    assert discover_projects(root, environment={
        "MTS_NETLISTOR_MODULEFILES": str(tmp_path / "wrong")
    }) == ("alpha",)


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_invalid_module_directory_is_reported(tmp_path: Path, kind: str) -> None:
    root = tmp_path / "invalid"
    if kind == "file":
        root.touch()
    with pytest.raises(RequestValidationError, match="module directory does not exist"):
        discover_projects(root)


def test_project_name_rejects_path_traversal(tmp_path: Path) -> None:
    root, _ = _module_root(tmp_path)
    with pytest.raises(RequestValidationError, match="module name"):
        load_project_environment("../alpha", root=root)
    with pytest.raises(RequestValidationError, match="module name"):
        load_project_environment("alpha\nmodule", root=root)


def test_load_project_environment_does_not_mutate_base_and_detaches_mps(
    tmp_path: Path,
) -> None:
    root, _ = _module_root(tmp_path)
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", "xh"),
        "MODULEPATH": str(root),
        "PROJ_NAME": "host",
        "CDS_MPS_SESSION": "host-session",
    }
    result = load_project_environment("alpha", root=root, base_environment=base)
    assert result["PROJ_NAME"] == "alpha"
    assert "CDS_MPS_SESSION" not in result
    assert base["PROJ_NAME"] == "host"


def test_project_environment_drops_unmanaged_host_project_variables(
    tmp_path: Path,
) -> None:
    root, _ = _module_root(tmp_path)
    base = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", "xh"),
        "MODULEPATH": str(root),
        "PROJECT_ROOT": "/host/project",
        "PROJ_NAME": "host",
    }
    result = load_project_environment("alpha", root=root, base_environment=base)
    assert "PROJECT_ROOT" not in result
    assert result["PROJ_NAME"] == "alpha"


def test_project_environment_reports_only_non_sensitive_timings(
    tmp_path: Path,
) -> None:
    root, _ = _module_root(tmp_path)
    timings = {}
    load_project_environment(
        "alpha",
        root=root,
        base_environment={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "USER": os.environ.get("USER", "xh"),
        },
        timings=timings,
    )

    assert set(timings) == {"modulecmd_purge_ms", "modulecmd_load_ms"}
    assert all(isinstance(value, float) and value >= 0 for value in timings.values())


def test_project_environment_requires_module_user_home(tmp_path: Path) -> None:
    root, _ = _module_root(tmp_path)
    (root / "broken").write_text(
        "#%Module 1.0\nsetenv PROJ_NAME broken\n", encoding="utf-8"
    )
    with pytest.raises(RequestValidationError, match="PROJ_USER_HOME"):
        load_project_environment("broken", root=root)


def test_modulecmd_parser_rejects_unknown_calls_and_aliases() -> None:
    from mtsnetlistor.moduleenv import apply_python_modulecmd

    with pytest.raises(RequestValidationError, match="unsupported call"):
        apply_python_modulecmd(
            "import os\nos.environ['X']='1'\nprint('bad')\n", {}
        )
    with pytest.raises(RequestValidationError, match="unexpected import"):
        apply_python_modulecmd("import os as env\n_mlstatus = True\n", {})


def test_modulecmd_parser_requires_status_when_requested() -> None:
    from mtsnetlistor.moduleenv import apply_python_modulecmd

    with pytest.raises(RequestValidationError, match="status marker"):
        apply_python_modulecmd(
            "import os\nos.environ['X']='1'\n", {}, require_status=True
        )


def test_resolve_project_replaces_inherited_host_cdslib_selector(
    tmp_path: Path,
) -> None:
    root, user_home = _module_root(tmp_path)
    cds_lib = user_home / "cds.lib"
    cds_lib.write_text("DEFINE alpha .\n", encoding="utf-8")
    context = resolve_project(
        "alpha",
        root=root,
        base_environment={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "USER": os.environ.get("USER", "xh"),
            "CDS_LIB": "/host/project/cds.lib",
            "CDS_CDSLIB": "/host/project/cds.lib",
        },
    )
    assert context.environment["CDS_LIB"] == str(cds_lib.resolve())
    assert context.environment["CDS_CDSLIB"] == str(cds_lib.resolve())


def test_resolve_project_requires_default_user_cdslib(tmp_path: Path) -> None:
    root, user_home = _module_root(tmp_path)
    (user_home / "cds.lib").write_text("DEFINE alpha .\n", encoding="utf-8")
    context = resolve_project(
        "alpha",
        root=root,
        base_environment={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            "USER": os.environ.get("USER", "xh"),
            "MODULEPATH": str(root),
        },
    )
    assert context.name == "alpha"
    assert context.cds_lib == (user_home / "cds.lib").resolve()
    assert context.environment["CDS_LIB"] == str(context.cds_lib)
    assert context.environment["CDS_CDSLIB"] == str(context.cds_lib)
    timings = dict(context.timings)
    assert set(timings) == {
        "modulecmd_load_ms",
        "modulecmd_purge_ms",
        "project_resolve_ms",
    }
    assert timings["project_resolve_ms"] >= timings["modulecmd_load_ms"]


def test_resolve_project_reports_missing_default_cdslib(tmp_path: Path) -> None:
    root, _ = _module_root(tmp_path)
    with pytest.raises(RequestValidationError, match="default cds.lib"):
        resolve_project(
            "alpha",
            root=root,
            base_environment={
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "USER": os.environ.get("USER", "xh"),
                "MODULEPATH": str(root),
            },
        )
