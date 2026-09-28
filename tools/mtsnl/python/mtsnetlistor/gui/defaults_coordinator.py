"""Own queued defaults probes and reject receipts for superseded drafts."""

from dataclasses import dataclass, replace

from ..defaults import DefaultsProbeRequest, SourceDefaults, source_defaults_from_report
from .cell_drafts import DraftRevision


@dataclass(frozen=True)
class DefaultsRequestContext:
    source: object
    cell_key: tuple
    dialect: str
    revision: DraftRevision
    automatic_probe: bool = True


class DefaultsCoordinator:
    def __init__(self, drafts, controller, source, environment, log):
        self.drafts, self.controller = drafts, controller
        self.source, self.environment, self.log = source, environment, log
        self.cache, self.baselines = {}, {}
        self.pending, self.queue, self.dialects = {}, [], {}
        self.active = None
        self.last_report = None
        self._updates = []

    def take_updates(self):
        updates, self._updates = tuple(self._updates), []
        return updates

    @property
    def busy(self):
        return self.active is not None or bool(self.queue) or any(self.dialects.values())

    def cancel(self):
        active = self.active
        self.active = None
        self.queue.clear()
        self.dialects.clear()
        self.pending.clear()
        if active is not None and self.controller.state.stage == "probing_defaults":
            self.controller.cancel()

    def reset(self):
        self.cancel()
        self.cache.clear()
        self.baselines.clear()
        self.last_report = None
        self._updates.clear()

    def remove(self, key):
        context = self.pending.get(self.active)
        if context is not None and context.cell_key == key:
            self.pending.pop(self.active, None)
            self.active = None
            if self.controller.state.stage == "probing_defaults":
                self.controller.cancel()
        self.queue = [item for item in self.queue if item != key]
        self.dialects.pop(key, None)
        self.cache.pop(key, None)

    def apply(self, report, *, requested_key=None, automatic_probe=False):
        original = report
        # Legacy raw-report adapter; retire when scripted callers use SourceDefaults.
        if not isinstance(report, SourceDefaults):
            try:
                source = report.source
                key = tuple(str(source.get(name, "")) for name in ("library", "cell", "view"))
                requested_source = self.source(key).validate()
                if str(requested_source.cds_lib) != source.get("cds_lib"):
                    raise ValueError("source cds.lib does not match the current project")
                report = source_defaults_from_report(report, requested_source)
            except Exception as exc:
                self.log(f"Discarded invalid PDK defaults report: {type(exc).__name__}: {exc}")
                return None
        key, dialect = report.source_key, report.dialect or "spectre"
        if requested_key is not None and key != requested_key:
            self.log("Discarded PDK defaults with mismatched source identity")
            return None
        if key not in self.drafts:
            self.log(f"Discarded PDK defaults for {'/'.join(key)}; the Source Cell was removed")
            return None
        self.last_report = original
        self.cache.setdefault(key, {})[dialect] = report
        if automatic_probe:
            self.baselines.setdefault(dialect, report)
        if not self.drafts.apply_defaults(key, dialect, report, automatic=automatic_probe):
            self.log(f"PDK defaults available for {key[1]} (user edits preserved)")
            return None
        self.log(f"Loaded PDK runtime defaults ({report.provider}) for {'/'.join(key)} ({dialect})")
        for detail in report.diagnostics:
            self.log(f"Defaults diagnostic: {detail}")
        self._updates.append((key, dialect))
        return key, dialect

    def reuse(self, key, dialect):
        if dialect in self.cache.get(key, {}):
            return True
        template = self.baselines.get(dialect)
        if template is None or self.drafts.dirty(key, dialect):
            return False
        try:
            source = self.source(key).validate()
        except Exception:
            return False
        self.apply(replace(template, source=source), requested_key=key, automatic_probe=True)
        reused = dialect in self.cache.get(key, {})
        if reused:
            self.log(f"Reused cached PDK defaults for {'/'.join(key)} ({dialect})")
        return reused

    def enqueue(self, key, dialects):
        if key not in self.drafts:
            return
        pending = self.dialects.setdefault(key, [])
        for dialect in dialects:
            if dialect in ("spectre", "hspiceD") and not self.reuse(key, dialect):
                if dialect not in pending:
                    pending.append(dialect)
        context = self.pending.get(self.active)
        if pending and key not in self.queue and (context is None or context.cell_key != key):
            self.queue.append(key)
        self.advance()

    def advance(self):
        if self.active is not None or self.controller.state.busy:
            return
        while self.queue:
            key = self.queue.pop(0)
            if key not in self.drafts:
                continue
            pending = self.dialects.setdefault(key, [])
            for dialect in tuple(pending):
                if self.reuse(key, dialect):
                    pending.remove(dialect)
                elif self.drafts.dirty(key, dialect):
                    pending.remove(dialect)
                    self.log(f"Skipped automatic PDK defaults for {'/'.join(key)} ({dialect}); user edits preserved")
            if not pending:
                continue
            dialect = pending.pop(0)
            try:
                request = DefaultsProbeRequest(self.source(key), dialect).validate()
                options = {"dialect": dialect}
                environment = self.environment()
                if environment is not None:
                    options["environ"] = environment
                token = self.controller.read_source_defaults(request.source, **options)
                self.pending[token] = DefaultsRequestContext(
                    request.source, key, dialect, self.drafts.revision(key, dialect))
                self.active = token
                self.log(f"Reading PDK runtime defaults for {'/'.join(key)} ({dialect})")
                return
            except Exception as exc:
                pending.insert(0, dialect)
                self.queue.insert(0, key)
                self.log(f"PDK defaults failed for {'/'.join(key)}: {type(exc).__name__}: {exc}")
                return

    def _current(self, context):
        if not self.drafts.matches(context.cell_key, context.dialect, context.revision,
                                   automatic=context.automatic_probe):
            return False
        try:
            return self.source(context.cell_key).validate() == context.source
        except Exception:
            return False

    def receive(self, state):
        active = self.active
        finished = active is not None and state.token == active and not state.busy
        superseded = active is not None and state.token > active
        context = self.pending.get(active)
        applied = None
        if state.defaults is not None and state.defaults is not self.last_report:
            captured = self.pending.pop(state.token, None)
            report = state.defaults
            report_source = getattr(report, "source", None)
            if captured is None:
                self.log("Discarded PDK defaults without an active request context")
            else:
                matching_source = report_source == captured.source
                if hasattr(report_source, "get"):
                    matching_source = all(report_source.get(name) == str(getattr(captured.source, name))
                                          for name in ("cds_lib", "library", "cell", "view"))
                if (not self._current(captured) or not matching_source
                        or getattr(report, "dialect", "") != captured.dialect):
                    self.log("Discarded stale PDK defaults; source, simulator, or requesting cell settings changed")
                else:
                    applied = self.apply(report, requested_key=captured.cell_key,
                                         automatic_probe=captured.automatic_probe)
                    self.log(f"Defaults report: {report.report_path or 'in-memory'}")
        if state.error:
            self.pending.pop(state.token, None)
        if finished or superseded:
            self.pending.pop(active, None)
            self.active = None
            if (context is not None and context.cell_key in self.drafts
                    and self.drafts.revision(context.cell_key, context.dialect).incarnation
                    == context.revision.incarnation):
                pending = self.dialects.setdefault(context.cell_key, [])
                if (superseded and context.dialect not in self.cache.get(context.cell_key, {})
                        and not self.drafts.dirty(context.cell_key, context.dialect)
                        and context.dialect not in pending):
                    pending.insert(0, context.dialect)
                if pending and context.cell_key not in self.queue:
                    self.queue.insert(0, context.cell_key)
        return applied
