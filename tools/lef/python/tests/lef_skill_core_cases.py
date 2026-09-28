from __future__ import annotations

from skill_integration_fixtures import CAD_ROOT, SKILL_FILES, _balanced
from skill_test_support import read_skill_source


def test_lef_skill_sources_are_bounded_and_balanced() -> None:
    for relative in SKILL_FILES:
        source = (CAD_ROOT / relative).read_text(encoding="utf-8")
        assert len(source.splitlines()) <= 300, relative
        assert _balanced(source), relative



def test_lef_frontend_keeps_abstract_commands_out_of_virtuoso() -> None:
    loader = (CAD_ROOT / "lef/skill++/LEF.ils").read_text(encoding="utf-8")
    runner = (CAD_ROOT / "lef/skill++/LEFRUN.ils").read_text(encoding="utf-8")
    replay = (CAD_ROOT / "lef/python/lefpy/replay.py").read_text(encoding="utf-8")

    assert 'lef/python/lef' in runner
    assert "ipcBeginProcess" in runner
    assert "cadDisplayLefForm" in loader
    assert "absSkillMode()" not in loader + runner
    assert '"absSkillMode()"' in replay
    assert '"absExit()"' in replay



def test_lef_frontend_backs_up_the_run_before_starting_ipc() -> None:
    runner = (CAD_ROOT / "lef/skill++/LEFRUN.ils").read_text(encoding="utf-8")
    start = runner.split("procedure(lefStart", 1)[1]

    assert "procedure(lefBackupRunData" in runner
    assert 'tag=$(date +%m-%d-%H-%M-%S)' in runner
    assert r'mv \"$base\" \"$backup\"' in runner
    assert start.index("lefBackupRunData(runDir form)") < start.index(
        "configFile=lefWriteToml(form)"
    )
    assert start.index("lefBackupRunData(runDir form)") < start.index(
        "ipcBeginProcess("
    )
    assert 'strcat("LEF_BACKUP_DONE=1 "' in start



def test_lef_frontend_emits_batch_input_and_step_controls() -> None:
    callback = (CAD_ROOT / "lef/skill++/LEFCB.ils").read_text(encoding="utf-8")
    gui = (CAD_ROOT / "lef/skill++/LEFGUI.ils").read_text(encoding="utf-8")
    loader = (CAD_ROOT / "lef/skill++/LEF.ils").read_text(encoding="utf-8")
    config = (CAD_ROOT / "lef/skill++/LEFCFG.ils").read_text(encoding="utf-8")
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")

    assert '"cell_list_file"' in callback
    assert 'SICO_tomlWriteSection(outFile "steps")' in callback
    assert "form~>runPins~>value" in callback
    assert "form~>runExtract~>value" in callback
    assert "form~>runAbstract~>value" in callback
    assert "!cadOutDirUsableP(form~>outDirPath~>value)" in callback
    assert "form~>layoutView~>value" in callback
    assert "?callback inputCallback" in gui
    assert "hiCreateFileSelectorField" in gui
    assert "'cellListFile" in gui
    assert "'runPins" in gui
    assert "'runType" in base
    assert "'queueName" in base
    assert "'srvName" in base
    assert "'lsfInteractive" not in gui
    assert "Live Log (-Is)" not in gui
    assert "'runCpu" in base
    assert "'lsfMonitor" in base
    assert "defclass(LEFPROFILEGUI (PROFILEGUI OUTPUTGUI HARDWARE) ())" in gui
    assert "makeOutDir(profileGui)" in gui
    assert '"Output Configuration"' in gui
    assert "?name 'runDir" not in gui
    assert "Run Directory:" not in gui
    assert 'SICO_envValue("LEFGEN_OPT_FILE")' in config
    assert 'SICO_envValue("PROJ_LEF_OPTIONS")' not in config
    assert "makeServers(profileGui)" in gui
    assert "makeRunCpus(profileGui)" in gui
    assert "profileGui->servers" in gui
    assert 'lefHardwareGuiRevision()=="20260820.lsf.monitor.v1"' in loader
    assert 'SICO_lsfMonitorRevision()=="20260924.sico.monitor.environment.v3"' in loader
    assert '"/skill/SICO_lsfMonitor.il"' in loader
    assert "SICO_lsfChoicesCB(lefForm)" in gui
    runner = (CAD_ROOT / "lef/skill++/LEFRUN.ils").read_text(encoding="utf-8")
    assert "SICO_lsfWrapCommand(" in runner
    assert "form~>lsfInteractive~>value" not in runner
