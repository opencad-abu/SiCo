"""Test-only local adapter proves real command bytes survive the existing journal.

It does not select production shell permissions or qualify managed exec-server.
"""

from pathlib import Path

from bash_channel_fixtures import BashFixture, command
from sico.core.terminal_session import TerminalGeneration, TerminalRetirement
from sico.process_identity import process_identity
from sico.service.terminal_backend import TerminalCompletion


class LocalBashBackend:
    def __init__(self):
        self.shell = None
        self.starts = 0
        self.generations = []

    def start(self, attempt, claim):
        self.starts += 1
        assert self.starts == 1
        self.shell = BashFixture(Path(claim.cwd))
        facts, _ = self.shell.run(command(":", name="bootstrap"))
        pid = facts[0].value
        identity = process_identity(pid)
        # This fixture shares the parent's PID namespace. Check actual kernel
        # parentage before using its PID in the persisted generation.
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        assert int(fields[1]) == self.shell.owner.child.pid
        self.generation = TerminalGeneration(attempt, pid, int(identity["start"]))
        return self.generation

    def execute(self, request, *, started, output, cancelled):
        assert not cancelled()
        def observed(fact):
            assert fact.value == self.generation.pid
            assert int(process_identity(fact.value)["start"]) == self.generation.process_start
            self.generations.append(self.generation)
            started("execution-" + request.ref.command_id)
        facts, _ = self.shell.run(request, on_start=observed, on_output=output)
        assert facts[-1].kind == "finished"
        return TerminalCompletion(facts[-1].value, output_complete=True, shell_ready=True)

    def write_input(self, value):
        raise ValueError("The local pipe fixture has no interactive stdin")

    def close(self, attempt):
        assert self.generation.attempt == attempt
        self.shell.close()
        return TerminalRetirement(attempt, "process_exited", "local-fixture-cleanup", self.generation)
