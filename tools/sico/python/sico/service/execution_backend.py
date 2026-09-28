"""Backend contract for one admitted saved-OA job; no queue or business scheduler."""

from dataclasses import dataclass
from typing import Callable, Protocol

from ..core.contracts import BoundContext, identifier, json_copy
from ..transport.framing import ProtocolError
from .published import freeze


@dataclass(frozen=True)
class ExecutionRequest:
    job_id: str
    worker_id: str
    owner_session_id: str
    origin: BoundContext
    method: str
    params: object
    environment: object = None

    def __post_init__(self):
        for value in (self.job_id, self.worker_id, self.owner_session_id):
            identifier(value)
        object.__setattr__(self, "origin", BoundContext.from_record(self.origin.record()))
        object.__setattr__(self, "params", freeze(json_copy(self.params)))
        from .background_environment import validate_environment

        object.__setattr__(self, "environment", freeze(validate_environment(
            {} if self.environment is None else self.environment)))


class JobExecution(Protocol):
    """One admitted operation. cancel requests cancellation, never proves a terminal state."""

    def run(self) -> dict: ...

    def cancel(self) -> None: ...

    def detach(self) -> None:
        """Release process ownership; remote jobs continue without observation."""
        ...


class ExecutionBackend(Protocol):
    kind: str

    @property
    def available(self) -> bool: ...

    def create(self, request: ExecutionRequest, progress: Callable) -> JobExecution:
        """Prepare in memory only. External execution is forbidden before run()."""
        ...

    def recover(self, row: dict) -> dict:
        """Reconcile retained evidence without submitting or replaying work."""
        ...

    def read_result(self, row: dict) -> dict: ...


def backend_kind(row):
    # Legacy B1/B2 records predate the discriminator. Remove this read adapter
    # only after such retained records have been explicitly migrated or archived.
    value = row.get("execution_backend", "local")
    if not isinstance(value, str) or value not in {"local", "lsf"}:
        raise ProtocolError("Invalid captured execution backend")
    return value


def validate_progress(row, receipt):
    if not isinstance(receipt, dict):
        raise ProtocolError("Invalid execution progress")
    for key in ("job_id", "worker_id"):
        if receipt.get(key) != row[key]:
            raise ProtocolError("Execution progress belongs to another job")
    for key in ("service_id", "owner_session_id", "origin", "method", "params", "target",
                "execution_backend"):
        if key in receipt and receipt[key] != row[key]:
            raise ProtocolError("Execution progress changed captured admission")
    if receipt.get("automatic_resume_allowed", False) is not False:
        raise ProtocolError("Execution progress cannot authorize automatic replay")
