"""Flat topology projection and a transient graph view for existing algorithms."""

from ..template_graph import fingerprint
from .codec import encode, fail, sha

KINDS = set(
    "nmos pmos resistor capacitor inductor diode pnp npn moscap_n moscap_p "
    "voltage_source current_source subcircuit unknown".split()
)
ATTRIBUTES = {"polarity", "device_class", "voltage_domain"}


def unknown(reason, state="unknown"):
    return dict(state=state, reason=reason)


def project(record, dependencies, source):
    old = record["topology"]
    top = dict(
        source=source,
        depends_on=["capture"],
        level="direct",
        masters={},
        devices={},
        nets={},
        ports={},
        connections={},
    )
    for row in old["devices"]:
        target = row["master"]
        # The capture proves the observed interface, not the full master contents.
        key = "m" + sha(encode(target))[7:23]
        dependencies.setdefault(
            key,
            dict(
                kind="master",
                target=target,
                fingerprint=unknown("Full master content fingerprint was not captured"),
                method="saved-master-interface-v1",
            ),
        )
        top["masters"][key] = dict(dependency=key)
        attrs = {k: v for k, v in row["attributes"].items() if k in ATTRIBUTES}
        top["devices"][row["id"]] = dict(
            kind=row["kind"] if row["kind"] in KINDS else "unknown",
            role=row["role"],
            master=key,
            terminals=sorted(p["name"] for p in row["pins"]),
            source_name=row["source_name"],
            **({"attributes": attrs} if attrs else {}),
        )
        for pin in row["pins"]:
            top["connections"]["c" + str(len(top["connections"]))] = dict(
                device=row["id"],
                terminal=pin["name"],
                net=pin["net"]
                if pin["net"] is not None
                else unknown("Saved endpoint has no net", "unconnected"),
            )
    for row in old["nets"]:
        top["nets"][row["id"]] = dict(
            name=row["source_name"],
            width=row["num_bits"],
            scope="unknown"
            if row["is_global"] is None
            else "global"
            if row["is_global"]
            else "local",
            signal_type=row["sig_type"]
            if row["sig_type"] in {"signal", "ground", "supply"}
            else "unknown",
        )
    for row in old["ports"]:
        top["ports"][row["id"]] = dict(
            name=row["name"],
            width=row["num_bits"],
            direction=row["direction"]
            if row["direction"] in {"input", "output", "inputOutput"}
            else "unknown",
        )
        top["connections"]["c" + str(len(top["connections"]))] = dict(
            port=row["id"],
            net=row["net"]
            if row["net"] is not None
            else unknown("Saved port has no net", "unconnected"),
        )
    top["depends_on"] = sorted(["capture", *top["masters"]])
    return top


def validate(top):
    devices, nets, ports = top["devices"], top["nets"], top["ports"]
    expected, found = set(), set()
    if top["level"] != "direct":
        fail("only direct topology is supported")
    for key, row in devices.items():
        if row["kind"] not in KINDS or row["role"] not in {"device", "hierarchy", "unknown"}:
            fail("unregistered device semantics")
        if set(row.get("attributes", {})) - ATTRIBUTES:
            fail("unregistered classification attributes")
        if isinstance(row["master"], str) and row["master"] not in top["masters"]:
            fail("missing master")
        if isinstance(row["terminals"], list):
            if len(set(row["terminals"])) != len(row["terminals"]):
                fail("duplicate terminal")
            expected.update((key, pin) for pin in row["terminals"])
    expected.update(("port", key) for key in ports)
    for row in top["connections"].values():
        if set(row) == {"device", "terminal", "net"}:
            endpoint = (row["device"], row["terminal"])
        elif set(row) == {"port", "net"}:
            endpoint = ("port", row["port"])
        else:
            fail("invalid connection endpoint shape")
        if endpoint not in expected or endpoint in found:
            fail("duplicate or absent connection endpoint")
        found.add(endpoint)
        if isinstance(row["net"], str):
            if row["net"] not in nets:
                fail("connection references absent net")
        elif not isinstance(row["net"], dict) or row["net"].get("state") not in {
            "unknown",
            "unconnected",
        }:
            fail("invalid connection state")
    if found != expected:
        fail("every known endpoint requires exactly one connection")
    for row in [*nets.values(), *ports.values()]:
        if type(row["width"]) is not int or row["width"] < 1:
            fail("invalid width")
    for row in ports.values():
        if "net" in row or row["direction"] not in {"input", "output", "inputOutput", "unknown"}:
            fail("invalid port direction/duplicate connectivity")
    for row in nets.values():
        if row["scope"] not in {"local", "global", "unknown"} or row["signal_type"] not in {
            "signal",
            "ground",
            "supply",
            "unknown",
        }:
            fail("invalid net semantics")


def graph(top):
    """In-memory compatibility only: connections remain the sole stored authority."""
    validate(top)
    connected = {}
    for row in top["connections"].values():
        endpoint = (row["device"], row["terminal"]) if "device" in row else ("port", row["port"])
        connected[endpoint] = row["net"] if isinstance(row["net"], str) else None
    out = dict(
        level="direct",
        gaps=[],
        net_global_semantics="uniform_boolean_v1",
        devices=[],
        nets=[],
        ports=[],
    )
    for key, row in top["devices"].items():
        pins = row["terminals"] if isinstance(row["terminals"], list) else []
        out["devices"].append(
            dict(
                id=key,
                kind=row["kind"],
                role=row["role"],
                source_name=row.get("source_name", key),
                attributes=row.get("attributes", {}),
                pins=[dict(name=p, net=connected.get((key, p))) for p in pins],
            )
        )
    for key, row in top["nets"].items():
        out["nets"].append(
            dict(
                id=key,
                source_name=row["name"],
                num_bits=row["width"],
                is_global=None if row["scope"] == "unknown" else row["scope"] == "global",
                sig_type=row["signal_type"],
            )
        )
    for key, row in top["ports"].items():
        out["ports"].append(
            dict(
                id=key,
                name=row["name"],
                num_bits=row["width"],
                direction=row["direction"],
                net=connected[("port", key)],
            )
        )
    out["fingerprint"] = fingerprint(out)
    return out
