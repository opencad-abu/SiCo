"""Source probes for callback reservation ownership and completion ordering."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from skill_test_support import ROOT

pytestmark = pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1",
    reason="set RCE_RUN_SKILL_PROBE=1 to run dbAccess source probes",
)


def _run(source: str) -> None:
    executable = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if executable is None:
        pytest.skip("dbAccess is unavailable")
    completed = subprocess.run(
        [executable], input=source + '\nexit()\n', text=True,
        capture_output=True, timeout=30, check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "RCE_CALLBACK_LIFECYCLE_OK" in output, output


@pytest.mark.parametrize("outcome", ["success", "view_error", "summary_error"])
def test_ipc_releases_after_publication_even_when_callbacks_fail(
    tmp_path: Path, outcome: str,
) -> None:
    pid = tmp_path / "worker.pid"
    pid.write_text("123\n")
    view = 'error("expected view failure")' if outcome == "view_error" else "t"
    summary = 'error("expected summary failure")' if outcome == "summary_error" else "viewOk"
    expected = "view summary release close" if outcome == "success" else "view summary release"
    _run(f'''
load("{ROOT / 'rce/skill++/RCEIPCCB.ils'}")
events=nil
procedure(rceCompleteViewRequest(request logPath)
  events=append1(events 'view) {view})
procedure(rceSummaryDisplay(meta status viewOk)
  events=append1(events 'summary) {summary})
procedure(rceReleaseRun(reservation)
  unless(reservation==list("run" "token") error("wrong reservation"))
  events=append1(events 'release))
procedure(GUI_flowLogClose(request) events=append1(events 'close))
procedure(info(message) t)
rceIpcSetMeta(123 list("{pid}" "" "config" 'view 'meta 'flow list("run" "token")))
rceIpcPostFunc(123 0)
when(and(!isFile("{pid}") !rceIpcGetMeta(123) events=='({expected}))
  printf("RCE_CALLBACK_LIFECYCLE_OK\\n"))
''')


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize("outcome", ["success", "check_failed", "writer_error"])
def test_print_releases_only_owned_reservations(borrowed: bool, outcome: str) -> None:
    supplied = 'list("run" "token")' if borrowed else "nil"
    check = "1" if outcome == "check_failed" else "0"
    writer = 'error("expected writer failure")' if outcome == "writer_error" else '"config"'
    expected = ([] if borrowed else ["reserve"]) + ["check"]
    if outcome != "check_failed":
        expected.append("write")
    if not borrowed:
        expected.append("release")
    _run(f'''
load("{ROOT / 'rce/skill++/RCERUNLOCK.ils'}")
events=nil
procedure(rceRunDirectory(form) "run")
procedure(rceReserveRun(form)
  events=append1(events 'reserve) list("run" "token"))
procedure(rceRunLockCommand(action directory token)
  events=append1(events 'check) {check})
procedure(rceReleaseRun(reservation) events=append1(events 'release))
procedure(rcePrintReserved(form) events=append1(events 'write) {writer})
result=errset(rcePrint('form {supplied}) nil)
when(and(events=='({' '.join(expected)})
         {'result==list("config")' if outcome == 'success' else '!car(result)'})
  printf("RCE_CALLBACK_LIFECYCLE_OK\\n"))
''')


@pytest.mark.parametrize("started", [False, True])
def test_start_keeps_reservation_only_after_successful_launch(started: bool) -> None:
    expected = "reserve start" if started else "reserve start release"
    _run(f'''
load("{ROOT / 'rce/skill++/RCERUN.ils'}")
events=nil
procedure(rceReserveRun(form) events=append1(events 'reserve) 'reservation)
procedure(rceStartReserved(form reservation)
  events=append1(events 'start) {'t' if started else 'nil'})
procedure(rceReleaseRun(reservation) events=append1(events 'release))
result=rceStart('form)
when(and(events=='({expected}) result=={'t' if started else 'nil'})
  printf("RCE_CALLBACK_LIFECYCLE_OK\\n"))
''')
