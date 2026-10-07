"""Local durable intent/response records; never a substitute for an OA transaction."""

import fcntl
from sicolock import lock as state_lock
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from sicostate import absolute, project_directory

from .circuit_spec_schema import CircuitSpecError, canonical, digest
from .project_contract import journal_record
from .project_schema import OPERATIONS

MAX_RECORD_BYTES = 512 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


class OperationJournal:
    def __init__(self, workspace):
        if workspace is None:
            raise CircuitSpecError("circuit operations require an explicit workspace")
        self.workspace = absolute(workspace)

    @property
    def root(self):
        return project_directory(self.workspace, "ai/circuit_operations")

    def key(self, operation, request_id):
        return OPERATIONS[operation] + request_id

    def path(self, key):
        return self.root / (digest(key) + ".json")

    def read(self, key):
        path = self.path(key)
        try:
            if path.is_symlink() or path.stat().st_size > MAX_RECORD_BYTES:
                raise CircuitSpecError("invalid or oversized circuit operation record")
            data = json.loads(path.read_text(encoding="utf-8"))
            return journal_record(data, key)
        except FileNotFoundError:
            return None
        except (ValueError, TypeError) as exc:
            raise CircuitSpecError("invalid circuit operation record") from exc

    @contextmanager
    def lock(self, key):
        project_directory(self.workspace, "ai/circuit_operations", create=True)
        # One short nonblocking workspace lock also serializes capacity checks.
        fd = os.open(self.root / ".journal.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                state_lock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise CircuitSpecError(
                    "operation in progress; query offline retained status"
                ) from exc
            yield
        finally:
            os.close(fd)

    def write(self, record):
        text = canonical(record)
        if len(text.encode("utf-8")) > MAX_RECORD_BYTES:
            raise CircuitSpecError("circuit operation record exceeds 512 KiB")
        if (
            not self.path(record["key"]).exists()
            and sum(1 for _ in self.root.glob("*.json")) >= 4096
        ):
            raise CircuitSpecError(
                "4096 circuit operation records retained; archive completed workspace"
            )
        fd, temporary = tempfile.mkstemp(prefix=".operation-", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path(record["key"]))
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
