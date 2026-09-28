"""Own frozen runs, artifact receipts and cancellable publication handoffs."""

from dataclasses import dataclass

from ..config import canonical_request_digest
from ..workflow import MultiGenerationResult
from .draft_requests import target_free_request


@dataclass(frozen=True)
class PendingRun:
    request: object
    revision: int
    serial: int
    publication: object = None
    multiple: bool = False


@dataclass(frozen=True)
class AcceptedGeneration:
    result: object
    request: object


@dataclass(frozen=True)
class PublicationHandoff:
    serial: int
    revision: int
    generation: object
    request: object


def generation_matches(result, request):
    if isinstance(result, MultiGenerationResult):
        value = request.validate()
        return len(result.cells) == len(value.selected_cells) and all(
            item.request.source.cds_lib == value.source.cds_lib
            and item.request.source.startup_file == value.source.startup_file
            and item.request.source.simrc == value.source.simrc
            and item.request.selected_cells[0] == spec
            and item.result.request_digest == canonical_request_digest(target_free_request(item.request))
            for item, spec in zip(result.cells, value.selected_cells))
    digest = getattr(result, "request_digest", None)
    return digest is not None and digest == canonical_request_digest(request)


class GenerationCoordinator:
    def __init__(self):
        self.revision = 0
        self.serial = 0
        self.pending = {}
        self.accepted = None
        self.displayed = None
        self.handoff = None

    @property
    def result(self):
        return self.accepted.result if self.accepted is not None else None

    @property
    def request(self):
        return self.accepted.request if self.accepted is not None else None

    def cancel(self):
        self.serial += 1
        self.handoff = None
        self.pending.clear()

    def invalidate(self, retained=None):
        self.revision += 1
        self.cancel()
        self.accepted = None
        self.displayed = retained

    def begin(self):
        self.cancel()
        self.accepted = None

    def submitted(self, token, request, *, publication=None, multiple=False):
        self.pending[token] = PendingRun(request, self.revision, self.serial, publication, multiple)

    def receive(self, state):
        if state.error:
            self.pending.pop(state.token, None)
        result = state.generation
        if result is None or result is self.displayed:
            return None
        self.displayed = result
        captured = self.pending.pop(state.token, None)
        if captured is None:
            for token, candidate in tuple(self.pending.items()):
                if token < state.token and generation_matches(result, candidate.request):
                    captured = self.pending.pop(token)
                    break
        multiple = isinstance(result, MultiGenerationResult)
        if (captured is None or captured.revision != self.revision
                or captured.serial != self.serial or captured.multiple != multiple
                or not generation_matches(result, captured.request)):
            self.accepted = None
            return False
        self.accepted = AcceptedGeneration(result, captured.request)
        if captured.publication is not None:
            self.handoff = PublicationHandoff(captured.serial, captured.revision,
                                              result, captured.publication)
        return True

    def take_publication(self, *, canceled=False):
        pending, self.handoff = self.handoff, None
        if (pending is None or canceled or pending.serial != self.serial
                or pending.revision != self.revision or pending.generation is not self.result):
            return None
        return pending.request
