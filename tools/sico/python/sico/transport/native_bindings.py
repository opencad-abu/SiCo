"""Trusted native identities, finite output reservations and host lifecycle events."""

from pathlib import Path

from ..core.contracts import BoundContext, identifier
from .bindings import binding_error, resource_keys
from .circuit import encode_value

NATIVE_CONTRACT = "cad_ai_native_binding.v1"
CREATORS = {"aiCreateExecute", "aiTemplateSymbolCreate", "aiDrawSymbol", "aiSimCreate"}


def native_wire(identity):
    """Metadata is injected by the authenticated broker, never tool arguments."""
    if not isinstance(identity, dict) or identity.get("contract") != NATIVE_CONTRACT:
        raise ValueError("Native session identity is missing")
    fields = [identity[key] for key in ("session_id", "binding_id", "instance_id", "generation")]
    for value in fields:
        identifier(value)
    fields += [identity["cwd"], identity.get("task_id", ""), identity["request_id"]]
    if fields[5]:
        identifier(fields[5])
    identifier(fields[6])
    if not isinstance(fields[4], str) or not fields[4].startswith("/"):
        raise ValueError("Native project anchor must be absolute")
    fields.append(identity.get("resources", []))
    return encode_value(fields)


def output_context(source, params, request_id):
    """The output resource this call declares, never derived from focus.

    Creation/open/write bindings always carry an ``expected`` target. A guarded
    read that carries the operation's ``expected`` binding owns the same
    declared row, so the final native gate compares it against an authenticated
    reservation instead of an empty table.
    """
    function = params["function"]
    expected = params.get("expected")
    if function == "aiCopilotOpenTarget":
        names, path = params["values"][:3], None
    elif isinstance(expected, list) and len(expected) == 7:
        names, path = expected[2], expected[5]
    else:
        return None
    snapshot = dict(
        source="native_resource",
        cwd=source.snapshot.get("cwd"),
        cellview=dict(zip(("lib", "cell", "view"), names)),
    )
    if path:
        snapshot["library_path"] = str(Path(path).resolve())
    return BoundContext(source.instance_id, source.generation, "resource_" + request_id, snapshot)


class NativeBindings:
    """Broker lock protects request attestations and lifecycle admission."""

    def __init__(self, broker):
        self.broker = broker
        self.requests = {}
        self.invalidated = {}

    def dispatch(self, session_id, context, method, params, request_id, task_id="", resources=None):
        record = self.broker.bindings.require(session_id, context)
        identity = dict(
            contract=NATIVE_CONTRACT,
            session_id=session_id,
            binding_id=record.binding_id,
            instance_id=context.instance_id,
            generation=context.generation,
            cwd=record.anchor.snapshot.get("cwd"),
            task_id=task_id,
            request_id=request_id,
        )
        # Legacy synthetic peers without a captured cwd do not advertise native ownership.
        if not identity["cwd"]:
            return None
        reservation = (
            output_context(context, params, request_id) if method == "circuit_call" else None
        )
        outputs = []
        if resources is not None:
            if not isinstance(resources, list) or not 1 <= len(resources) <= 2:
                raise ValueError("Invalid native output resource count")
            for index, row in enumerate(resources):
                if (
                    not isinstance(row, list)
                    or len(row) != 4
                    or not all(isinstance(value, str) and value for value in row)
                    or not Path(row[3]).is_absolute()
                ):
                    raise ValueError("Invalid native output resource")
                outputs.append(
                    BoundContext(
                        context.instance_id,
                        context.generation,
                        "resource_" + request_id + "_" + str(index),
                        dict(
                            source="native_resource",
                            cwd=identity["cwd"],
                            cellview=dict(zip(("lib", "cell", "view"), row[:3])),
                            library_path=str(Path(row[3]).resolve()),
                        ),
                    )
                )
            if reservation and (
                outputs[0].snapshot["cellview"] != reservation.snapshot["cellview"]
                or (
                    reservation.snapshot.get("library_path")
                    and outputs[0].snapshot["library_path"] != reservation.snapshot["library_path"]
                )
            ):
                declared = [
                    (reservation.snapshot.get("cellview") or {}).get(key)
                    for key in ("lib", "cell", "view")
                ] + [reservation.snapshot.get("library_path") or ""]
                resolved = [
                    outputs[0].snapshot["cellview"].get(key) for key in ("lib", "cell", "view")
                ] + [outputs[0].snapshot.get("library_path") or ""]
                error = binding_error(
                    "binding_reservation_changed",
                    "Output library resolution changed: declared "
                    + repr(declared)
                    + " resolved "
                    + repr(resolved),
                    context,
                )
                error.data.update(declared_resource=declared, resolved_resource=resolved)
                raise error
        elif reservation:
            outputs = [reservation]
        identity["resources"] = resources or []
        if outputs:
            if len(self.requests) >= 4096 and request_id not in self.requests:
                raise binding_error(
                    "binding_resource_limit", "Native event evidence limit reached", context
                )
            self.broker.binding_events.reserve_many(session_id, outputs, request_id)
            self.requests[request_id] = dict(
                identity=identity,
                source=context,
                resources=outputs,
                function=(params or {}).get("function"),
            )
        return identity

    def event(self, message):
        kind = message.get("kind")
        if kind == "binding.native_closed":
            context = BoundContext.from_record(message["context"])
            peer = self.broker.peers.get(context.instance_id)
            if not peer or peer.generation != context.generation:
                raise ValueError("Native close belongs to another generation")
            captured = peer.contexts.get(context.target_id)
            # The desktop lease may release a target before its native window
            # closes. There is no remaining identity to invalidate in that case.
            if captured is None:
                return None
            if captured != context:
                raise ValueError("Native close target identity changed")
            key = (context.instance_id, context.generation, context.target_id)
            self.invalidated[key] = context
            # invalidate_target is idempotent per session. Let it finish any
            # owners whose earlier journal publication failed.
            self.broker.binding_events.invalidate_target(
                context, message.get("reason", "window_closed")
            )
            return None
        if kind != "binding.native_created":
            raise ValueError("Unsupported native binding event")
        request_id = identifier(message.get("request_id"))
        request = self.requests.get(request_id)
        if not request:
            raise ValueError("Native creation has no authenticated origin")
        identity = request["identity"]
        record = self.broker._sessions.get(identity["session_id"])
        if (
            not record
            or record.releasing
            or record.binding_id != identity["binding_id"]
            or message.get("binding_id") != record.binding_id
            or message.get("session_id") != identity["session_id"]
        ):
            raise ValueError("Native binding belongs to a retired logical session")
        context = BoundContext.from_record(message["context"])
        if (context.instance_id, context.generation, context.snapshot.get("cwd")) != (
            identity["instance_id"],
            identity["generation"],
            identity["cwd"],
        ):
            raise ValueError("Native output belongs to another project or generation")
        resources = request["resources"]
        resource = next(
            (
                row
                for row in resources
                if row.snapshot["cellview"] == context.snapshot.get("cellview")
            ),
            None,
        )
        if (
            not resource
            or not resource_keys(resource) & resource_keys(context)
            or (
                resource.snapshot.get("library_path")
                and (
                    not context.snapshot.get("library_path")
                    or str(Path(context.snapshot["library_path"]).resolve())
                    != resource.snapshot["library_path"]
                )
            )
        ):
            raise binding_error(
                "binding_event_changed", "Native output differs from reserved resource", context
            )
        created = (
            message.get("created_by_workflow") is True
            and request["function"] in CREATORS
            and message.get("verified") is True
        )
        if request.get("event_target"):
            if request["event_target"] != context:
                raise ValueError("Native creation event changed after publication")
            return request["proposal"]
        self.broker.register_target(context)
        proposal = self.broker.propose_binding(
            identity["session_id"],
            context,
            task_id=identity["task_id"] or request_id,
            origin_request_id=request_id,
            reason=message.get("reason", "Native workflow opened this target"),
            created_by_workflow=created,
        )
        request.update(event_target=context, proposal=proposal)
        return proposal
