"""Wire values for observing and explicitly changing one session's control lease."""

from dataclasses import asdict, dataclass

from ..transport.framing import ProtocolError
from .service_protocol import MAX_REQUEST_ID, control_claim, exact_fields
from .service_session_dto import SessionAddress
from .service_values import integer

ACTIONS = {"status", "acquire", "resume", "takeover", "release"}
STATES = {"available", "owned", "recoverable", "occupied", "detached"}


class ControlRefused(ValueError):
    """Explicit control refusal; never a transport or persistence failure."""


def control_params(params):
    exact_fields(params, {"address", "action", "generation", "previous"})
    address = SessionAddress.from_record(params["address"])
    if address.session.runtime_id is None:
        raise ProtocolError("History cannot hold a control lease")
    if not isinstance(params["action"], str) or params["action"] not in ACTIONS:
        raise ProtocolError("Invalid control action")
    integer(params["generation"])
    if params["generation"] > MAX_REQUEST_ID:
        raise ProtocolError("Control generation exceeds its budget")
    proof = params["previous"]
    if proof is not None:
        exact_fields(proof, {"client_id", "session_id", "runtime_id", "generation", "lease_id"})
        claim = control_claim(proof, proof["client_id"])
        if (claim.session_id, claim.runtime_id) != (address.session.session_id,
                                                   address.session.runtime_id):
            raise ProtocolError("Control proof belongs to another runtime")
    if (params["action"] in {"resume", "release"}) != (proof is not None):
        raise ProtocolError("Control action requires its captured proof")
    return address


@dataclass(frozen=True)
class ControlView:
    address: SessionAddress
    generation: int
    state: str
    claim: object = None

    def __post_init__(self):
        if type(self.address) is not SessionAddress or self.address.session.runtime_id is None:
            raise ProtocolError("Invalid control address")
        integer(self.generation)
        if self.generation > MAX_REQUEST_ID:
            raise ProtocolError("Control generation exceeds its budget")
        if not isinstance(self.state, str) or self.state not in STATES:
            raise ProtocolError("Invalid control state")
        if self.claim is not None:
            claim = control_claim(asdict(self.claim), self.claim.client_id)
            token = self.address.session
            if (self.state not in {"owned", "recoverable"} or claim.generation != self.generation
                    or (claim.session_id, claim.runtime_id) !=
                    (token.session_id, token.runtime_id)):
                raise ProtocolError("Invalid control grant")

    def record(self):
        return dict(address=self.address.record(), generation=self.generation, state=self.state,
                    claim=None if self.claim is None else asdict(self.claim))

    @classmethod
    def from_record(cls, row):
        exact_fields(row, {"address", "generation", "state", "claim"})
        proof = row["claim"]
        if proof is not None:
            exact_fields(proof, {"client_id", "session_id", "runtime_id", "generation", "lease_id"})
            proof = control_claim(proof, proof["client_id"])
        return cls(SessionAddress.from_record(row["address"]), row["generation"],
                   row["state"], proof)


def validate_control_result(view, params, client_id):
    if view.address.record() != params["address"]:
        raise ProtocolError("Control result belongs to another session")
    grants = params["action"] in {"acquire", "resume", "takeover"}
    if grants != (view.claim is not None):
        raise ProtocolError("Control result changed its authority contract")
    if view.claim is not None and (view.claim.client_id != client_id or view.state != "owned"):
        raise ProtocolError("Control grant belongs to another client")
    if params["action"] != "status" and view.generation != params["generation"] + 1:
        raise ProtocolError("Control generation did not advance exactly once")
    if params["action"] == "release" and view.state != "available":
        raise ProtocolError("Control release did not relinquish authority")
