"""Qt-free desktop command queue; receipt completion follows durable acceptance."""

from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import Future

from .replay import connection, prepare_preview, prepare_replay
from .session_tokens import SessionTokens


class WorkerApi:
    RESOURCE_COMMANDS = {"resource_status", "turn_input_status", "thread_status"}
    TURN_COMMANDS = {"steer", "elicitation"}
    COMMANDS = {"submit", "accept", "rename", "delete", "initialize_context", "attach_targets",
                "query_interrupted", "resolve_interrupted",
                "cancel", "cancel_router_request", "resume", "acknowledge_interrupted",
                "answer_audit", "router_receipt", "propose_binding", "resolve_binding",
                "select_binding_target", "release_binding", "configure_bindings",
                "release_binding_resource", "reconcile_binding_operation", "thread_operation"}

    def __init__(self, sessions):
        self.sessions = sessions
        self._tokens = SessionTokens(sessions)
        self._queue = queue.Queue(maxsize=64)
        self._lock = threading.Lock()
        self._pending = 0
        self._closing = False
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="copilot-commands", daemon=True)
        self._thread.start()

    @property
    def busy(self):
        with self._lock:
            return bool(self._pending) or (self._closing and not self._stopped.is_set())

    def _request(self, operation, *args, **kwargs):
        future = Future()
        with self._lock:
            if self._closing or self._stopped.is_set():
                raise ValueError("Desktop command worker is unavailable")
            try:
                self._queue.put_nowait((future, operation, args, kwargs))
            except queue.Full:
                raise ValueError("Desktop command queue is full") from None
            self._pending += 1
        return future

    def open_session(self, session_id):
        return self._request(self.sessions.open, session_id)

    def create_session(self, context):
        return self._request(self.sessions.create, context)

    def accept_quick(self, controller, message):
        handle = self._tokens.internal_token(controller)
        return self._request(self._accept_quick, handle, message)

    def _accept_quick(self, handle, message):
        return self.sessions.quick_input.accept(self._tokens.resolve(handle), message)

    def prepare_replay(self, controller, activation, messages):
        from .event_service import preparation_receipt

        token = self._tokens.internal_token(controller)
        controller = self._tokens.resolve(token)
        return preparation_receipt(
            self.sessions.events, self._prepare_replay, token, connection(controller),
            activation, messages,
        )

    def _prepare_replay(self, token, identity, activation, messages):
        return prepare_replay(self._tokens.resolve(token), identity, activation, messages,
                              self.sessions)

    def attach_targets(self, token):
        self._tokens.resolve(token)
        return self.command(token, "attach_targets", self.sessions.broker)

    def session_query(self, token, kind, *args, **kwargs):
        self._tokens.resolve(token)
        if kind == "tool_result":
            def query():
                owner = self._tokens.resolve(token)
                result = self.sessions.data._tool_result(owner.journal, *args, **kwargs)
                self._tokens.resolve(token)
                return result
            return self.sessions.data._request(query)
        if kind == "background":
            return self.sessions.background.request(
                *args, session_id=token.session_id,
                validate=lambda: self._tokens.resolve(token), **kwargs)
        raise ValueError("Unknown session query")

    def preview_session(self, session_id, activation=0, messages=0):
        from .event_service import preparation_receipt

        controller = self.sessions.controllers.get(session_id)
        runtime = controller.runtime_id if controller is not None else None
        return preparation_receipt(
            self.sessions.events, prepare_preview, self.sessions, session_id, runtime, activation,
            messages,
        )

    def event_batch(self, stream):
        return stream.request(self.sessions.events)

    def event_detail(self, stream, key, offset=0):
        return self.sessions.data._request(stream.detail, key, offset)

    def input_detail(self, stream, sequence, index=None):
        from .input_details import input_detail

        return self.sessions.data._request(input_detail, stream, sequence, index)

    def rename_session(self, session_id, name):
        return self._request(self._metadata_command, self._tokens.session(session_id),
                             "rename", session_id, name)

    def delete_session(self, session_id):
        return self._request(self._metadata_command, self._tokens.session(session_id),
                             "delete", session_id)

    def _metadata_command(self, expected, name, session_id, *args):
        if self._tokens.session(session_id) != expected:
            raise ValueError("Session runtime changed before metadata command")
        return getattr(self.sessions, name)(session_id, *args)

    def _session_command(self, handle, name, args, kwargs):
        return getattr(self._tokens.resolve(handle), name)(*args, **kwargs)

    def command(self, controller, name, *args, **kwargs):
        token = self._tokens.internal_token(controller)
        controller = self._tokens.resolve(token)
        if name not in self.COMMANDS | self.RESOURCE_COMMANDS | self.TURN_COMMANDS:
            raise ValueError("Unknown worker command")
        if name == "cancel":
            controller.interrupt()
        reservation = None
        if name == "submit" and kwargs.get("context") is not None:
            reservation = controller.reserve_submission(kwargs["context"])
            kwargs["reservation"] = reservation
        try:
            future = self._request(
                self._session_command, token, name, args, kwargs,
            )
        except Exception:
            if reservation:
                controller.release_submission(reservation)
            if name == "attach_targets":
                controller.request_close()
            raise
        if reservation:
            future.add_done_callback(lambda _future: controller.release_submission(reservation))
        return future

    def _run(self):
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                future, operation, args, kwargs = item
                try:
                    if future.set_running_or_notify_cancel():
                        try:
                            result = operation(*args, **kwargs)
                            if isinstance(result, Future):
                                # The turn consumer owns the RPC. Do not hold
                                # the command worker while its receipt arrives.
                                result.add_done_callback(
                                    lambda receipt, target=future: self._forward(receipt, target)
                                )
                            else:
                                future.set_result(result)
                        except BaseException as exc:
                            # Future callbacks can raise after completion. Do
                            # not complete that Future twice or retire sessions.
                            if not future.done():
                                try:
                                    future.set_exception(exc)
                                except BaseException:
                                    logging.getLogger(__name__).exception(
                                        "Desktop command completion callback failed"
                                    )
                            operation_name = (args[1] if operation == self._session_command
                                              else getattr(operation, "__name__", ""))
                            diagnostic = {
                                "attach_targets": "Quick input target attach failed",
                                "accept": "Quick input was not accepted",
                                "_accept_quick": "Quick input was not accepted",
                            }.get(operation_name)
                            if diagnostic:
                                logging.getLogger(__name__).warning(
                                    "%s: %s: %s", diagnostic, type(exc).__name__, exc,
                                )
                            elif not isinstance(exc, Exception):
                                logging.getLogger(__name__).exception("Desktop command aborted")
                finally:
                    with self._lock:
                        self._pending -= 1
                    try:
                        self.sessions.catalog.request()
                    except Exception:
                        logging.getLogger(__name__).exception("Desktop catalog refresh failed")
        except BaseException:
            logging.getLogger(__name__).exception("Desktop command worker stopped unexpectedly")
        finally:
            try:
                if self._closing:
                    self.sessions._close_controllers()
            except Exception:
                logging.getLogger(__name__).exception("Desktop cleanup failed")
            finally:
                self._stopped.set()

    @staticmethod
    def _forward(receipt, target):
        try:
            target.set_result(receipt.result())
        except BaseException as exc:
            if not target.done():
                try:
                    target.set_exception(exc)
                except BaseException:
                    logging.getLogger(__name__).exception(
                        "Desktop forwarded receipt callback failed")
            else:
                logging.getLogger(__name__).exception("Desktop forwarded receipt callback failed")

    def close(self):
        cancelled = []
        with self._lock:
            if self._closing:
                return
            self._closing = True
            while True:
                try:
                    future, _, _, _ = self._queue.get_nowait()
                except queue.Empty:
                    break
                cancelled.append(future)
                self._pending -= 1
            self._queue.put_nowait(None)
            if self._stopped.is_set():
                # Unexpected worker failure preserves live sessions. An
                # explicit close still has to drain and release them.
                self._stopped.clear()
                self._thread = threading.Thread(target=self._run,
                                                name="copilot-cleanup", daemon=True)
                self._thread.start()
        # Future callbacks can reenter queue APIs; never invoke them under its lock.
        for future in cancelled:
            future.cancel()
        self.sessions.catalog.close()
        for controller in tuple(self.sessions.controllers.values()):
            controller.request_close()

    def wait(self, timeout=None):
        self._thread.join(timeout)
        return self._stopped.is_set()
