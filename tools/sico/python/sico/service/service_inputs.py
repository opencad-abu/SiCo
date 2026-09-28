"""Service input admission over the existing controller and durable inbox authority."""

import hashlib
import json
from contextlib import nullcontext

from ..core.contracts import BoundContext
from ..storage.inbox_records import inbox_directory, read_input
from ..transport.framing import ProtocolError
from .published import thaw
from .service_business_dto import InputResult


class ServiceInputs:
    def __init__(self, owner, descriptor, control):
        self.owner, self.descriptor = owner, descriptor
        self.control = control

    def _controller(self, address):
        controller = self.owner.controllers.get(address.session.session_id)
        if (address.service_id != self.descriptor.service_id or controller is None
                or controller.runtime_id != address.session.runtime_id):
            return None
        return controller

    def _read(self, address, input_id):
        if address.project_id != self.descriptor.project_id:
            raise ProtocolError("Input belongs to another project")
        directory = inbox_directory(self.owner.project, address.session.session_id)
        row = read_input(directory, input_id, confirm=True)
        if row is not None and row.get("service_address") != address.record():
            raise ProtocolError("Durable input belongs to another session runtime")
        return row

    def _result(self, address, input_id, row):
        if row is None:
            return InputResult(address, input_id, "unknown")
        controller = self._controller(address)
        live = False
        if controller is not None:
            live = (any(item["id"] == input_id for item in controller.inbox.pending)
                    or (controller.inbox.active or {}).get("id") == input_id)
            live = live and not controller.fault and not controller.closing
        recovery = row["status"] in {"queued", "executing", "waiting_user"} and not live
        return InputResult(address, input_id, "accepted", execution=row["status"],
                           task_id=row.get("task_id"), recovery_required=recovery)

    def query(self, address, input_id):
        controller = self._controller(address)
        with controller._lock if controller is not None else nullcontext():
            return self._result(address, input_id, self._read(address, input_id))

    def submit(self, address, input_id, text, context, authorize=None, *, extras=None):
        return self._admit(address, input_id, text, context, authorize=authorize, extras=extras)

    def controlled(self, address, input_id, text, context, connection, proof, *, extras=None):
        controller = self._controller(address)
        options = {"extras": extras} if extras else {}
        if controller is None or controller.closing or controller._shutdown.is_set():
            return self.submit(address, input_id, text, context, **options)
        with self.control.guard(address) as entry:
            return self.submit(address, input_id, text, context,
                               lambda: self.control.require(entry, connection, proof), **options)

    def accept(self, address, input_id, text, context):
        # Authenticated, target-bound host intake does not acquire GUI control.
        controller = self._controller(address)
        if controller is None or controller.closing or controller._shutdown.is_set():
            return self._admit(address, input_id, text, context, quick=True)
        with self.control.guard(address):
            return self._admit(address, input_id, text, context, quick=True)

    def _admit(self, address, input_id, text, context, *, quick=False, authorize=None, extras=None):
        controller = self._controller(address)
        # Same authority as controller task transitions: the record and live queue
        # observation must describe one committed state, never a replaced inode.
        with controller._lock if controller is not None else nullcontext():
            return self._submit(address, input_id, text, context, controller, quick=quick,
                                authorize=authorize, extras=extras)

    def _submit(self, address, input_id, text, context, controller, *, quick=False, authorize=None,
                extras=None):
        extras = thaw(extras or {})
        if set(extras) - {"inputs", "attachment_text", "turn_options"}:
            raise ProtocolError("Invalid submission fields")
        # Match the user's original selection on retry, independently of later
        # sticky defaults or changed/deleted files. Asset snapshots stay in the
        # existing controller/inbox authority and are never created on retry.
        digest = (hashlib.sha256(json.dumps(extras, sort_keys=True, ensure_ascii=True,
                  allow_nan=False, separators=(",", ":")).encode("ascii")).hexdigest()
                  if extras else None)
        row = self._read(address, input_id)
        if row is not None:
            if (row["message"]["text"] != text or row["message"]["context"] != context
                    or row["message"].get("submission_digest") != digest):
                return InputResult(address, input_id, "rejected", "operation_conflict")
            return self._result(address, input_id, row)
        if controller is None:
            return InputResult(address, input_id, "rejected", "stale_session")
        if authorize is not None:
            authorize()
        elif not quick:
            from .session_control import ControlRefused

            raise ControlRefused("当前为只读观察，请明确获取控制权")
        try:
            if quick:
                controller.accept(dict(kind="submit", id=input_id, text=text, context=context),
                                  service_address=address.record())
            else:
                controller.submit(text, context=BoundContext.from_record(context),
                                  input_id=input_id, service_address=address.record(),
                                  submission_digest=digest, **extras)
        except ProtocolError:
            raise
        except ValueError:
            # A failure after persistence must not turn an accepted input into a refusal.
            row = self._read(address, input_id)
            if row is not None:
                return self._result(address, input_id, row)
            return InputResult(address, input_id, "rejected", "input_rejected")
        return self.query(address, input_id)
