"""Session creation and control-authorized durable input admission."""

from .frontend_session import SessionToken
from .host_admission import HostAdmission
from .service_business_dto import InputResult, SessionResult
from .service_inputs import ServiceInputs
from .service_session_dto import SessionAddress
from ..storage.roots import agent_root


class ServiceBusiness:
    def __init__(self, owner, descriptor, control):
        self.owner, self.descriptor = owner, descriptor
        self.control = control
        self.inputs = ServiceInputs(owner, descriptor, control)
        self.host = HostAdmission(owner, descriptor, self.inputs)

    def execute(self, request, connection=None):
        params, method = request["params"], request["method"]
        if method == "session.control":
            return self.control.execute(connection, params).record()
        if method.startswith("host."):
            return self.host.execute(request).record()
        if method == "session.open":
            # This is a live creation endpoint, never an implicit history/recovery open.
            path = agent_root(self.owner.project) / "sessions" / params["session_id"]
            if params["session_id"] not in self.owner.controllers and path.exists():
                return SessionResult("rejected", "history_readonly").record()
            controller = self.owner.open(params["session_id"], params["bridge"], params["context"],
                                         provider_config=params["provider_config"],
                                         environment=params["environment"], new_only=True)
            address = SessionAddress(self.descriptor.project_id, self.descriptor.service_id,
                                     SessionToken(controller.session_id, controller.runtime_id))
            return SessionResult("opened", "", address).record()
        address = SessionAddress.from_record(params["address"])
        if method == "session.submit":
            return self.inputs.controlled(address, request["operation_id"], params["text"],
                params["context"], connection, request["control"]).record()
        return self.inputs.query(address, request["operation_id"]).record()

    @staticmethod
    def failure(request, outcome, code):
        if request["method"] == "session.control":
            return {"error": "control_refused"}
        if request["method"] == "session.open":
            return SessionResult(outcome, code).record()
        address = (None if request["method"].startswith("host.") else
                   SessionAddress.from_record(request["params"]["address"]))
        return InputResult(address,
                           request["operation_id"], outcome, code).record()
