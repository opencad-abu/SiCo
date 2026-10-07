"""Bounded typed core embedding; enumerate ambiguity instead of choosing symmetry."""

import copy
import math
import time

from .template_embedding import verify_embedding
from .template_omit_selection import omit_selected
from .template_reuse_schema import validate_record
from .template_rules import rule_ref
from .template_schema import TemplateError
from .template_topology import evidence_gaps, topology_parts

VERSION = "20260922.core-embedding.v2"


def match_core(record, target, *, port_map=None, device_map=None, terminal_map=None,
               net_map=None, omitted_groups=(), timeout=0.5, max_states=50000,
               clock=time.monotonic):
    """Target is a typed saved graph. A unique result still requires explicit binding."""
    start = clock()
    deadline = start + timeout
    states, found, adaptation_required = 0, [], False
    adaptation_reasons = set()

    def result(status, reason=None, complete=True):
        return {"status": status, "reason": reason, "complete": complete, "states": states,
                "mappings": copy.deepcopy(found), "adaptation_required": adaptation_required,
                "version": VERSION,
                "elapsed_ms": (clock() - start) * 1000, "creation_qualified": False}

    try:
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise TemplateError("invalid core matching budget")
        if type(max_states) is not int or not 1 <= max_states <= 200000:
            raise TemplateError("invalid core matching budget")
        validate_record(record)
        from .template_source_qualification import require_qualified

        require_qualified(record)
        source, _ = omit_selected(record, omitted_groups)
        sd, sn, sp, _ = topology_parts(source)
        td, tn, tp, target_pins = topology_parts(target)
        gaps = sorted(set(evidence_gaps(source) + evidence_gaps(target)))
        if gaps:
            return result("inconclusive", "incomplete_electrical_evidence:" + ",".join(gaps), False)
        if any(sum(len(part) for part in topology_parts(t)) > 512 for t in (source, target)):
            return result("inconclusive", "graph_exceeds_512_nodes", False)
        allowed = record["reuse_contract"]["allowed_rule_refs"]
        if omitted_groups and rule_ref("omit_optional_group") not in allowed:
            return result("needs_adaptation", "omission_rule_not_allowed")
        explicit_ports = {} if port_map is None else port_map
        if set(explicit_ports) - sp.keys() or set(explicit_ports.values()) - tp.keys():
            raise TemplateError("port_map refers to absent ports")
        explicit_devices = {} if device_map is None else device_map
        explicit_nets = {} if net_map is None else net_map
        explicit_terminals = {} if terminal_map is None else terminal_map
        if (set(explicit_devices) - sd.keys() or set(explicit_devices.values()) - td.keys()
                or set(explicit_nets) - sn.keys() or set(explicit_nets.values()) - tn.keys()
                or set(explicit_terminals) - sd.keys()):
            raise TemplateError("explicit core mapping refers to absent objects")
        if any(set(values) - {p["name"] for p in sd[key]["pins"]}
               for key, values in explicit_terminals.items()):
            raise TemplateError("terminal_map refers to absent source terminals")
        domains = {d: sorted(t for t, v in td.items() if sd[d]["kind"] == v["kind"]
                            and sd[d]["role"] == v["role"]
                            and all(
                                v["attributes"].get(k) == x
                                for k, x in sd[d]["attributes"].items()
                            )
                            and {p["name"] for p in sd[d]["pins"]}
                            == {p["name"] for p in v["pins"]})
                   for d in sd}
        order = sorted(sd, key=lambda d: (len(domains[d]), d))
        terminals = {d: {p["name"]: p["name"] for p in sd[d]["pins"]} for d in sd}

        def tick():
            nonlocal states
            states += 1
            if states > max_states or clock() >= deadline:
                raise TimeoutError

        def record_match(dm, nm, pm):
            nonlocal adaptation_required
            mapping = {"device_map": dict(dm), "terminal_map": terminals,
                       "net_map": dict(nm), "port_map": dict(pm),
                       "omitted_groups": list(omitted_groups)}
            try:
                checked = verify_embedding(record, source, target, mapping)
            except TemplateError:
                return
            if any(dm.get(key) != value for key, value in explicit_devices.items()):
                return
            if any(nm.get(key) != value for key, value in explicit_nets.items()):
                return
            if any(
                any(terminals[key].get(name) != value for name, value in values.items())
                for key, values in explicit_terminals.items()
            ):
                return
            if checked["residual"] and omitted_groups:
                adaptation_reasons.add("multiple_topology_adaptations")
                return
            renamed_nets = any(
                sn[source_net]["source_name"] != tn[target_net]["source_name"]
                for source_net, target_net in nm.items()
                if not sn[source_net]["is_global"]
            )
            renamed_ports = any(name != candidate for name, candidate in pm.items())
            if (renamed_nets or renamed_ports) and rule_ref("rename") not in allowed:
                adaptation_reasons.add("rename_rule_not_allowed")
                return
            if checked["residual"] and rule_ref("add_boundary_group") not in record[
                "reuse_contract"
            ]["allowed_rule_refs"]:
                adaptation_reasons.add("boundary_rule_not_allowed")
                return
            adaptation_required = adaptation_required or bool(
                checked["residual"] or omitted_groups or renamed_nets or renamed_ports
            )
            if mapping not in found:
                found.append(mapping)
            if len(found) == 2:
                raise StopIteration

        def map_ports(dm, nm, pm, remaining):
            if not remaining:
                map_free_nets(dm, nm, pm)
                return
            name = remaining[0]
            source_port = sp[name]
            # Unspecified names are candidates, never evidence of polarity. Two
            # valid renamings produce needs_mapping, even when the first is obvious.
            choices = [explicit_ports[name]] if name in explicit_ports else sorted(tp)
            for candidate in choices:
                tick()
                actual = tp[candidate]
                if candidate in pm.values() or (actual["direction"], actual["num_bits"]) != (
                    source_port["direction"], source_port["num_bits"]
                ):
                    continue
                extended = extend_net(nm, source_port["net"], actual["net"])
                if extended is not None:
                    map_ports(dm, extended, {**pm, name: candidate}, remaining[1:])

        def extend_net(nm, old, new):
            if old in nm:
                return nm if nm[old] == new else None
            if new in nm.values() or new not in tn:
                return None
            before, after = sn[old], tn[new]
            if (before["is_global"], before["num_bits"]) != (after["is_global"], after["num_bits"]):
                return None
            if before["is_global"] and before["source_name"] != after["source_name"]:
                return None
            return {**nm, old: new}

        def map_free_nets(dm, nm, pm):
            missing = [name for name in sorted(sn) if name not in nm]
            if not missing:
                record_match(dm, nm, pm)
                return
            name = missing[0]
            source_net = sn[name]
            for candidate, target_net in sorted(tn.items()):
                tick()
                if candidate in nm.values():
                    continue
                if (source_net["is_global"], source_net["num_bits"]) != (
                    target_net["is_global"], target_net["num_bits"]
                ):
                    continue
                if source_net["is_global"] and source_net["source_name"] != target_net[
                    "source_name"
                ]:
                    continue
                map_free_nets(dm, {**nm, name: candidate}, pm)

        def walk(index, dm, nm):
            if index == len(order):
                map_ports(dm, nm, {}, sorted(sp))
                return
            source_id = order[index]
            for target_id in domains[source_id]:
                tick()
                if target_id in dm.values():
                    continue
                extended = nm
                for pin in sd[source_id]["pins"]:
                    extended = extend_net(
                        extended, pin["net"], target_pins[(target_id, pin["name"])]
                    )
                    if extended is None:
                        break
                if extended is not None:
                    walk(index + 1, {**dm, source_id: target_id}, extended)

        walk(0, {}, {})
    except TimeoutError:
        return result("inconclusive", "search_budget_exceeded", False)
    except StopIteration as exc:
        reason = str(exc)
        if reason:
            return result("needs_adaptation", reason, False)
        # Two valid embeddings are enough to prove ambiguity.  This is a
        # completed decision, unlike a timeout or state-budget interruption.
        return result("needs_mapping", "ambiguous_mapping", True)
    except TemplateError as exc:
        return result("invalid_request", str(exc), False)
    if clock() >= deadline:
        return result("inconclusive", "search_budget_exceeded", False)
    if adaptation_required:
        return result("needs_adaptation", "adaptation_required")
    if found:
        return result("matched")
    if adaptation_reasons:
        return result("needs_adaptation", sorted(adaptation_reasons)[0])
    return result("different")
