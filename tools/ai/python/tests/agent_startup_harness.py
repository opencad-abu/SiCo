"""Test-only in-process startup harness for historical FB regression."""

import time
from contextlib import ExitStack

from agent_frontend_harness import frontend
from agent_sessions_harness import DesktopSessions

from cadai.env_names import name as env_name
from sico.adapters.virtuoso import context_tools
from sico.core.contracts import BoundContext
from sico.providers.config import provider_from_config, resolve_provider_config
from sico.service.backend import create_backend
from sico.service.controller import SessionController
from sico.service.startup_lifecycle import StartupLifecycle
from sico.service.startup_result import StartupReady
from sico.storage.journal import SessionJournal
from sico.transport.bridge_client import BridgeClient


class DesktopStartup(StartupLifecycle):
    @staticmethod
    def _close_sessions(sessions):
        sessions.close()
        sessions.wait()

    def _prepare(self, resources):
        registration = self._receive(self._registration, self.registration_timeout)
        bound = BoundContext.from_record(registration["context"])
        descriptor = registration["bridge"]
        if any(descriptor[key] != getattr(bound, key) for key in ("instance_id", "generation")):
            raise ValueError("Desktop bridge and target identity mismatch")
        self._check()
        self.phase = "bridge"
        broker = resources.enter_context(BridgeClient(descriptor, cancelled=self._stop.is_set))
        # The launcher's handshake precedes slow history/config loading.
        self.notice(
            "ready", bridge_id=broker.bridge_id, router_id=broker.descriptor["router_id"],
            instance_id=bound.instance_id, generation=bound.generation,
            session_id=self.args.session, target_id=bound.target_id,
        )
        self._check()
        self.phase = "context"
        context = broker.context(
            bound.instance_id, bound.target_id, timeout=self.context_timeout,
            deadline=time.monotonic() + self.context_timeout, cancelled=self._stop.is_set,
        )
        if context != bound:
            raise ValueError("Desktop target does not match Relay registration")
        self._check()
        journal = self._journal(resources)
        provider = self._provider()
        controller, sessions = self._sessions(resources, provider, broker, journal, context)
        initialize = not any(event["kind"] == "task.started" for event in journal.events())
        return StartupReady(frontend(sessions.worker, controller), initialize)

    def _journal(self, resources):
        self.phase = "history"
        journal = resources.enter_context(SessionJournal(self.args.launch_dir, self.args.session))
        self._check()
        return journal

    def _provider(self):
        self.phase = "config"
        config = resolve_provider_config(self.args.provider_config, launch_dir=self.args.launch_dir,
                                         environment=self.environment)
        self._check()
        key = config.get("api_key_env", env_name("API_KEY")) if config else None
        if key and not self.environment.get(key):
            self.phase = "credentials"
            self.configuration.set_result(key)
            self.environment[key] = self._receive(self._credential)
        else:
            self.configuration.set_result(None)
        self._check()
        self.phase = "provider"
        provider = provider_from_config(config, environment=self.environment)
        self._check()
        return provider

    def _sessions(self, resources, provider, broker, journal, context, *, attach=True):
        self.phase = "session"
        loop = create_backend(provider, context_tools(broker, journal), journal, context)
        with ExitStack() as pending_backend:
            if callable(getattr(loop, "close", None)):
                pending_backend.callback(loop.close)
            self._check()
            if getattr(self.args, "abandon_interrupted", False):
                loop.acknowledge_interrupted()
            controller = SessionController(loop)
            self._check()
            from sico.service.desktop import resume_context

            sessions = DesktopSessions(
                controller, broker=broker if attach else None,
                resolve_context=lambda saved: resume_context(broker, controller.current, saved),
            )
            resources.callback(self._close_sessions, sessions)
            # SessionController.wait now owns backend cleanup.
            pending_backend.pop_all()
        return controller, sessions
