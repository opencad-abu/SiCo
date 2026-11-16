# Original test bodies retain their imports and Qt lifetime references.
# ruff: noqa: F401, F841, I001
"""router project startup for the opt-in source acceptance."""

from __future__ import annotations

import json
from pathlib import Path

from router_project_config import CAD


def _quote(value):
    return json.dumps(str(value))

def _startup_source(evidence, case):
    lines = [
    ]
    if case.get("context"):
        context = Path(case["context"])
        lines += [
            f"unless(loadContext({_quote(context)} t) error(\"Context load failed\"))",
            f"unless(callInitProc({_quote(context.stem)})"
            " error(\"Context initialization failed\"))",
        ]
    else:
        lines += [f"load({_quote(CAD / 'sico/skill++/SICO.ils')})"]
    for hidden in case.get("hidden_sources", []):
        lines.append(f"unless(!isFile({_quote(hidden)})"
                     f' error(strcat("Source tree is visible: " {_quote(hidden)})))')
    lines += [
        f"acceptLabel={_quote(case['label'])}",
        f"acceptLib=ddGetObj({_quote(case['lib'])})",
        'unless(acceptLib error("Authorized project library is missing"))',
        "acceptLibPath=acceptLib->readPath",
        "acceptTech=car(errset(techGetTechLibName(acceptLib) nil))",
        'unless(acceptLibPath error("Authorized project library resolves to no path"))',
        "procedure(acceptBool(value) if(value then \"true\" else \"false\"))",
        "procedure(acceptText(@rest items)",
        "  let((out) out=\"\" foreach(item items out=strcat(out item \"\\n\")) out))",
        "procedure(acceptWrite(path text)",
        "  let((port) port=outfile(path \"w\") when(port fprintf(port \"%s\" text) close(port))))",
        "unless(isCallable('sicoShow) error(\"Silicon Copilot loader is incomplete\"))",
        "acceptBinding1=makeTable(\"acceptBinding\" nil)"
        " acceptBinding1['entry]=\"router-acceptance\"",
        "acceptBinding2=makeTable(\"acceptBinding\" nil)"
        " acceptBinding2['entry]=\"router-acceptance\"",
        "acceptCid1=sicoShow(nil acceptBinding1 t)",
        "acceptCid2=sicoShow(nil acceptBinding2 t)",
        'unless(and(acceptCid1 acceptCid2 acceptCid1!=acceptCid2'
        " sicoInstanceBridge!=acceptCid1 sicoInstanceBridge!=acceptCid2"
        " sicoBindings[acceptCid1] && sicoBindings[acceptCid2]"
        " sicoBindings[acceptCid1]['desktopSession]"
        " && sicoBindings[acceptCid2]['desktopSession]"
        " sicoBindings[acceptCid1]['generation]==sicoBindings[acceptCid2]['generation]"
        " eq(sicoBindings[acceptCid1]['targets]"
        " sicoBindings[sicoInstanceBridge]['targets])"
        " eq(sicoBindings[acceptCid2]['targets]"
        " sicoBindings[sicoInstanceBridge]['targets]))"
        ' error("Instance bridge ownership mismatch"))',
        f"acceptWrite({_quote(evidence / 'instance.json')} acceptText(",
        '  strcat("label=" acceptLabel)',
        "  strcat(\"instance_id=\" sicoBindings[sicoInstanceBridge]['instanceId])",
        "  strcat(\"generation=\" sicoBindings[sicoInstanceBridge]['generation])",
        '  strcat("bridge_id=" or(sicoBindings[sicoInstanceBridge][\'bridgeId] ""))',
        "  strcat(\"lib=\" acceptLib->name)",
        "  strcat(\"lib_path=\" acceptLibPath)",
        '  strcat("technology=" or(acceptTech ""))',
        '  strcat("loader=" if(isCallable(\'sicoLoaderRevision)'
        ' then sicoLoaderRevision() else "context-runtime"))',
        "  strcat(\"launcher=\" sicoLauncherRevision())",
        '  strcat("ai_agent_root=" or(sicoRoot ""))',
        '  strcat("context=" or(getShellEnvVar("CAD_AI_CONTEXT") ""))',
        '  strcat("desktop1=" sprintf(nil "%L" acceptCid1))',
        '  strcat("desktop2=" sprintf(nil "%L" acceptCid2))))',
        "procedure(acceptReconnect()",
        "  when(sicoReconnectBridge()",
        "    acceptBinding3=makeTable(\"acceptBinding\" nil)"
        " acceptBinding3['entry]=\"router-acceptance\"",
        "    acceptCid3=sicoShow(nil acceptBinding3 t)",
        "    when(acceptCid3",
        f"      acceptWrite({_quote(evidence / 'next.json')} acceptText(",
        '        strcat("instance_id=" sicoBindings[acceptCid3][\'instanceId])',
        '        strcat("generation=" sicoBindings[acceptCid3][\'generation])',
        '        strcat("desktop=" sprintf(nil "%L" acceptCid3)))))))',
        "procedure(acceptFinish()",
        "  let((guard counter)",
        "    when(and(acceptCid3 sicoBindings[acceptCid3])",
        "      sicoStopDesktop(acceptCid3))",
        "    guard=sicoInstanceBridge",
        "    when(guard",
        "      sicoDisconnect(guard)",
        "      for(counter 1 10 when(ipcIsAliveProcess(guard) ipcSleep(1))))",
        "    exit()))",
        "procedure(acceptPoll()",
        "  let((closeText)",
        f"    if(isFile({_quote(evidence / 'done')}) then acceptFinish()",
        "    else progn(",
        f"      when(and(isFile({_quote(evidence / 'stop_first')})"
        f" !isFile({_quote(evidence / 'stop1.json')}))",
        "        sicoStopDesktop(acceptCid1)",
        f"        acceptWrite({_quote(evidence / 'stop1.json')} acceptText(\"requested\")))",
        f"      when(and(isFile({_quote(evidence / 'check_b')})"
        f" !isFile({_quote(evidence / 'close1.json')})"
        " !sicoBindings[acceptCid1])",
        "        closeText=acceptText(",
        '          strcat("first_gone=" acceptBool(!sicoBindings[acceptCid1]))',
        "          strcat(\"second_alive=\" acceptBool(and(sicoBindings[acceptCid2]"
        " sicoBindings[acceptCid2]['desktopSession] sicoSessionLive(acceptCid2))))",
        "          strcat(\"bridge_alive=\" acceptBool(and(sicoInstanceBridge"
        " ipcIsAliveProcess(sicoInstanceBridge))))",
        "          strcat(\"second_generation=\" sicoBindings[acceptCid2]['generation])",
        "          strcat(\"instance_id=\" sicoBindings[acceptCid2]['instanceId]))",
        f"        acceptWrite({_quote(evidence / 'close1.json')} closeText))",
        f"      when(and(isFile({_quote(evidence / 'reconnect')})"
        f" !isFile({_quote(evidence / 'next.json')})) acceptReconnect())))",
        '  hiRegTimer("acceptPoll()" 1)))',
        "acceptCid3=nil",
        'hiRegTimer("acceptPoll()" 1)',
    ]
    return "\n".join(lines) + "\n"
