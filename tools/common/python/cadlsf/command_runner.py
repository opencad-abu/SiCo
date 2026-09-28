"""Bounded, cancellable subprocess execution for LSF commands."""

from __future__ import annotations

from dataclasses import dataclass
import os
import signal
import subprocess
import tempfile
import time
from typing import BinaryIO, Callable, Mapping, Protocol, Sequence

STDOUT_LIMIT_BYTES = 4 * 1024 * 1024
STDERR_LIMIT_BYTES = 64 * 1024
_TRUNCATION_MARKER = b"\n[output truncated]\n"
TRUNCATION_TEXT = _TRUNCATION_MARKER.decode("ascii")
_PROCESS_POLL_INTERVAL = 0.01

class CollectorError(RuntimeError):
    """A safe, user-facing collector failure."""


class CollectorCancelled(RuntimeError):
    """Raised when a cooperative collector request is cancelled."""


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        ...


class SubprocessRunner:
    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._cancelled = cancelled

    @staticmethod
    def _process_group_exists(process: subprocess.Popen[bytes]) -> bool:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return False
        except OSError as exc:
            raise CollectorError(
                f"Cannot inspect LSF command process group: {exc}"
            ) from exc
        return True

    @staticmethod
    def _signal_process_group(
        process: subprocess.Popen[bytes], process_signal: int
    ) -> bool:
        try:
            os.killpg(process.pid, process_signal)
        except ProcessLookupError:
            return False
        except OSError as exc:
            raise CollectorError(
                f"Cannot stop LSF command process group: {exc}"
            ) from exc
        return True

    @classmethod
    def _stop_process(cls, process: subprocess.Popen[bytes]) -> None:
        if not cls._signal_process_group(process, signal.SIGTERM):
            return
        deadline = time.monotonic() + 0.5
        while cls._process_group_exists(process):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if process.poll() is None:
                try:
                    process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
            else:
                time.sleep(min(0.05, remaining))
        if cls._process_group_exists(process):
            cls._signal_process_group(process, signal.SIGKILL)
        if process.poll() is None:
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired as exc:
                raise CollectorError(
                    "Cannot reap terminated LSF command process"
                ) from exc

    def run(self, argv: Sequence[str], *, timeout: float) -> CommandResult:
        command = tuple(str(item) for item in argv)
        environment = dict(self._environ)
        environment["LC_ALL"] = "C"
        if self._cancelled is not None and self._cancelled():
            raise CollectorCancelled("LSF collection was cancelled")
        try:
            with tempfile.TemporaryFile() as stdout_stream:
                with tempfile.TemporaryFile() as stderr_stream:
                    try:
                        process = subprocess.Popen(
                            command,
                            stdout=stdout_stream,
                            stderr=stderr_stream,
                            env=environment,
                            start_new_session=True,
                        )
                    except FileNotFoundError as exc:
                        raise CollectorError(
                            f"LSF executable is unavailable: {command[0]}"
                        ) from exc
                    except OSError as exc:
                        raise CollectorError(
                            f"Cannot start LSF executable {command[0]}: {exc}"
                        ) from exc
                    deadline = time.monotonic() + timeout
                    while process.poll() is None:
                        if self._cancelled is not None and self._cancelled():
                            self._stop_process(process)
                            raise CollectorCancelled(
                                "LSF collection was cancelled"
                            )
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            self._stop_process(process)
                            raise CollectorError(
                                "LSF command timed out after "
                                f"{timeout:g} seconds: {command[0]}"
                            )
                        try:
                            process.wait(
                                timeout=min(_PROCESS_POLL_INTERVAL, remaining)
                            )
                        except subprocess.TimeoutExpired:
                            pass
                    returncode = process.returncode
                    stdout = _read_bounded_output(
                        stdout_stream, STDOUT_LIMIT_BYTES
                    )
                    stderr = _read_bounded_output(
                        stderr_stream, STDERR_LIMIT_BYTES
                    )
        except (CollectorCancelled, CollectorError):
            raise
        except OSError as exc:
            raise CollectorError(
                f"Cannot buffer LSF command output for {command[0]}: {exc}"
            ) from exc
        return CommandResult(
            command,
            returncode,
            stdout,
            stderr,
        )


def _read_bounded_output(stream: BinaryIO, limit: int) -> str:
    stream.seek(0)
    payload = stream.read(limit + 1)
    if len(payload) > limit:
        payload = payload[: limit - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    return payload.decode("utf-8", errors="replace")
