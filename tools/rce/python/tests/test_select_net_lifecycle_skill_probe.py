from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest


CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


def _skill_literal(path: Path) -> str:
    """Return a quoted SKILL path without relying on shell interpolation."""
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


@pytest.mark.skipif(
    not RUN_PROBE,
    reason="set RCE_RUN_SKILL_PROBE=1 to run the Cadence dbAccess probe",
)
def test_select_net_done_guards_owner_and_window_lifecycle(tmp_path: Path) -> None:
    dbaccess = shutil.which(os.environ.get("RCE_DBACCESS", "dbAccess"))
    if dbaccess is None:
        pytest.skip("dbAccess is unavailable")

    command_file = CAD_ROOT / "rce/skill/CMD_rceSelectNetAddDone.il"
    skill = "\n".join(
        (
            "defstruct(mockWindow isWindow selected closed closeCount)",
            "defstruct(mockField value)",
            "defstruct(mockForm valid netSelFile)",
            "defstruct(mockNet sigNames)",
            "defstruct(mockObject net)",
            "procedure(windowp(win) and(win win~>isWindow))",
            "procedure(hiIsForm(form) and(form form~>valid))",
            "procedure(geGetSelSet(win) win~>selected)",
            "procedure(hiCloseWindow(win) win~>closed=t "
            "win~>closeCount=win~>closeCount+1 t)",
            "procedure(geAddSelectPoint(win layer point) t)",
            "procedure(GUI_messageApp(severity title text) "
            "printf(\"MSG:%s\\n\" text) t)",
            "procedure(SICO_logo() \"SiCo\")",
            f"load({_skill_literal(command_file)})",
            # Empty selection is a valid completed selection.  The window and
            # owner mapping must still be closed/cleared without an error.
            "emptyField=make_mockField(?value \"keep\")",
            "emptyOwner=make_mockForm(?valid t ?netSelFile emptyField)",
            "emptyWin=make_mockWindow(?isWindow t ?selected nil ?closeCount 0)",
            "rceSelectNetOwnerSet(emptyWin emptyOwner)",
            "CMD_rceSelectNetDone(emptyWin t nil)",
            "emptyOk=and(emptyField~>value==\"\" emptyWin~>closed "
            "emptyWin~>closeCount==1 !rceSelectNetOwnerGet(emptyWin))",
            # Multiple objects are traversed independently.  The integer in
            # the middle simulates a deleted database object; errset must let
            # the valid objects on either side survive.
            "net1=make_mockNet(?sigNames list(\"N1\"))",
            "net2=make_mockNet(?sigNames list(\"N2\"))",
            "multiOwner=make_mockForm(?valid t "
            "?netSelFile make_mockField(?value \"old\"))",
            "multiWin=make_mockWindow(?isWindow t "
            "?selected list(make_mockObject(?net net1) 1 "
            "make_mockObject(?net net2)) ?closeCount 0)",
            "rceSelectNetOwnerSet(multiWin multiOwner)",
            "CMD_rceSelectNetDone(multiWin t nil)",
            "multiOk=and(multiOwner~>netSelFile~>value==\"N1 N2\" "
            "multiWin~>closed multiWin~>closeCount==1 "
            "!rceSelectNetOwnerGet(multiWin))",
            # A stale owner must not receive a write, but the selection window
            # and owner table entry still need deterministic cleanup.
            "staleField=make_mockField(?value \"preserve\")",
            "staleOwner=make_mockForm(?valid nil ?netSelFile staleField)",
            "staleWin=make_mockWindow(?isWindow t "
            "?selected list(make_mockObject(?net net1)) ?closeCount 0)",
            "rceSelectNetOwnerSet(staleWin staleOwner)",
            "CMD_rceSelectNetDone(staleWin t nil)",
            "staleOk=and(staleField~>value==\"preserve\" staleWin~>closed "
            "staleWin~>closeCount==1 !rceSelectNetOwnerGet(staleWin))",
            # Invalid windows are ignored by all owner helpers and must not
            # trigger a database/window dereference.
            "invalidOwner=make_mockForm(?valid t "
            "?netSelFile make_mockField(?value \"untouched\"))",
            "invalidWin=make_mockWindow(?isWindow nil ?selected nil "
            "?closeCount 0)",
            "rceSelectNetOwnerSet(invalidWin invalidOwner)",
            "CMD_rceSelectNetDone(invalidWin t nil)",
            "invalidOk=and(invalidOwner~>netSelFile~>value==\"untouched\" "
            "invalidWin~>closeCount==0 !rceSelectNetOwnerGet(invalidWin))",
            "if(and(emptyOk multiOk staleOk invalidOk) "
            "then printf(\"RCE_SELECT_NET_LIFECYCLE_OK\\n\"))",
            "exit()",
        )
    )
    completed = subprocess.run(
        [dbaccess],
        input=skill,
        text=True,
        capture_output=True,
        cwd=tmp_path,
        timeout=30,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "*Error*" not in output
    assert "(reader)" not in output
    assert "still unclosed on EOF" not in output
    assert "RCE_SELECT_NET_LIFECYCLE_OK" in output
