"""Project-owned session construction and per-session dependency isolation.

The service owns every live controller and its resources. Controller snapshots
are read-only views derived from the sole session registry.
"""

from __future__ import annotations

import os
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from ..adapters.virtuoso import context_tools
from ..core.contracts import BoundContext, identifier, json_copy
from ..providers.config import provider_from_config, resolve_provider_config
from ..storage.journal import SessionJournal
from ..storage.history_policy import assert_writable
from ..storage.roots import agent_root
from ..storage.session_snapshot import NAME, capture, save
from ..transport.bridge_client import BridgeClient
from .backend import create_backend
from .cleanup_task import DEFAULT_CLEANUP_SECONDS, CleanupTask
from .controller import SessionController
from .published import freeze, thaw
from .service_lifecycle import PendingWork
from .session_dependencies import SessionDependencies
from .session_creation import SessionCreation
from .session_disposal import close_bundle, close_partial
from .session_health import SessionHealth

_CAPTURE_BACKGROUND = object()


@dataclass
class _SessionBundle:
    dependencies: SessionDependencies
    provider: object
    bridge: object
    journal: SessionJournal
    controller: SessionController
    health: object = None
    resources: object = None


class ProjectSessionOwner:
    """Own all sessions for one project and publish them atomically."""

    def __init__(self, project, *, provider_config=None, environment=None, bridge_factory=None):
        project = Path(project).absolute()
        if not project.is_dir():
            raise ValueError("Project directory is unavailable")
        self.project = project
        self._default_config = None if provider_config is None else thaw(freeze(provider_config))
        self._default_environment = None if environment is None else dict(environment)
        self._bridge_factory = bridge_factory or BridgeClient
        self._bundles = {}
        self.creation = SessionCreation(self)
        self._lock = threading.RLock()
        self._mutation = threading.RLock()
        self._opening = 0
        self._closed = False
        self._cleanup = CleanupTask(self._release, "copilot-owner-close")

    @property
    def controllers(self):
        return MappingProxyType(self._controller_snapshot())

    def _controller_snapshot(self):
        with self._lock:
            return {session_id: bundle.controller
                    for session_id, bundle in self._bundles.items()}

    def dependencies_for(self, session_id):
        identifier(session_id)
        with self._lock:
            try:
                return self._bundles[session_id].dependencies
            except KeyError:
                raise ValueError("Session is not open") from None

    def provider_for(self, session_id):
        """Service-internal provider lookup; callers receive no mutable registry."""

        identifier(session_id)
        with self._lock:
            try:
                return self._bundles[session_id].provider
            except KeyError:
                raise ValueError("Session is not open") from None

    def open(self, session_id, descriptor, context, *, provider_config=None, environment=None,
             bridge=None, new_only=False, recovery=None, background=_CAPTURE_BACKGROUND,
             previous_context=None):
        """Build and publish one session after all resources are ready."""

        identifier(session_id)
        assert_writable(agent_root(self.project), session_id)
        from ..storage.record_removal import assert_present

        assert_present(agent_root(self.project), session_id)
        bound = self._capture_context(context)
        descriptor_snapshot = self._capture_descriptor(descriptor, bound)
        captured_environment = self._capture_environment(environment)
        config = (freeze(json_copy(provider_config)) if recovery is not None else
                  self._resolve_config(provider_config, captured_environment))
        # Serialize ownership changes separately from the listener's short registry lock.
        with self._mutation:
            assert_present(agent_root(self.project), session_id)
            with self._lock:
                if self._closed:
                    raise ValueError("Project session owner is closed")
                existing = self._bundles.get(session_id)
                if existing is None and len(self._bundles) >= 64:
                    raise ValueError("Project session limit reached")
                self._opening += int(existing is None)
            if existing is not None:
                if existing.controller.closing or existing.controller._shutdown.is_set():
                    raise ValueError("会话正在结束，不能重新关联执行")
                if (existing.dependencies.bridge_descriptor != descriptor_snapshot
                        or existing.dependencies.provider_config != config
                        or existing.dependencies.environment != captured_environment
                        or existing.controller.inbox.initial != bound):
                    raise ValueError("Session is already open with different captured dependencies")
                return existing.controller
            from .background_config import capture_settings

            provider = None
            supplied_bridge = bridge
            journal = loop = controller = None
            try:
                if background is _CAPTURE_BACKGROUND:
                    background = (capture_settings(captured_environment)
                                  if recovery is None else None)
                if new_only and (agent_root(self.project) / "sessions" / session_id).exists():
                    raise ValueError("历史会话只读；恢复执行需明确操作")
                provider = provider_from_config(
                    thaw(config) if config is not None else None,
                    environment=dict(captured_environment),
                )
                bridge = supplied_bridge or self._bridge_factory(thaw(descriptor_snapshot))
                journal = SessionJournal(self.project, session_id)
                original = previous_context or bound
                if journal.state is not None and journal.state.context != original:
                    raise ValueError("Session belongs to another captured target")
                register_target = getattr(bridge, "register_target", None)
                if callable(register_target):
                    register_target(bound)
                tools = context_tools(bridge, journal)
                loop = create_backend(provider, tools, journal, original)
                if previous_context is not None:
                    from .session_host_transition import transition_host

                    transition_host(loop, bound, descriptor)
                controller = SessionController(loop)
                controller.attach_targets(bridge)
                if recovery is not None:
                    from .recovered_session import RecoveredSession

                    controller.recovery = RecoveredSession(controller, self.project)
                    journal.append_session("session.recovered", dict(recovery,
                        runtime_id=controller.runtime_id), controller.current)
                elif not (journal.directory / NAME).exists():
                    from .project_identity import read_identity

                    try:
                        directory = agent_root(self.project) / "service"
                        project_id = read_identity(directory).project_id
                    except FileNotFoundError:
                        project_id = None  # Legacy in-process test owner; never service recovery.
                    save(journal.directory, capture(session_id, bound, thaw(config),
                        captured_environment, descriptor_snapshot, project_id,
                        background=background))
                dependencies = SessionDependencies(
                    config, captured_environment, descriptor_snapshot, background)
                bundle = _SessionBundle(dependencies, provider, bridge, journal, controller)
                bundle.resources = next((b.resources for b in self._bundles.values()
                                         if b.bridge is bridge), None) or ExitStack()
                bundle.health = SessionHealth(controller)
                with self._lock:
                    self._bundles[session_id] = bundle
                return controller
            except BaseException:
                close_partial(controller, loop, journal,
                                    bridge if supplied_bridge is None else None)
                raise
            finally:
                with self._lock:
                    self._opening -= 1

    def close(self, timeout=DEFAULT_CLEANUP_SECONDS):
        with self._lock:
            self._closed = True
        return self._cleanup.start().wait(timeout)

    def _release(self):
        with self._mutation:
            with self._lock:
                bundles = tuple(self._bundles.values())
            for bundle in bundles:
                close_bundle(bundle, bridge=False)
            for bundle in {id(b.bridge): b for b in bundles}.values():
                bundle.bridge.close()
                bundle.resources.close()
            with self._lock:
                self._bundles.clear()
            self.creation.clear()

    def pending_work(self):
        """Bounded in-memory observations; no ownership or controller I/O lock waits."""
        with self._lock:
            controllers = tuple(bundle.controller for bundle in self._bundles.values())
            opening = self._opening
        observations = [controller.pending_work() for controller in controllers]
        return PendingWork(
            sessions=len(controllers) + opening,
            **{key: sum(getattr(row, key) for row in observations)
               for key in ("queued", "approvals", "jobs", "reconcile")})

    def close_session(self, session_id):
        """Retire only after cleanup, excluding a same-ID writer during close."""
        identifier(session_id)
        with self._mutation:
            with self._lock:
                bundle = self._bundles.get(session_id)
            if bundle is None:
                return False
            shared = any(other is not bundle and other.bridge is bundle.bridge
                         for other in self._bundles.values())
            close_bundle(bundle, bridge=not shared)
            with self._lock:
                del self._bundles[session_id]
            return True

    def stop_observation(self, controller):
        with self._lock:
            bundle = self._bundles.get(controller.session_id)
        if bundle is not None and bundle.controller is controller and bundle.health is not None:
            bundle.health.close()
            bundle.health.wait()

    def own_resource(self, controller, close):
        with self._mutation:
            bundle = self._bundles.get(controller.session_id)
            if bundle is None or bundle.controller is not controller:
                raise ValueError("Session resource owner changed")
            bundle.resources.callback(close)

    def retire_session(self, controller, *, released=lambda: None):
        """Release a fully drained runtime; never dispose a replacement or sibling."""
        with self._mutation:
            bundle = self._bundles.get(controller.session_id)
            if bundle is None or bundle.controller is not controller or controller.busy:
                raise ValueError("Session retirement identity changed")
            shared = any(other is not bundle and other.bridge is bundle.bridge
                         for other in self._bundles.values())
            if not shared:
                bundle.bridge.close()
                bundle.resources.close()
            bundle.journal.close()
            released()
            self.creation.retain(controller, bundle.dependencies)
            with self._lock:
                del self._bundles[controller.session_id]

    def create_from(self, source_id, session_id, context):
        """Create from captured dependencies; local relay lifetime stays with this owner."""
        with self._mutation:
            source = self._bundles.get(source_id)
            if source is None or source.controller.closing:
                raise ValueError("Source session is unavailable")
            deps = source.dependencies
            return self.open(session_id, dict(deps.bridge_descriptor), context,
                provider_config=deps.provider_config, environment=deps.environment,
                bridge=source.bridge if not deps.bridge_descriptor else None, new_only=True,
                background=deps.background)

    @staticmethod
    def _capture_context(context):
        if isinstance(context, BoundContext):
            return BoundContext.from_record(context.record())
        return BoundContext.from_record(context)

    @staticmethod
    def _capture_descriptor(descriptor, context):
        if not isinstance(descriptor, dict):
            raise TypeError("Bridge descriptor requires a mapping")
        for key in ("instance_id", "generation"):
            if key in descriptor and descriptor[key] != getattr(context, key):
                raise ValueError("Bridge descriptor and session target identity mismatch")
        return freeze(json_copy(descriptor))

    def _capture_environment(self, environment):
        if environment is None:
            source = os.environ if self._default_environment is None else self._default_environment
        else:
            source = environment
        if not isinstance(source, dict) and not hasattr(source, "items"):
            raise TypeError("Session environment requires a mapping")
        return freeze(json_copy(dict(source)))

    def _resolve_config(self, provider_config, environment):
        if provider_config is not None:
            if not isinstance(provider_config, dict):
                raise TypeError("Session provider configuration requires a mapping")
            return freeze(json_copy(provider_config))
        if self._default_config is not None:
            return freeze(json_copy(self._default_config))
        return freeze(
            json_copy(resolve_provider_config(launch_dir=self.project,
                                              environment=dict(environment)))
        )

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
