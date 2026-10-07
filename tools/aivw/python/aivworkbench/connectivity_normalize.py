"""Canonical terminal bindings and scoped SI alias projection."""

from __future__ import annotations

from typing import Mapping, Sequence

from .connectivity_model import ConnectivityParseError, Instance, Module, Port


def canonicalize_module(
    module: Module, child_ports: Mapping[str, Sequence[Port]]
) -> dict[str, object]:
    """Convert positional and named instance bindings to terminal/net pairs."""
    instances: list[dict[str, object]] = []
    for instance in module.instances:
        ports = child_ports.get(instance.master)
        if not ports and any(key.isdigit() for key, _ in instance.connections):
            raise ConnectivityParseError(
                f"no port declaration for positional master {instance.master}"
            )
        values: dict[str, str] = {}
        for terminal, net in instance.connections:
            if terminal.isdigit():
                index = int(terminal)
                if index >= len(ports or ()):
                    raise ConnectivityParseError(
                        f"positional terminal {index} exceeds {instance.master} port count"
                    )
                terminal_name = ports[index].name  # type: ignore[index]
            else:
                terminal_name = terminal
            if terminal_name in values:
                raise ConnectivityParseError(
                    f"duplicate terminal {terminal_name} on {instance.name}"
                )
            values[terminal_name] = net
        instances.append(
            {
                "name": instance.name,
                "master": instance.master,
                "connections": tuple(sorted(values.items())),
            }
        )
    return {
        "name": module.name,
        "ports": tuple(port.name for port in module.ports),
        "instances": tuple(sorted(instances, key=lambda item: str(item["name"]))),
    }


def apply_aliases(module: Module, aliases: Mapping[str, str]) -> Module:
    """Map source/recipe spellings to SI's canonical generated names."""
    if not aliases:
        return module
    ports = tuple(
        Port(aliases.get(port.name, port.name), port.direction) for port in module.ports
    )
    instances = tuple(
        Instance(
            instance.name,
            instance.master,
            tuple(
                (terminal, aliases.get(net, net))
                for terminal, net in instance.connections
            ),
        )
        for instance in module.instances
    )
    return Module(module.name, ports, instances)
