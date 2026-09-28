from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_test_support import common_source_loads

CAD_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_lsf_signal_result_semantics_with_dbaccess(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    completed = subprocess.run(
        [dbaccess],
        input="\n".join(
            (
                "procedure(CAD_lsfProbeSignalSucceededP(signalResult) "
                "and(signalResult car(signalResult)==t))",
                "when(and(!CAD_lsfProbeSignalSucceededP(nil) "
                "!CAD_lsfProbeSignalSucceededP(list(nil)) "
                "CAD_lsfProbeSignalSucceededP(list(t))) "
                'printf("CAD_LSF_SIGNAL_RESULT_OK\\n"))',
                "exit()",
            )
        ),
        text=True,
        capture_output=True,
        timeout=40,
        check=False,
        cwd=tmp_path,
    )
    output = completed.stdout + completed.stderr

    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "CAD_LSF_SIGNAL_RESULT_OK" in output


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_lsf_host_discovery_callbacks_with_dbaccess(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    bqueues = tmp_path / "fake-bqueues"
    bqueues.write_text(
        "#!/bin/sh\n"
        "# LSF reports interactive capability in the queue scheduling policies.\n"
        'case "$2" in\n'
        "  batch) echo 'SCHEDULING POLICIES:  FAIRSHARE NO_INTERACTIVE' ;;\n"
        "  interactive) echo 'SCHEDULING POLICIES:  FAIRSHARE ONLY_INTERACTIVE' ;;\n"
        "  *) echo 'SCHEDULING POLICIES:  FAIRSHARE' ;;\n"
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
    )
    bqueues.chmod(0o755)
    toml_helper = CAD_ROOT / "common/skill/SICO_toml.il"
    protocol_helper = CAD_ROOT / "common/skill/SICO_guiProtocol.il"
    skill = "\n".join(
        (
            f'setShellEnvVar("SICO_HOME" "{CAD_ROOT.parent}")',
            f'load("{toml_helper}")',
            f'load("{protocol_helper}")',
            common_source_loads("lsf"),
            "defstruct(mockLsfRunType value)",
            "defstruct(mockLsfField items value enabled defValue)",
            "defstruct(mockLsfForm runType queueName srvName "
            "cadLsfQueueDiscoveryCid cadLsfDiscoveryCid)",
            "procedure(hiIsForm(form) t)",
            "mockLsfSerial=40",
            "procedure(SICO_lsfBeginProcess(command dataHandler errHandler postFunc) "
            "mockLsfCommand=command "
            "mockLsfSerial=mockLsfSerial+1 mockLsfSerial)",
            'setShellEnvVar("EXT_LSF_QUEUE" "legacy")',
            'normalDiscoveryCommand=SICO_lsfDiscoveryCommand('
            '"hosts" "normal" "/tmp/cad-lsf-hosts.tsv")',
            'queueDiscoveryCommand=SICO_lsfDiscoveryCommand('
            '"queues" nil "/tmp/cad-lsf-queues.tsv")',
            'runType=make_mockLsfRunType(?value "LSF Farm")',
            'queue=make_mockLsfField(?items list("legacy") ?value "legacy" '
            '?enabled t ?defValue "")',
            'server=make_mockLsfField(?items list("legacyHost") '
            '?value "legacyHost" ?enabled t ?defValue "localhost")',
            "form=make_mockLsfForm(?runType runType ?queueName queue "
            "?srvName server)",
            "SICO_lsfChoicesCB(form)",
            "queueCid=form~>cadLsfQueueDiscoveryCid",
            "queueCommand=mockLsfCommand",
            'SICO_guiProtocolWrite('
            'cadLsfQueueDiscoveryState[queueCid][\'resultFile] '
            'list(list("QUEUE" "normal") list("QUEUE" "batch")) '
            '"ready" "1")',
            "SICO_lsfQueueDiscoveryPostFunc(queueCid 0)",
            "hostCid=form~>cadLsfDiscoveryCid",
            "normalHostCommand=mockLsfCommand",
            'SICO_guiProtocolWrite('
            'cadLsfDiscoveryState[hostCid][\'resultFile] '
            'list(list("SELECTED_QUEUE" "normal") '
            'list("HOST" "node01") list("HOST" "node02")) '
            '"partial" "1")',
            "SICO_lsfDiscoveryPostFunc(hostCid 0)",
            "autoSelectionValid=SICO_lsfSelectionValidP(form)",
            'autoValidation=SICO_lsfValidateSelection(form "TEST")',
            'server~>value="node02"',
            "explicitSelectionValid=SICO_lsfSelectionValidP(form)",
            'explicitValidation=SICO_lsfValidateSelection(form "TEST")',
            'server~>value="staleHost"',
            "staleSelectionRejected=!SICO_lsfSelectionValidP(form)",
            'staleValidationRejected=!SICO_lsfValidateSelection(form "TEST")',
            'server~>value=SICO_lsfAutoHost()',
            'preferredQueue=make_mockLsfField(?items list("") '
            '?value "" ?enabled nil ?defValue "")',
            'preferredServer=make_mockLsfField(?items list("localhost") '
            '?value "localhost" ?enabled nil ?defValue "localhost")',
            'preferredForm=make_mockLsfForm(?runType runType '
            '?queueName preferredQueue ?srvName preferredServer)',
            'SICO_lsfSetPreferredValues(preferredForm "savedq" "savedh")',
            'preferredImmediate=and(preferredQueue~>value=="" '
            '!member("savedq" preferredQueue~>items) '
            'preferredServer~>value=="localhost" '
            '!member("savedh" preferredServer~>items))',
            "preferredQueueCid=SICO_lsfStartQueueDiscovery(preferredForm)",
            'SICO_guiProtocolWrite('
            'cadLsfQueueDiscoveryState[preferredQueueCid][\'resultFile] '
            'list(list("QUEUE" "normal")) "ready" "1")',
            "SICO_lsfQueueDiscoveryPostFunc(preferredQueueCid 0)",
            'preferredAfterQueue=and(preferredQueue~>value=="normal" '
            '!member("savedq" preferredQueue~>items))',
            "preferredHostCid=preferredForm~>cadLsfDiscoveryCid",
            'SICO_guiProtocolWrite('
            'cadLsfDiscoveryState[preferredHostCid][\'resultFile] '
            'list(list("SELECTED_QUEUE" "normal") '
            'list("HOST" "node01")) "ready" "1")',
            "SICO_lsfDiscoveryPostFunc(preferredHostCid 0)",
            'preferredAfterHost=and('
            'preferredServer~>value==SICO_lsfAutoHost() '
            '!member("savedh" preferredServer~>items))',
            'queue~>value="batch"',
            "SICO_lsfQueueChoicesCB(form)",
            "batchCid=form~>cadLsfDiscoveryCid",
            "batchHostCommand=mockLsfCommand",
            "batchResultFile=cadLsfDiscoveryState[batchCid]['resultFile]",
            'queue~>value="normal"',
            "SICO_lsfQueueChoicesCB(form)",
            "latestCid=form~>cadLsfDiscoveryCid",
            'SICO_guiProtocolWrite(batchResultFile '
            'list(list("SELECTED_QUEUE" "batch") list("HOST" "stale")) '
            '"ready" "1")',
            "SICO_lsfDiscoveryPostFunc(batchCid 0)",
            'SICO_guiProtocolWrite('
            'cadLsfDiscoveryState[latestCid][\'resultFile] '
            'list(list("SELECTED_QUEUE" "normal") '
            'list("HOST" "node01")) "ready" "1")',
            "SICO_lsfDiscoveryPostFunc(latestCid 0)",
            'fallbackRun=make_mockLsfRunType(?value "LSF Farm")',
            'fallbackQueue=make_mockLsfField(?items list("saved") '
            '?value "saved" ?enabled t ?defValue "saved")',
            'fallbackServer=make_mockLsfField(?items list("legacyHost") '
            '?value "legacyHost" ?enabled t ?defValue "localhost")',
            "fallbackForm=make_mockLsfForm(?runType fallbackRun "
            "?queueName fallbackQueue ?srvName fallbackServer)",
            "fallbackQueueCid=SICO_lsfStartQueueDiscovery(fallbackForm)",
            "SICO_lsfQueueDiscoveryPostFunc(fallbackQueueCid 1)",
            "fallbackQueueValue=fallbackQueue~>value",
            "fallbackQueueWasEnabled=fallbackQueue~>enabled",
            "fallbackHostItems=fallbackServer~>items",
            "fallbackHostValue=fallbackServer~>value",
            "fallbackHostWasEnabled=fallbackServer~>enabled",
            'invalidQueue=make_mockLsfField(?items list("stale") '
            '?value "stale" ?enabled t ?defValue "stale")',
            'invalidServer=make_mockLsfField(?items list("staleHost") '
            '?value "staleHost" ?enabled t ?defValue "staleHost")',
            'invalidForm=make_mockLsfForm(?runType fallbackRun '
            '?queueName invalidQueue ?srvName invalidServer)',
            "invalidQueueCid=SICO_lsfStartQueueDiscovery(invalidForm)",
            'SICO_guiProtocolWrite('
            'cadLsfQueueDiscoveryState[invalidQueueCid][\'resultFile] '
            'list(list("QUEUE" "normal")) "ready" "2")',
            "SICO_lsfQueueDiscoveryPostFunc(invalidQueueCid 0)",
            'invalidResultOk=and(invalidQueue~>items==list("") '
            'invalidQueue~>value=="" !invalidQueue~>enabled '
            'invalidServer~>items==list(SICO_lsfAutoHost()) '
            'invalidServer~>value==SICO_lsfAutoHost() '
            '!invalidServer~>enabled)',
            'fallbackRun~>value="Current Host"',
            "serialBeforeLocal=mockLsfSerial",
            "SICO_lsfChoicesCB(fallbackForm)",
            'setShellEnvVar("SICO_LSF_BSUB" "/opt/lsf/bin/bsub")',
            f'setShellEnvVar("SICO_LSF_BQUEUES" "{bqueues}")',
            'emptyQueue=SICO_lsfWrapCommand("/bin/true" "LSF Farm" '
            '"" SICO_lsfAutoHost() "8")',
            'wrapped=SICO_lsfWrapCommand("env X=1 /bin/true" "LSF Farm" '
            '"express" "node02" "8")',
            'automatic=SICO_lsfWrapCommand("/bin/true" "LSF Farm" '
            '"express" SICO_lsfAutoHost() "8")',
            'interactive=SICO_lsfWrapCommand("/bin/true" "LSF Farm" '
            '"interactive" "node02" "8" nil "/tmp/rce.launch.log")',
            'batched=SICO_lsfWrapCommand("/bin/true" "LSF Farm" '
            '"batch" "node02" "8" "job-1" "/tmp/rce.launch.log")',
            'unlogged=SICO_lsfWrapCommand("/bin/true" "LSF Farm" '
            '"batch" "node02" "8")',
            'local=SICO_lsfWrapCommand("/bin/true" "Current Host" "express" '
            '"node02" "8")',
            'legacyLocal=SICO_lsfWrapCommand("/bin/true" "Local Host" '
            '"express" "node02" "8")',
            'if(and(queueCid==41 '
            'index(queueCommand "common/python/sico-lsf") '
            'index(queueCommand " queues") hostCid==42 '
            'index(normalHostCommand "common/python/sico-lsf") '
            'index(normalHostCommand " hosts") '
            'index(normalHostCommand "--queue \'normal\'") '
            'index(normalHostCommand "--format tsv --output") '
            'autoSelectionValid autoValidation explicitSelectionValid '
            'explicitValidation staleSelectionRejected staleValidationRejected '
            'preferredImmediate preferredAfterQueue preferredAfterHost '
            'batchCid==45 latestCid==46 '
            'index(batchHostCommand "common/python/sico-lsf") '
            'index(batchHostCommand " hosts") '
            'index(batchHostCommand "batch") '
            'server~>items==list(SICO_lsfAutoHost() "node01") '
            'server~>value==SICO_lsfAutoHost() '
            'server~>enabled !cadLsfDiscoveryState[batchCid] '
            'fallbackQueueCid==47 '
            'fallbackQueueValue=="" !fallbackQueueWasEnabled '
            'fallbackHostItems==list(SICO_lsfAutoHost()) '
            'fallbackHostValue==SICO_lsfAutoHost() !fallbackHostWasEnabled '
            'invalidQueueCid==48 invalidResultOk '
            '!fallbackQueue~>enabled !fallbackServer~>enabled '
            'mockLsfSerial==serialBeforeLocal '
            '!emptyQueue '
            'local=="/bin/true" legacyLocal=="/bin/true" '
            'index(wrapped "-q \'express\'") '
            'index(wrapped "-Is") !index(wrapped " -K") '
            '!index(wrapped " >> ") '
            'index(interactive "-q \'interactive\'") index(interactive "-Is") '
            '!index(interactive " -K") !index(interactive " >> ") '
            'index(batched "-K") !index(batched " -Is") '
            'index(batched "-J \'job-1\'") index(batched " >> ") '
            'index(batched "2>&1") index(batched " -q \'batch\'") '
            'index(unlogged "-K") !index(unlogged " >> ") '
            'index(wrapped "-m \'node02\'") index(wrapped "-n \'8\'") '
            '!index(automatic " -m ") '
            'SICO_lsfSelectedHost(SICO_lsfAutoHost())=="" '
            'SICO_lsfMaxCpu(list("2" "16" "4"))=="16" '
            'index(normalDiscoveryCommand "common/python/sico-lsf") '
            'index(normalDiscoveryCommand " hosts") '
            'index(normalDiscoveryCommand "--queue \'normal\'") '
            '!index(normalDiscoveryCommand " -c ") '
            'index(queueDiscoveryCommand " queues")) '
            'then printf("RCE_LSF_DISCOVERY_OK\\n"))',
            'printf("RCE_LSF_NORMAL_COMMAND_BEGIN\\n%s\\n" '
            'normalDiscoveryCommand)',
            'printf("RCE_LSF_NORMAL_COMMAND_END\\n")',
            'printf("RCE_LSF_BATCH_COMMAND_BEGIN\\n%s\\n" '
            'normalDiscoveryCommand)',
            'printf("RCE_LSF_BATCH_COMMAND_END\\n")',
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess], input=skill, text=True, capture_output=True, timeout=30, check=False,
        cwd=tmp_path,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output, output
    assert "RCE_LSF_DISCOVERY_OK" in output, output

    def command_between(begin: str, end: str) -> str:
        captured = output.split(begin, maxsplit=1)[1].split(end, maxsplit=1)[0]
        lines = captured.strip().splitlines()
        while lines and lines[-1].strip() in {"t", ">"}:
            lines.pop()
        return "\n".join(lines)

    normal_command = command_between(
        "RCE_LSF_NORMAL_COMMAND_BEGIN\n", "RCE_LSF_NORMAL_COMMAND_END"
    )
    batch_command = command_between(
        "RCE_LSF_BATCH_COMMAND_BEGIN\n", "RCE_LSF_BATCH_COMMAND_END"
    )

    for command in (normal_command, batch_command):
        assert '"' not in command
        assert "(" not in command
        assert " -c " not in command
        wrapped = (
            "/App/cadence/ic618.350/tools/bin/64bit/cdsServIpc "
            f'-c 38474 -n 14 -r 15 -x "{command}"'
        )
        syntax = subprocess.run(
            ["/bin/sh", "-n", "-c", wrapped],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert syntax.returncode == 0, syntax.stderr

    assert "common/python/sico-lsf" in normal_command
    assert "--format tsv --output" in normal_command
    assert "--queue 'normal'" in normal_command
