from __future__ import annotations

from pathlib import Path

from skill_test_support import read_skill_source

CAD_ROOT = Path(__file__).resolve().parents[3]


def test_shared_lsf_hardware_policy_is_wired_to_all_flows() -> None:
    helper = read_skill_source(CAD_ROOT / "common/skill/SICO_lsf.il")
    callback = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    config = read_skill_source(CAD_ROOT / "rce/skill++/RCECFG.ils")
    gui = read_skill_source(CAD_ROOT / "rce/skill++/RCEGUI.ils")
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")
    entry = read_skill_source(CAD_ROOT / "rce/skill++/RCE.ils")
    assert "procedure(SICO_lsfDiscoveryCommand(mode queue outputPath)" in helper
    assert 'SICO_lsfDiscoveryCommand("hosts" queue path)' in helper
    assert 'SICO_lsfDiscoveryCommand("queues" nil path)' in helper
    assert '" --session-pid "' in helper
    assert "sessionPid=ipcGetPid()" in helper
    assert '" --format tsv --output "' in helper
    assert 'SICO_guiProtocolRead(path allowedKinds)' in helper
    assert 'list("SELECTED_QUEUE" "HOST")' in helper
    assert helper.count('list("ready" "partial")') >= 2
    assert "procedure(CAD_lsfParseQueues" not in helper
    assert "procedure(CAD_lsfParseHosts" not in helper
    assert "SICO_lsfBeginProcess(command 'SICO_lsfDiscoveryDataHandler" in helper
    assert "SICO_lsfBeginProcess(command 'SICO_lsfQueueDiscoveryDataHandler" in helper
    assert "procedure(SICO_lsfDiscoveryPostFunc(cid exitStatus)" in helper
    assert "procedure(SICO_lsfQueueDiscoveryPostFunc(cid exitStatus)" in helper
    assert "cadLsfHostRequestToken" in helper
    assert "cadLsfQueueRequestToken" in helper
    assert "procedure(SICO_lsfUpdateQueueField(form items value)" in helper
    assert "cadLsfQueueUpdateActive" in helper
    assert "unwindProtect(" in helper
    assert "validRequest=and(!state['cancelled]" in helper
    assert "procedure(SICO_lsfCancelFormDiscovery(form)" in helper
    assert "isCallable('ipcSignalProcess)" in helper
    assert "ipcSignalProcess(cid 'TERM)" in helper
    assert "procedure(SICO_lsfSignalSucceededP(signalResult)" in helper
    assert "and(signalResult car(signalResult)==t)" in helper
    assert "unless(SICO_lsfSignalSucceededP(signalResult)" in helper
    assert "ipcKillProcess(cid)" in helper
    assert '"Auto (LSF Scheduler)"' in helper
    assert 'if(interactive then " -Is" else " -K")' in helper
    assert "SICO_lsfQueueInteractiveP(queue)" in helper
    assert 'grep -q NO_INTERACTIVE' in helper
    assert 'command=strcat(command " >> " SICO_shellQuote(logFile) " 2>&1")' in helper
    assert '" -m "' in helper
    assert '" -n "' in helper
    assert "procedure(rceLsfChoicesCB(form)" in callback
    assert "SICO_lsfChoicesCB(form)" in callback
    assert gui.index("SICO_lsfChoicesCB(gui->mainForm)") < gui.index(
        "hiDisplayForm(gui->mainForm)"
    )
    assert "CAD_lsfConfiguredHosts" not in config
    assert "CAD_lsfConfiguredQueues" not in config
    assert "EXT_LSF_QUEUE" not in helper
    assert "EXT_DEF_RUN_SRV" not in helper
    assert "EXT_LSFSRV_LIST" not in helper
    assert 'procedure(SICO_lsfValidateSelection(form title)' in helper
    assert 'procedure(SICO_lsfSelectionValidP(form)' in helper
    assert "?queueCB" in config
    assert 'SICO_lsfRevision()=="20260901.form.lifecycle.v1"' in entry
    assert "procedure(SICO_lsfCancelCommand(runType jobName)" in helper
    assert '" -J "' in helper
    assert "isCallable('SICO_lsfStartHostDiscovery)" in entry
    assert "SICO_lsfMaxCpu(list(form~>lvsCpu~>value form~>extCpu~>value))" in callback
    assert 'list("Current Host" "LSF Farm")' in base
    assert '?defValue "Current Host"' in base
    assert "Live Log (-Is)" not in base
    assert "lsfInteractive" not in base
    assert base.count("?enabled  nil") >= 2
    assert 'form~>runType~>value!="LSF Farm"' in helper

    for config_path in (
        "rce/skill++/RCECFG.ils",
        "drc/skill++/DRCCFG.ils",
        "lvs/skill++/LVSCFG.ils",
        "lvs/skill++/STAGECFG.ils",
        "lef/skill++/LEFCFG.ils",
    ):
        flow_config = (CAD_ROOT / config_path).read_text(encoding="utf-8")
        assert '("Current Host" "LSF Farm")' in flow_config
        assert '?defQueueName' in flow_config
        assert '?queueChoices' in flow_config
        assert 'CAD_lsfConfigured' not in flow_config
        assert 'CAD_lsfDefault' not in flow_config

    lef_gui = read_skill_source(CAD_ROOT / "lef/skill++/LEFGUI.ils")
    assert "makeServers(profileGui)" in lef_gui
    assert '?defValue "Current Host"' in base

    for gui_path in (
        "drc/skill++/DRCGUI.ils",
        "lvs/skill++/LVSGUI.ils",
        "lvs/skill++/STAGEGUI.ils",
        "lef/skill++/LEFGUI.ils",
    ):
        flow_gui = (CAD_ROOT / gui_path).read_text(encoding="utf-8")
        assert "SICO_lsfChoicesCB" in flow_gui
        assert "queue" in flow_gui.lower()

    cpu_fields = {
        "drc/skill++/DRCRUN.ils": "form~>runCpu~>value",
        "lvs/skill++/LVSRUN.ils": "form~>lvsCpu~>value",
        "lvs/skill++/STAGECB.ils": "form~>runCpu~>value",
        "lef/skill++/LEFRUN.ils": "form~>runCpu~>value",
    }
    for run_path, cpu_field in cpu_fields.items():
        flow_run = (CAD_ROOT / run_path).read_text(encoding="utf-8")
        assert "SICO_lsfWrapCommand(" in flow_run
        assert "form~>queueName~>value" in flow_run
        assert "form~>lsfInteractive~>value" not in flow_run
        assert "nil t)" not in flow_run
        assert "jobName t)" not in flow_run
        # Every flow hands its launch log to the wrapper so a queue that
        # cannot stream the job output still writes it to the flow log.
        assert "value nil launchLog)" in flow_run or "jobName launchLog)" in flow_run
        assert cpu_field in flow_run

    rce_single = read_skill_source(CAD_ROOT / "rce/skill++/RCECB.ils")
    rce_batch = read_skill_source(CAD_ROOT / "rce/skill++/RCEBATCH.ils")
    assert "nil launchLog)" in rce_single
    assert "jobName launchLog)" in rce_batch
    assert 'GUI_flowLogOpen("RCE" launchLog)' in rce_single

    for callback_path in (
        "rce/skill++/RCECB.ils",
        "drc/skill++/DRCCB.ils",
        "lvs/skill++/LVSCB.ils",
        "lvs/skill++/STAGECB.ils",
        "lef/skill++/LEFCB.ils",
    ):
        flow_callback = read_skill_source(CAD_ROOT / callback_path)
        assert 'SICO_tomlWriteString(outFile "queue_name"' in flow_callback
        assert "SICO_lsfSelectedHost(form~>srvName~>value)" in flow_callback
        assert "SICO_lsfValidateSelection(form" in flow_callback

    for loader_path in (
        "drc/skill++/DRC.ils",
        "lvs/skill++/LVS.ils",
        "rce/skill++/RCE.ils",
        "lef/skill++/LEF.ils",
    ):
        loader = (CAD_ROOT / loader_path).read_text(encoding="utf-8")
        assert 'SICO_guiProtocolRevision()=="20260814.cadgui.v1"' in loader
        assert 'SICO_lsfRevision()=="20260901.form.lifecycle.v1"' in loader
        assert "isCallable('SICO_lsfValidateSelection)" in loader

    drc_callback = read_skill_source(CAD_ROOT / "drc/skill++/DRCCB.ils")
    drc_runner = (CAD_ROOT / "drc/python/drcpy/runner.py").read_text(
        encoding="utf-8"
    )
    assert 'SICO_tomlWriteString(outFile "cpus" form~>runCpu~>value)' in drc_callback
    assert 'self.cfg.calibre_run_mode("drc")' in drc_runner
    assert '"-turbo"' in drc_runner
