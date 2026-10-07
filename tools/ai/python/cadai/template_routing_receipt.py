"""Bounded immutable routing evidence carried by the existing native create payload."""

from .circuit_spec_schema import CircuitSpecError, canonical, digest

SCHEMA = "cad.template.routing-receipt.v1"
FIELDS = ("schema", "template_ref", "record_digest", "reference_digest", "routing_digest",
          "wires_digest", "nets")


def receipt(plan):
    routing = plan["routing"]
    return [SCHEMA, routing["template_ref"], routing["record_digest"],
            routing["reference_digest"], digest(routing), digest(plan["wires"]),
            [[r["net"], r["status"], r["terminals"], r["reasons"]] for r in routing["nets"]]]


def decode_receipt(row):
    if not isinstance(row, list) or len(row) != len(FIELDS) or row[0] != SCHEMA:
        raise CircuitSpecError("native template routing evidence is malformed")
    result = dict(zip(FIELDS, row))
    for key in (
        "template_ref", "record_digest", "reference_digest", "routing_digest", "wires_digest"
    ):
        value = result[key]
        if not isinstance(value, str) or not value or len(value) > 256:
            raise CircuitSpecError("native template routing evidence has an invalid " + key)
    nets = result["nets"]
    if not isinstance(nets, list) or len(nets) > 256:
        raise CircuitSpecError("native template routing evidence has too many nets")
    decoded = []
    statuses = {"reused", "rerouted", "stub", "rejected"}
    for item in nets:
        if not isinstance(item, list) or len(item) != 4:
            raise CircuitSpecError("native template routing net evidence is malformed")
        net, status, terminals, reasons = item
        # The native SKILL value codec represents an empty Python list as nil;
        # normalize that one lossless case back to the public empty list. Any
        # non-empty reasons field still has to be an actual bounded list.
        if reasons is None:
            reasons = []
        if (not isinstance(net, str) or not net or len(net) > 256
                or status not in statuses or type(terminals) is not int
                or not 0 <= terminals <= 4096 or not isinstance(reasons, list)
                or len(reasons) > 32 or any(not isinstance(reason, str) or not reason
                                             or len(reason) > 256 for reason in reasons)):
            raise CircuitSpecError("native template routing net evidence is invalid")
        decoded.append({"net": net, "status": status, "terminals": terminals,
                        "reasons": list(reasons)})
    if len({canonical(n["net"]) for n in decoded}) != len(decoded):
        raise CircuitSpecError("native template routing evidence contains duplicate nets")
    result["nets"] = decoded
    return result
