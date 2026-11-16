"""Terminal value and persistence boundaries cannot load an execution runtime."""

import os
from pathlib import Path
import subprocess
import sys

from utility.check_private_import_boundaries import check_rule, load_manifest


ROOT = Path(__file__).resolve().parents[3]


def test_terminal_sources_obey_the_declared_layer_boundaries():
    rules = [rule for rule in load_manifest()["rules"]
             if rule["id"] in {"core-terminal-contracts-runtime-free",
                               "core-terminal-session-runtime-free",
                               "storage-terminal-records-one-way-boundary",
                               "storage-terminal-history-read-only",
                               "storage-journal-snapshot-no-execution",
                               "service-terminal-environment-runtime-free",
                               "service-terminal-dispatch-no-launcher-ui",
                               "service-terminal-backend-runtime-free",
                               "storage-terminal-journal-one-way-boundary"}]
    assert len(rules) == 9
    for rule in rules:
        assert not check_rule(rule, root=ROOT)


def test_terminal_recovery_imports_without_execution_or_gui(tmp_path):
    roots = [str(ROOT / "tools" / tool / "python") for tool in ("sico", "ai", "common")]
    script = "import sys\nsys.path[:0] = " + repr(roots) + "\n" + r'''
import importlib.abc

BLOCKED = ("sico.transport", "sico.service", "sico.providers", "sico.codex", "sico.execution",
           "sico.local_process", "sico_ui", "PyQt5", "PySide", "subprocess",
           "socket", "sicoprocess")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".") for prefix in BLOCKED):
            raise ImportError("blocked runtime import: " + fullname)

sys.meta_path.insert(0, Block())
from sico.core.terminal_commands import CommandRef, CommandRequest
from sico.core.terminal_output import OutputChunk
from sico.core.terminal_state import apply_event, event, recovered
from sico.core.terminal_session import TerminalClaim, TerminalRef
from sico.core.terminal_session_state import apply_event as apply_session_event
from sico.core.terminal_session_events import event as session_event
from sico.storage.terminal_records import restore, store
from sico.storage.terminal_journal import TerminalJournal

request = CommandRequest(CommandRef("s", "t", "c"), "r", "/project", "env", argv=("pwd",))
state = apply_event(None, event("accepted", request.ref, request=request.record()))
assert recovered(state).phase == "unknown"
assert OutputChunk.from_record(OutputChunk(request.ref, 1, 0, "pty", b"bytes").record()).data == b"bytes"
terminal = TerminalClaim(TerminalRef("s", "t"), "/bin/bash", "/project", "env")
terminal_state = apply_session_event(None, session_event("claimed", terminal.ref,
                                                         claim=terminal.record()))
assert terminal_state.terminal_ref == terminal.ref
assert not any(name in sys.modules for name in BLOCKED)
assert "sico.storage.journal" not in sys.modules
from sico.storage.terminal_history import TerminalHistory
from sico.core.terminal_receipts import summary
assert summary(state)['phase'] == 'accepted'
assert not any(name in sys.modules for name in BLOCKED)
assert "sico.storage.journal" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-B", "-S", "-c", script],
                            cwd=tmp_path, env=dict(os.environ, PYTHONPATH=os.pathsep.join(roots)),
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
