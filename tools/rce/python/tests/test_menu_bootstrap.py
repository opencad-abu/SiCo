from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from skill_probe_support import run_virtuoso_source

CAD_ROOT = Path(__file__).resolve().parents[3]


def _load_menu_generator():
    path = CAD_ROOT / "utility/menu_to_skill.py"
    spec = importlib.util.spec_from_file_location("menu_to_skill_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_menu_callbacks_use_the_menu_local_loader() -> None:
    top_loader = (CAD_ROOT / "common/skill/sicoToolRegister.il").read_text(encoding="utf-8")
    legacy_loader = (CAD_ROOT.parent / "loadThisFirst.il").read_text(encoding="utf-8")
    menu = (CAD_ROOT / "utility/menu.il").read_text(encoding="utf-8")
    config = (CAD_ROOT / "utility/menu.toml").read_text(encoding="utf-8")
    generated = (CAD_ROOT / "utility/menu.generated.il").read_text(encoding="utf-8")

    assert "procedure(witMenuToolsRoot" in menu
    assert "procedure(witMenuLoadRelative" in menu
    assert "procedure(witMenuInstalledDataPath" in menu
    assert "procedure(witMenuCacheDataPath" in menu
    assert "procedure(witMenuTryLoadData" in menu
    assert "procedure(witMenuLoadData" in menu
    assert "procedure(witMenuRegenerate" in menu
    assert "procedure(sicoToolRegister" in top_loader
    assert "procedure(cadLoadToolScripts" in top_loader
    assert 'scriptsRoot=cadPath("scripts")' in top_loader
    assert 'sicoToolRegisterVersion="20260922.sico.register.v2"' in top_loader
    assert 'witMenuInitVersion=="20260922.sico.menu.v4"' in top_loader
    assert 'witMenuLoaderVersion="20260922.sico.menu.v4"' in menu
    assert "isCallable('cadLoadRelative)" in top_loader
    assert "isCallable('witMenuLoadRelative)" in menu
    assert "when(witMenuLoadData()" in menu
    assert "result=errset(load(path) nil)" in menu
    assert "when(and(helper config output" in menu
    assert "status=system(cmd)" in menu
    assert "status==0" in menu
    assert 'output=witMenuCacheDataPath()' in menu
    assert 'output=strcat(witMenuRoot "/menu.generated.il")' not in menu
    assert "scripts/sicoAutoLoad.il" in legacy_loader
    assert "(defun" not in legacy_loader
    assert "cadLoadRelative" not in config
    assert "cadLoadRelative" not in generated
    assert config.count('text = "DSPF Analyzer..."') == 2
    assert config.count("(rceDspfAnalyzerChooseFile)") == 2
    assert generated.count("(witMenuLoadRelative") == config.count("(witMenuLoadRelative")
    assert generated.count('"aiAssistantAgent" "Silicon Copilot"') == 3
    assert generated.count('"dspfAnalyzer" "DSPF Analyzer..."') == 2
    assert generated.count("(rceDspfAnalyzerChooseFile)") == 2
    assert config.count("ai/skill++/AI.ils") == 3
    assert config.count('text = "AI Assistant"') == 3
    assert "AI Assistant [Codex]" not in config
    assert generated.count('"aiAssistant" "AI Assistant"') == 3
    assert generated.count("(cadDisplayAiAssistant)") == 3
    assert config.count('name = "lsfLoadMonitor"') == 3
    assert config.count('text = "LSF Load Monitor"') == 3
    assert generated.count('"lsfLoadMonitor" "LSF Load Monitor"') == 3
    assert generated.count("(SICO_lsfMonitorStart)") == 3
    monitor_callback = (
        '(witMenuLoadRelative "common/skill/SICO_environment.il") '
        '(witMenuLoadRelative "common/skill/SICO_installation.il") '
        '(witMenuLoadRelative "common/skill/SICO_toml.il") '
        '(witMenuLoadRelative "common/skill/SICO_guiProtocol.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsf.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsfSelection.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsfProtocol.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsfProcess.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsfCommands.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsfHosts.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsfQueues.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsfChoices.il") '
        '(witMenuLoadRelative "common/skill/SICO_lsfMonitor.il") '
        "(when (isCallable 'SICO_lsfMonitorStart) (SICO_lsfMonitorStart))"
    )
    assert config.count('"lefGenerator"') == 2
    assert config.count('text = "LEF Generator"') == 2
    assert config.count("lef/skill++/LEF.ils") == 2
    assert generated.count('"lefGenerator" "LEF Generator"') == 2
    assert generated.count("(cadDisplayLefForm)") == 2
    assert config.count('name = "cadChangeTechLib"') == 3
    assert config.count('text = "Change Tech Library..."') == 3
    assert config.count("utility/skill/cadChangeTechLibForm.il") == 3
    assert generated.count('"cadChangeTechLib" "Change Tech Library..."') == 3
    assert generated.count("(cadChangeTechLibGui)") == 3
    assert config.count('name = "nl2view"') == 3
    assert config.count('text = "Netlist to View..."') == 3
    assert generated.count('"nl2view" "Netlist to View..."') == 3
    assert generated.count("(cadDisplayNl2ViewForm)") == 3
    generator = _load_menu_generator()
    with (CAD_ROOT / "utility/menu.toml").open("rb") as handle:
        menu_data = generator.tomllib.load(handle)
    for section in ("ciw", "layout", "schematic"):
        assert menu_data[section]["items"][0]["name"] == "aiAssistantAgent"
        assert menu_data[section]["items"][1]["name"] == "aiAssistant"
        assert menu_data[section]["items"][2]["name"] == "lsfLoadMonitor"
        assert menu_data[section]["items"][2]["callback"] == monitor_callback

    reload_body = menu.split("procedure(witMenuReload", 1)[1].split(
        "procedure(witMenuRegenerate", 1
    )[0]
    assert "witMenuLoadData()" in reload_body
    assert "witMenuGenerateData" not in reload_body


def test_menu_trigger_registration_uses_cadence_registry() -> None:
    menu = (CAD_ROOT / "utility/menu.il").read_text(encoding="utf-8")
    probe = menu.split("procedure(witMenuTriggerRegisteredP", 1)[1].split(
        "procedure(witMenuRegisterTriggers", 1
    )[0]
    register = menu.split("procedure(witMenuRegisterTriggers", 1)[1].split(
        "procedure(witMenuReload", 1
    )[0]

    assert "appInfo=deGetAppInfo(viewType)" in probe
    assert "appInfo->userPostInstallTrigList" in probe
    assert "witMenuTriggerRegisteredP(viewType 'witMenuInstallLayout)" in register
    assert "witMenuTriggerRegisteredP(viewType 'witMenuInstallSchematic)" in register
    assert "witMenuRegisteredLayoutViewTypes" not in menu
    assert "witMenuRegisteredSchematicViewTypes" not in menu


def test_production_bootstrap_source_has_a_non_skill_suffix() -> None:
    source = CAD_ROOT.parent / "scripts/sicoAutoLoad.il"
    bootstrap = source.read_text(encoding="utf-8")

    assert source.suffix == ".il"
    assert 'sicoAutoLoadVersion="20260921.sico.autoload.v1"' in bootstrap
    assert "procedure(sicoAutoLoadSkill()" in bootstrap
    assert 'registerFile=strcat(toolsRoot "/common/skill/sicoToolRegister.il")' in bootstrap


def test_documented_recursive_loader_has_balanced_parentheses() -> None:
    readme = (CAD_ROOT / "utility/README.md").read_text(encoding="utf-8")
    skill = readme.split("procedure(vtsoCustom()", 1)[1].split("```", 1)[0]
    skill = "procedure(vtsoCustom()" + skill

    assert skill.count("(") == skill.count(")")
    assert 'scriptsDir=simplifyFilename(strcat(sklDir "/scripts") t)' in skill
    assert "simplifyFilename(tmp t)!=scriptsDir" in skill


def test_checked_in_menu_data_matches_the_generator(tmp_path: Path) -> None:
    generated = tmp_path / "new-cache" / "menu.generated.il"
    subprocess.run(
        [
            sys.executable,
            str(CAD_ROOT / "utility/menu_to_skill.py"),
            str(CAD_ROOT / "utility/menu.toml"),
            str(generated),
        ],
        check=True,
    )

    expected = (CAD_ROOT / "utility/menu.generated.il").read_text(encoding="utf-8")
    assert generated.read_text(encoding="utf-8") == expected


def test_generator_accepts_matching_read_only_published_data(tmp_path: Path) -> None:
    published = tmp_path / "published"
    published.mkdir()
    output = published / "menu.generated.il"
    output.write_text(
        (CAD_ROOT / "utility/menu.generated.il").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    before = output.stat().st_mtime_ns
    output.chmod(0o444)
    published.chmod(0o555)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(CAD_ROOT / "utility/menu_to_skill.py"),
                str(CAD_ROOT / "utility/menu.toml"),
                str(output),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert output.stat().st_mtime_ns == before
    finally:
        published.chmod(0o755)
        output.chmod(0o644)


def test_generator_cli_reports_write_failure_without_traceback(
    monkeypatch, capsys,
) -> None:
    generator = _load_menu_generator()

    def fail(*_args) -> None:
        raise PermissionError(13, "Permission denied", "/readonly/menu.generated.il")

    monkeypatch.setattr(generator, "generate", fail)
    assert generator.main(["menu_to_skill.py", "menu.toml", "menu.generated.il"]) == 1
    stderr = capsys.readouterr().err
    assert stderr.startswith("menu generation failed: ")
    assert "/readonly/menu.generated.il" in stderr
    assert "Traceback" not in stderr


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence Virtuoso probe",
)
def test_lsf_monitor_callback_loads_with_virtuoso(
    tmp_path: Path,
) -> None:
    virtuoso = shutil.which(os.environ.get("RCE_VIRTUOSO", "virtuoso"))
    if virtuoso is None:
        pytest.skip("virtuoso is unavailable")
    install = tmp_path / "install"
    install.symlink_to(CAD_ROOT.parent, target_is_directory=True)
    replay = tmp_path / "lsf-monitor-menu.il"
    log = tmp_path / "virtuoso.log"
    replay.write_text(
        "\n".join(
            (
                f'setShellEnvVar("CAD_HOME" "{install}")',
                f'load("{CAD_ROOT.parent / "scripts/sicoAutoLoad.il"}")',
                "witMenuRegisterTriggers()",
                "witMenuRegisterTriggers()",
                'entry=assoc("lsfLoadMonitor" cadddr(witMenuCIW))',
                'witMenuLoadRelative("common/skill/SICO_toml.il")',
                'witMenuLoadRelative("common/skill/SICO_guiProtocol.il")',
                'witMenuLoadRelative("common/skill/SICO_lsf.il")',
                'witMenuLoadRelative("common/skill/SICO_lsfSelection.il")',
                'witMenuLoadRelative("common/skill/SICO_lsfProtocol.il")',
                'witMenuLoadRelative("common/skill/SICO_lsfProcess.il")',
                'witMenuLoadRelative("common/skill/SICO_lsfCommands.il")',
                'witMenuLoadRelative("common/skill/SICO_lsfHosts.il")',
                'witMenuLoadRelative("common/skill/SICO_lsfQueues.il")',
                'witMenuLoadRelative("common/skill/SICO_lsfChoices.il")',
                'witMenuLoadRelative("common/skill/SICO_lsfMonitor.il")',
                "cadMenuMonitorCalls=0",
                "procedure(SICO_lsfMonitorStart(@optional queue)",
                "  cadMenuMonitorCalls=add1(cadMenuMonitorCalls) t)",
                'result=evalstring(strcat("(progn " caddr(entry) ")"))',
                'when(and(result cadMenuMonitorCalls==1',
                '         cadr(entry)=="LSF Load Monitor")',
                '  printf("CAD_LSF_MONITOR_MENU_OK\\n"))',
                "exit()",
                "",
            )
        ),
        encoding="utf-8",
    )
    output = run_virtuoso_source(replay.read_text(), tmp_path, log_path=log)

    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    assert "DEBASE-102203" not in output
    assert "CAD_LSF_MONITOR_MENU_OK" in output
