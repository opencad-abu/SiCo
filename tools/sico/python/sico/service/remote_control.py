"""Client publication of control observations and captured command proofs."""

import time
from concurrent.futures import Future
from dataclasses import asdict

from ..transport.framing import ProtocolError
from .control_contract import ControlView, validate_control_result


class RemoteControl:
    def __init__(self, api):
        self.api = api
        self._views = {}
        self._pending = None
        self._queried = 0

    def view(self, token):
        value = self._views.get(token)
        return value[0] if value else None

    def writable(self, token):
        value = self._views.get(token)
        return bool(value and value[0].claim is not None and value[0].state == "owned"
                    and self.api.owns(token)
                    and not self.api.is_closing(token)
                    and value[1] == self.api._commands.epoch
                    and not self.api._commands.retiring.is_set() and not self.api.detached)

    def proof(self, token):
        if not self.writable(token):
            return None
        return asdict(self._views[token][0].claim)

    def ensure(self, token):
        """Obtain a disconnected/free lease; never take authority from a live holder."""
        result = Future()
        pending = []

        def cancel(future):
            if future.cancelled():
                for receipt in pending:
                    receipt.discard()

        def complete(receipt, *, status=False):
            if result.cancelled():
                receipt.discard()
                return
            try:
                view = receipt.result()
                if status and not self.writable(token):
                    action = {"available": "acquire", "recoverable": "resume",
                              "detached": "takeover"}.get(view.state)
                    if action is None:
                        raise ValueError("此会话仍由其他连接使用，稍后重试")
                    follow = self.request(token, action)
                    pending.append(follow)
                    follow.add_done_callback(complete)
                    return
                if not self.writable(token):
                    raise ValueError("会话连接已变化，请重试")
                result.set_result(view)
            except Exception as exc:
                if not result.done():
                    result.set_exception(exc)

        result.add_done_callback(cancel)
        receipt = self.request(token)
        pending.append(receipt)
        receipt.add_done_callback(lambda value: complete(value, status=True))
        return result

    def request(self, token, action="status"):
        self.api.handle(token)
        captured = self.view(token)
        address = self.api.address(token)
        previous = (asdict(captured.claim) if captured and captured.claim else None)
        params = dict(address=address.record(), action=action,
                      generation=captured.generation if captured else 0,
                      previous=previous if action in {"resume", "release"} else None)
        epoch = self.api._commands.epoch

        def publish(row):
            self.api.handle(token)
            view = ControlView.from_record(row)
            validate_control_result(view, params, self.api._commands.client_id)
            if epoch != self.api._commands.epoch:
                raise ProtocolError("Control observation belongs to another connection")
            # Status never grants a credential. Retain only this client's matching proof.
            old = self._views.get(token)
            if (action == "status" and old is not None and view.generation == old[0].generation
                    and view.state in {"owned", "recoverable"}):
                retained = old[0].claim
                self._views[token] = (ControlView(address, view.generation, view.state, retained),
                                      old[1])
            else:
                self._views[token] = (view, epoch)
            return view
        return self.api._commands.request("control", params, publish,
            discard=(lambda view: self._discard(token, view, epoch))
            if action in {"acquire", "resume", "takeover"} else None)

    def _discard(self, token, view, epoch):
        if epoch != self.api._commands.epoch:
            return
        current = self.view(token)
        if current is not None and current.generation == view.generation:
            self.api._commands.retire_connection()

    def created(self, token, row):
        self.continued(token, row, new=True)

    def continued(self, token, row, *, new=False):
        view = ControlView.from_record(row)
        if (view.address != self.api.address(token) or view.claim is None
                or view.claim.client_id != self.api._commands.client_id
                or new and view.generation != 1 or view.state != "owned"):
            raise ProtocolError("New session control belongs to another client")
        self._views[token] = view, self.api._commands.epoch

    def refresh(self, token):
        if token is None or token.runtime_id is None:
            return
        if (time.monotonic() - self._queried < .5
                or self._pending is not None and not self._pending.done()):
            return
        self._queried = time.monotonic()
        self._pending = self.request(token)

    def end(self, token, operation_id, *, observe=False):
        from .session_end_contract import SessionEndView

        address = self.api.address(token)
        def publish(row):
            result = SessionEndView.from_record(row, address, operation_id)
            if result.state != "unknown" and self.api.owns(token):
                self.api._sessions.mark_closing(token)
            return result
        return self.api._commands.request("end_status" if observe else "end_session",
            dict(address=address.record(), operation_id=operation_id), publish,
            operation_id=None if observe else operation_id,
            control=None if observe else self.proof(token))
