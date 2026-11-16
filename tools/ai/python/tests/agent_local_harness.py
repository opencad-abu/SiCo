"""Test-only local bootstrap for cancellation and resource ownership regression."""

import time
import uuid

from agent_frontend_harness import frontend
from agent_startup_harness import DesktopStartup

from sico.demo import DemoPeer
from sico.service.connection import connection_instructions
from sico.service.startup_result import StartupReady
from sico.transport.broker import ContextBroker


class LocalStartup(DesktopStartup):
    instructions = ""

    def _prepare(self, resources):
        provider = self._provider()
        journal = self._journal(resources)
        self.phase = "bridge"
        broker = resources.enter_context(ContextBroker(min(self.args.timeout, 30)))
        self._check()
        self.phase = "context"
        if self.args.command == "demo":
            peer = DemoPeer(broker, journal.state.context if journal.state else None,
                            cancelled=self._stop.is_set)
            resources.callback(peer.close)
            context = peer.context
        else:
            if journal.state:
                raise ValueError(
                    "Rebinding a real session is not supported yet; use a new session ID"
                )
            instance, generation = uuid.uuid4().hex, uuid.uuid4().hex
            broker.expected_identity = instance, generation
            self.instructions = connection_instructions(
                journal, broker, instance, generation,
                context_file=getattr(self.args, "context_file", None),
            )
            print(self.instructions, flush=True)
            context = self._context(broker, instance, generation)
        self._check()
        print(f"session={self.args.session}  provider={provider.label}", flush=True)
        controller, sessions = self._sessions(
            resources, provider, broker, journal, context,
        )
        controller.bindings.attach_targets(broker)
        return StartupReady(frontend(sessions.worker, controller), True, persistent=False,
                            initial_text=self.args.message or "", attach_targets=False)

    def _context(self, broker, instance, generation):
        deadline = time.monotonic() + self.args.timeout
        while True:
            self._check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Virtuoso relay did not register")
            try:
                context = broker.context(instance, timeout=min(0.05, remaining))
            except TimeoutError:
                continue
            if (context.instance_id, context.generation) != (instance, generation):
                raise ValueError("Relay source does not match the connection instructions")
            return context
