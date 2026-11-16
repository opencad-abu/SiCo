"""Run the host Ask callbacks in SKILL, independently of a graphical desktop."""

import json
import os
import subprocess
from pathlib import Path

import pytest

CAD = Path(__file__).absolute().parents[3]
DB = Path("/software/eda/cadence/installs/IC23.10.130/tools/dfII/bin/dbAccess")
pytestmark = pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")


def run_skill(tmp_path, body, *, expected_error=None):
    source = tmp_path / "host-callbacks.il"
    source.write_text(
        f'load({json.dumps(str(CAD / "ai/skill/AI_protocol.il"))})\n'
        + f'load({json.dumps(str(CAD / "ai/skill/AI_pdk_core.il"))})\n'
        + f'load({json.dumps(str(CAD / "ai/skill/AI_datahub_contracts.il"))})\n'
        + f'load({json.dumps(str(CAD / "ai/skill/AI_datahub.il"))})\n'
        + f'load({json.dumps(str(CAD / "sico/skill/SICO_binding.il"))})\n'
        + f'load({json.dumps(str(CAD / "sico/skill/SICO_desktop.il"))})\n'
        + f'load({json.dumps(str(CAD / "sico/skill/SICO_ask_queue.il"))})\n'
        + f'load({json.dumps(str(CAD / "sico/skill/SICO_quick.il"))})\n'
        + r'''
procedure(sicoQuickSend(qsComposer qsKind @optional qsMessage)
  qsComposer['notices]=append1(qsComposer['notices] qsKind)
  qsComposer['message]=qsMessage)
procedure(hostForm(hfKey hfChild)
  let((hfForm hfTarget)
    hfForm=makeTable("hostForm" nil) hfTarget=makeTable("hostTarget" nil)
    hfTarget['window]=42 hfForm['target]=hfTarget
    hfForm['bridge]=when(boundp('sicoInstanceBridge) sicoBindings[sicoInstanceBridge])
    hfForm['session]="capturedDesktop" hfForm['pending]=hfKey
    sicoQuickRequests[hfKey]=hfForm sicoQuickHostChildren[hfChild]=hfKey
    hfForm))
''' + body + '\nprintf("HOST_CALLBACKS_PASS\\n") exit()\n'
    )
    env = {key: value for key, value in os.environ.items()
           if key not in {"LD_LIBRARY_PATH", "LD_PRELOAD", "SICO_ROOT"}}
    result = subprocess.run([str(DB), "-load", str(source)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    (tmp_path / "output.log").write_text(output)
    assert "HOST_CALLBACKS_PASS" in output, output
    if expected_error:
        assert "*Error* " + expected_error in output, output
        output = output.replace("*Error* " + expected_error, "")
    assert "*Error*" not in output and "*WARNING*" not in output, output


def test_split_lines_exact_input_wake_and_terminal_receipt(tmp_path):
    run_skill(tmp_path, r'''
procedure(sicoQuickWake(qwKey qwSession) wakeObserved=list(qwKey qwSession))
form=hostForm("q1" "child")
sicoQuickHostData("child" "\n@accepted q10\n@accepted q1 extra\n@wake q1 s1 extra\n")
unless(and(form['pending]=="q1" !form['notices]) error("Accepted a prefix or extension"))
sicoQuickHostData("child" "@wa")
sicoQuickHostData("child" "ke q1 serviceSession\n@acce")
unless(and(wakeObserved==list("q1" "serviceSession") form['pending]=="q1")
  error("Wake confused session/input identity or acknowledged the input"))
sicoQuickHostData("child" "pted q1\n")
sicoQuickHostExit("child" 0)
sicoQuickHostData("child" "@unknown q1\n")
unless(and(form['notices]==list("accepted") form['closing] !form['pending]
  !sicoQuickHostChildren["child"] !sicoQuickHostBuffers["child"])
  error("Exit or late result changed an accepted receipt"))
''')


def test_unknown_rejected_truncated_exit_and_old_child_are_distinct(tmp_path):
    run_skill(tmp_path, r'''
form=hostForm("q2" "child2")
sicoQuickHostData("child2" "@unknown q2\n") sicoQuickHostExit("child2" 0)
unless(and(form['notices]==list("unknown") !form['closing] !form['pending]
  rexMatchp("q2" form['message])) error("Unknown lost its original input identity"))
form=hostForm("q3" "child3")
sicoQuickHostData("child3" "@rejected q3\n") sicoQuickHostExit("child3" 0)
unless(form['notices]==list("rejected") error("Explicit rejection became unknown"))
form=hostForm("q4" "child4")
sicoQuickHostData("child4" "@accepted q4") sicoQuickHostExit("child4" 0)
unless(form['notices]==list("unknown") error("Truncated receipt became acceptance"))
form=hostForm("q5" "child5") sicoQuickHostExit("child5" 0)
unless(form['notices]==list("unknown") error("Exit without receipt became rejection"))
form=hostForm("old" "oldChild") form['pending]="new"
sicoQuickRequests["new"]=form sicoQuickHostChildren["newChild"]="new"
sicoQuickHostData("oldChild" "@accepted old\n") sicoQuickHostExit("oldChild" 0)
unless(and(!form['notices] form['pending]=="new") error("Old child changed new input"))
sicoQuickHostData("newChild" "@accepted new\n")
unless(form['notices]==list("accepted") error("New child receipt was lost"))
''')


def test_wake_uses_captured_desktop_and_failure_does_not_swallow_acceptance(tmp_path):
    run_skill(tmp_path, r'''
procedure(sicoSessionForWindow(qwWindow) error("Wake followed a changed window session"))
procedure(sicoSessionLive(qwCid) shown=qwCid nil)
form=hostForm("q6" "child6")
sicoQuickHostData("child6" "@wake q6 serviceSession\n")
unless(shown=="capturedDesktop" error("Wrong desktop woke up"))
shown=nil form['closing]=t
sicoQuickHostData("child6" "@wake q6 serviceSession\n")
when(shown error("Closed composer woke a desktop"))
form['closing]=nil form['session]=nil
sicoQuickHostData("child6" "@wake q6 serviceSession\n@accepted q6\n")
unless(and(!shown form['notices]==list("accepted")) error("No GUI changed admission"))
procedure(sicoQuickWake(qwKey qwSession) error("Synthetic wake failure"))
form=hostForm("q7" "child7")
sicoQuickHostData("child7" "@wake q7 serviceSession\n@accepted q7\n")
unless(form['notices]==list("accepted") error("Wake failure swallowed receipt"))
''', expected_error="Synthetic wake failure")


def test_legacy_desktop_exit_and_receipts_cannot_settle_host_request(tmp_path):
    run_skill(tmp_path, r'''
sicoBindings=makeTable("bindings" nil)
sicoWindowSessions=makeTable("windows" nil)
form=hostForm("q8" "child8")
sicoQuickAccepted("capturedDesktop" "q8")
sicoQuickRejected("capturedDesktop" "q8") sicoQuickExit("capturedDesktop")
unless(and(form['pending]=="q8" !form['notices]) error("Desktop settled host admission"))
sicoQuickHostData("child8" "@accepted q8\n")
unless(form['notices]==list("accepted") error("Lost host receipt after desktop exit"))
form=hostForm("overflow" "overflowChild")
payload="x" for(i 1 13 payload=strcat(payload payload))
sicoQuickHostData("overflowChild" payload)
unless(and(form['notices]==list("unknown") !sicoQuickHostBuffers["overflowChild"])
  error("Unbounded receipt buffer or false rejection"))
''')


def test_service_wake_captures_runtime_and_rejects_malformed_identity(tmp_path):
    run_skill(tmp_path, r'''
procedure(sicoQuickWake(qwKey qwSession @optional qwService qwRuntime)
  wakeObserved=list(qwKey qwSession qwService qwRuntime))
form=hostForm("q9" "child9")
service="11111111111111111111111111111111" runtime="22222222222222222222222222222222"
sicoQuickHostData("child9" strcat("@wake q9 live " service " " runtime "\n"))
unless(wakeObserved==list("q9" "live" service runtime) error("Wake lost captured runtime"))
wakeObserved=nil
sicoQuickHostData("child9" strcat("@wake q9 live short " runtime "\n"))
sicoQuickHostData("child9" strcat("@wake q9 live " service " invalid\n"))
sicoQuickHostData("child9" strcat("@wake q90 live " service " " runtime "\n"))
when(wakeObserved error("Malformed or foreign wake was accepted"))
''')


def test_service_wake_ignores_closed_or_superseded_composer(tmp_path):
    run_skill(tmp_path, r'''
sicoBindings=makeTable("bindings" nil)
procedure(sicoDesktopWake(source bridge key session service runtime) calledMode="launch" t)
form=hostForm("q10" "child10")
service="11111111111111111111111111111111" runtime="22222222222222222222222222222222"
sicoQuickWake("q10" "live" service runtime)
unless(calledMode=="launch" error("Wrong GUI entrypoint"))
calledMode=nil form['closing]=t
sicoQuickWake("q10" "live" service runtime)
when(calledMode error("Closed composer opened GUI"))
form['closing]=nil form['pending]="new"
sicoQuickWake("q10" "live" service runtime)
when(calledMode error("Superseded input opened GUI"))
''')


def test_service_wake_attaches_the_live_desktop_instead_of_opening_a_second_window(tmp_path):
    run_skill(tmp_path, r'''
sicoBindings=makeTable("bindings" nil)
desktop=makeTable("desktop" nil) desktop['desktopSession]=t
sicoInstanceBridge="bridgeCid"
bridge=makeTable("bridge" nil) bridge['instanceId]="instance" bridge['generation]="generation"
bridge['cwd]="/tmp" sicoBindings[sicoInstanceBridge]=bridge
foreach(field list('instanceId 'generation 'cwd) desktop[field]=bridge[field])
sicoBindings["desktopCid"]=desktop
attached=nil launched=nil
procedure(sicoSessionLive(cid) cid=="desktopCid")
procedure(sicoLaunchCommand(mode) launched=mode nil)
procedure(sicoDesktopAttach(cid sessionId) attached=list(cid sessionId) t)
form=hostForm("q11" "child11")
service="11111111111111111111111111111111" runtime="22222222222222222222222222222222"
accepted="33333333333333333333333333333333"
sicoQuickWake("q11" accepted service runtime)
unless(and(attached==list("desktopCid" accepted) !launched)
  error("Live project window was not reused for the accepted session"))
attached=nil sicoBindings["desktopCid"]=nil
procedure(sicoDesktopLaunch(source bridge target session service runtime) launched="launch" t)
sicoQuickWake("q11" accepted service runtime)
unless(launched=="launch" error("No live window did not launch the GUI"))
''')


def test_quick_ask_sets_working_dir_without_calling_simplify_on_nil(tmp_path):
    run_skill(tmp_path, r'''
sicoQuickWindows=makeTable("quick" nil)
sicoWindowSessions=makeTable("windows" nil)
sicoLaunchWorkingDir="/workarea/projs/proj4/xh"
procedure(sicoSessionForWindow(qwWindow) nil)
procedure(sicoLaunchCommand(qwMode) nil)
binding=makeTable("binding" nil) binding['window]=42
sicoQuickAsk(binding)
unless(binding['cwd]==sicoLaunchWorkingDir
  error("Quick ask did not capture the project working directory"))
''')


def test_quick_anchor_uses_pointer_then_window_center(tmp_path):
    run_skill(tmp_path, r'''
quickPointer=list(120 80)
procedure(hiGetMaxScreenCoords() list(1920 1200))
procedure(hiGetAbsWindowScreenBBox(qwWindow) list(list(200 300) list(1000 900)))
procedure(hiGetScreenPoint(qwWindow) quickPointer)
binding=makeTable("anchorBinding" nil) binding['window]=42
unless(sicoQuickAnchor(binding)==list("pointer" 320 380)
  error("Pointer anchor did not use drawable screen coordinates"))
unless(sicoQuickAnchorJson(binding)=="{\"kind\":\"pointer\",\"x\":320,\"y\":380}"
  error("Pointer anchor JSON mismatch"))
quickPointer=nil
unless(sicoQuickAnchor(binding)==list("window" 600 600)
  error("Missing pointer did not fall back to window centre"))
unless(sicoQuickAnchorJson(binding)=="{\"kind\":\"window\",\"x\":600,\"y\":600}"
  error("Window-centre anchor JSON mismatch"))
''')


def test_quick_object_requests_return_bounded_picker_json(tmp_path):
    run_skill(tmp_path, r'''
procedure(aiSelectionInspect(qorRef qorOffset qorLimit)
  "{\"ok\":true,\"items\":[]}")
procedure(aiSelectionList(qorRef qorKind qorQuery qorOffset qorLimit)
  "{\"ok\":true,\"items\":[{\"kind\":\"net\",\"name\":\"VOUT\"}]}")
procedure(aiWindowList(qorOffset qorLimit)
  "{\"ok\":true,\"windows\":[{\"window_ref\":\"wctx_fixture_1\"}]}")
composer=makeTable("objectComposer" nil)
sicoQuickObjects(composer "selection o1 wctx_fixture_1 0 50")
unless(and(composer['notices]==list("objects")
           rexMatchp("o1" composer['message])
           rexMatchp("items" composer['message]))
  error("Ask selection request did not return picker JSON"))
composer['notices]=nil composer['message]=nil
sicoQuickObjects(composer "objects o2 wctx_fixture_1 all 50 50 564f5554")
unless(and(composer['notices]==list("objects")
           rexMatchp("o2" composer['message])
           rexMatchp("VOUT" composer['message]))
  error("Ask object request did not return picker JSON"))
composer['notices]=nil composer['message]=nil
sicoQuickObjects(composer "windows o3 0 200")
unless(and(composer['notices]==list("objects")
           rexMatchp("o3" composer['message])
           rexMatchp("wctx_fixture_1" composer['message]))
  error("Ask window request did not return picker JSON"))
''')


def test_service_wake_error_is_visible_without_losing_the_accepted_receipt(tmp_path):
    run_skill(tmp_path, r'''
sicoBindings=makeTable("bindings" nil)
desktopRow=makeTable("desktop" nil) desktopRow['desktopSession]=t
sicoInstanceBridge="bridgeCid" bridge=makeTable("bridge" nil)
bridge['instanceId]="instance" bridge['generation]="generation" bridge['cwd]="/tmp"
sicoBindings[sicoInstanceBridge]=bridge
foreach(field list('instanceId 'generation 'cwd) desktopRow[field]=bridge[field])
sicoBindings["desktopCid"]=desktopRow
procedure(sicoSessionLive(cid) cid=="desktopCid")
procedure(sicoDesktopAttach(cid sessionId) error("Synthetic desktop write failure"))
form=hostForm("wakeError" "wakeChild")
sicoQuickHostData("wakeChild" strcat("@wake wakeError "
 "33333333333333333333333333333333 11111111111111111111111111111111 "
 "22222222222222222222222222222222\n@accepted wakeError\n"))
unless(and(form['notices]==list("accepted") form['closing] !form['pending])
 error("Wake error changed durable acceptance"))
''', expected_error="Synthetic desktop write failure")


def test_first_wake_registers_a_desktop_for_later_attachments(tmp_path):
    run_skill(tmp_path, r'''
sicoBindings=makeTable("bindings" nil)
sicoInstanceBridge="bridgeCid"
bridgeRow=makeTable("bridge" nil)
bridgeRow['instanceId]="instance" bridgeRow['generation]="generation" bridgeRow['cwd]="/tmp"
bridgeRow['targets]=makeTable("targets" nil) sicoBindings[sicoInstanceBridge]=bridgeRow
procedure(sicoSessionLive(cid) cid=="newDesktop")
procedure(sicoLaunchCommand(mode) launched=mode "sico launch")
procedure(sicoQuote(value) strcat("'" value "'"))
procedure(sicoConnect(command instance generation window captured targetId)
  capturedCommand=command capturedTarget=targetId
  captured['instanceId]=instance captured['generation]=generation captured['cwd]="/tmp"
  unless(and(instance=="instance" generation=="generation" window==42
    eq(captured['targets] bridgeRow['targets])) error("Lost host routing identity"))
  sicoBindings["newDesktop"]=captured "newDesktop")
procedure(sicoBindSession(window cid) boundDesktop=list(window cid))
procedure(sicoDesktopAttach(cid sessionId) attached=list(cid sessionId) t)
form=hostForm("firstWake" "firstChild")
service="11111111111111111111111111111111" runtime="22222222222222222222222222222222"
accepted="33333333333333333333333333333333"
sicoQuickWake("firstWake" accepted service runtime)
unless(and(launched=="launch" boundDesktop==list(42 "newDesktop")
 sicoBindings["newDesktop"]['desktopSession] capturedTarget=="firstWake"
 rexMatchp(accepted capturedCommand) rexMatchp(service capturedCommand)
 rexMatchp(runtime capturedCommand)) error("First Ask window cannot be reused"))
launched=nil attached=nil
sicoQuickWake("firstWake" accepted service runtime)
unless(and(!launched attached==list("newDesktop" accepted))
 error("Second Ask launched another desktop"))
''')


def test_instance_admission_is_single_flight_and_unknown_only_queries(tmp_path):
    run_skill(tmp_path, r'''
started=nil sent=nil childCount=0
procedure(ipcBeginProcess(qbCommand qbHost qbData qbError qbExit)
  childCount=childCount+1
  started=append1(started qbCommand)
  sprintf(nil "proc%d" childCount))
procedure(ipcWriteProcess(qbChild qbText)
  sent=append1(sent list(qbChild qbText)) t)
procedure(sicoLaunchCommand(qbMode) qbMode)
first=hostForm("first" "unused1") second=hostForm("second" "unused2")
sicoAskEnqueue("first" "first-envelope" "host-submit")
sicoAskEnqueue("second" "second-envelope" "host-submit")
unless(and(started==list("host-submit") sicoAskFlight=="first"
  sicoAskWaiting==list("second")) error("Admission was not single flight"))
sicoQuickHostData("proc1" "@unknown first\n")
sicoQuickHostExit("proc1" 0)
unless(and(sicoAskFlight=="first" first['pending]=="first" !sicoAskAvailable())
  error("Unknown receipt released the slot"))
sicoAskQuery("first") sicoAskQuery("first")
unless(started==list("host-submit" "host-query") error("Unknown was resubmitted"))
sicoQuickHostData("proc2" "@accepted first\n")
unless(and(sicoAskFlight=="second" !sicoAskWaiting
  started==list("host-submit" "host-query" "host-submit")
  sent==list(list("proc1" "first-envelope\n") list("proc2" "first-envelope\n")
    list("proc3" "second-envelope\n"))) error("Durable receipt did not release one slot"))
sicoQuickHostExit("proc1" 0) sicoQuickHostExit("proc2" 0)
unless(sicoAskFlight=="second" error("Old worker changed a newer flight"))
sicoQuickHostData("proc3" "@rejected second queue_full\n")
unless(and(!sicoAskFlight !second['pending] second['notices]==list("rejected"))
  error("Explicit refusal lost original composer"))
''')


def test_non_head_wake_never_attaches_or_launches(tmp_path):
    run_skill(tmp_path, r'''
sicoBindings=makeTable("bindings" nil) sicoInstanceBridge="bridge"
bridge=makeTable("bridge" nil) sicoBindings["bridge"]=bridge
procedure(sicoDesktopFind(nhwBridge) error("Later request looked up desktop"))
session="33333333333333333333333333333333"
service="11111111111111111111111111111111" runtime="22222222222222222222222222222222"
form=hostForm("later" "later-child")
sicoQuickHostData("later-child" strcat("@wake later " session " " service " "
  runtime " 2 first\n@accepted later\n"))
unless(form['notices]==list("accepted") error("Suppressed wake changed receipt"))
''')
