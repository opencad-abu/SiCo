from collections import defaultdict

from skill_load_fixtures import (
    CAD_ROOT,
    FUNCTION_RE,
    _skill_sources,
)
from skill_test_support import read_skill_source


def test_il_global_functions_are_unique() -> None:
    definitions: dict[str, list[str]] = defaultdict(list)
    for path in _skill_sources():
        text = path.read_text(encoding="utf-8")
        for name in FUNCTION_RE.findall(text):
            definitions[name].append(str(path.relative_to(CAD_ROOT)))

    duplicates = {name: paths for name, paths in definitions.items() if len(paths) > 1}
    assert duplicates == {}


def test_function_il_files_have_version_guards() -> None:
    # Preserve the historical collection name; applicability belongs to the
    # shared audit rule, including parent-owned fragments and worker lifecycles.
    from skill_load_audit import check_repository

    errors, _ = check_repository(CAD_ROOT.parent)
    assert errors == []


def test_ciw_menu_is_initialized_before_custom_install() -> None:
    text = read_skill_source(CAD_ROOT / "utility/menu.il")
    start = text.index("procedure(witMenuInstallCIW")
    end = text.index("procedure(witMenuInstallLayout", start)
    install = text[start:end]

    assert install.index("ciwMenuInit()") < install.index("witMenuInstallOnWindow(")


def test_frontend_versions_force_updated_callbacks_to_reload() -> None:
    for entry, callback, prefix in (
        ("rce/skill++/RCE.ils", "rce/skill++/RCECB.ils", "rce"),
        ("lvs/skill++/LVS.ils", "lvs/skill++/LVSCB.ils", "lvs"),
        ("drc/skill++/DRC.ils", "drc/skill++/DRCCB.ils", "drc"),
    ):
        entry_text = (CAD_ROOT / entry).read_text(encoding="utf-8")
        callback_text = (CAD_ROOT / callback).read_text(encoding="utf-8")
        assert f"isCallable('{prefix}LoaderRevision)" in entry_text
        assert f"procedure({prefix}LoaderRevision(" in entry_text
        assert (
            f"{prefix}FrontendRevision()=={prefix}LoaderRevision()" in entry_text
        )
        assert f"procedure({prefix}FrontendRevision(" in callback_text


def test_flow_loaders_load_the_widget_before_ipc_callbacks() -> None:
    for entry, callback in (
        ("rce/skill++/RCE.ils", '"/skill++/RCECB.ils"'),
        ("lvs/skill++/LVS.ils", '"/skill++/LVSRUN.ils"'),
        ("drc/skill++/DRC.ils", '"/skill++/DRCRUN.ils"'),
    ):
        text = (CAD_ROOT / entry).read_text(encoding="utf-8")
        assert "isCallable('GUI_flowLogOpen)" in text
        assert text.index('"/skill/UI_flowLog.il"') < text.index(callback)


def test_profile_and_lef_adapter_do_not_accept_legacy_local_host() -> None:
    model = (CAD_ROOT / "common/python/cadprofile/model.py").read_text(
        encoding="utf-8"
    )
    adapter = read_skill_source(CAD_ROOT / "lef/skill++/LEFPROFILE.ils")

    assert 'run.get("run_type") == "Local Host"' not in model
    assert 'lefProfileValue(data "run.run_type" "Current Host")' in adapter
    assert '"run_type" "Current Host"' not in adapter


def test_flow_environment_fallbacks_are_isolated() -> None:
    cases = (
        (
            "drc",
            ("drc/bin/drc", "drc/skill++/DRCRUN.ils", "drc/python/drcpy/runner.py"),
            ("LVS_PYTHON", "RCE_PYTHON", "LVS_ORIG_LD_LIBRARY_PATH", "RCE_ORIG_LD_LIBRARY_PATH"),
        ),
        (
            "lvs",
            ("lvs/bin/lvs", "lvs/skill++/LVSRUN.ils", "lvs/python/lvspy/runner.py"),
            ("DRC_PYTHON", "RCE_PYTHON", "DRC_ORIG_LD_LIBRARY_PATH", "RCE_ORIG_LD_LIBRARY_PATH"),
        ),
        (
            "lef",
            ("lef/bin/lefgen", "lef/skill++/LEFRUN.ils"),
            ("DRC_PYTHON", "LVS_PYTHON", "RCE_PYTHON"),
        ),
    )
    for _flow, paths, forbidden in cases:
        source = "\n".join(
            (CAD_ROOT / path).read_text(encoding="utf-8") for path in paths
        )
        for name in forbidden:
            assert name not in source


def test_flow_project_directories_do_not_cross_fallback() -> None:
    cases = (
        ("drc/skill++/DRCCFG.ils", "DRC_DB_DIR", ("LVS_DB_DIR", "RCE_DB_DIR")),
        ("drc/skill++/DRCCB.ils", "DRC_DB_DIR", ("LVS_DB_DIR", "RCE_DB_DIR")),
        ("lvs/skill++/LVSCFG.ils", "LVS_DB_DIR", ("DRC_DB_DIR", "RCE_DB_DIR")),
        ("lvs/skill++/LVSCB.ils", "LVS_DB_DIR", ("DRC_DB_DIR", "RCE_DB_DIR")),
        ("lef/skill++/LEFCFG.ils", "LEF_DB_DIR", ("RCE_RUN_ROOT", "RCE_DB_DIR")),
    )
    for path, expected, forbidden in cases:
        source = (CAD_ROOT / path).read_text(encoding="utf-8")
        assert expected in source
        for name in forbidden:
            assert name not in source
