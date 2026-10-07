"""Retained PDK adapters and lifecycle validation, separate from discovery."""

import json
from copy import deepcopy

from .circuit_geometry_schema import INSTANCE
from .circuit_spec_schema import MASTER, CircuitSpecError, digest, validate
from .pdk_binding_data import (
    binding_ref,
    deck_order,
    geometry_rows,
    parameter_rows,
    read_device,
    require,
    terminal_map,
)
from .pdk_binding_geometry import capture_body, validate_body
from .pdk_binding_schema import BINDING_TOOLS
from .pdk_standard import gate as standard_gate


class PdkBindings:
    def __init__(self, session, client=None, *, body_reader=None, extension_reader=None):
        from .pdk_extensions import discover_extensions

        self.session = session
        self.body_reader = body_reader or (lambda d: capture_body(client, d))
        self.extension_reader = extension_reader or (lambda d: discover_extensions(client, d))
        self.records = {}

    def bind(self, args):
        validate(args, BINDING_TOOLS[0]["inputSchema"])
        device, context = read_device(self.session, args)
        args = deepcopy(args)
        standard = getattr(self.session, "standard", None)
        rule = standard_gate.bind(standard, device, args) if standard and standard.workspace else None
        response_mode = args.pop("response_mode", "full")
        needs_extension = any(c["presence"] == "present" for c in device["callbacks"]["items"])
        needs_extension = needs_extension or any(
            p["policy"] in {"project_extension", "cdf_callback"}
            for p in args["writable_parameters"]
        )
        if needs_extension or args.get("extension_ref"):
            from .pdk_extensions import select_extension

            args["extension_ref"] = select_extension(
                self.extension_reader(device), device["target"], args.get("extension_ref")
            )
        params, callbacks, omitted = parameter_rows(device, args)
        terms, bbox, pins = geometry_rows(device, args)
        crosscheck = device.get("port_crosscheck") or {}
        deck = [name for name in (crosscheck.get("deck") or []) if isinstance(name, str)]
        if rule:
            interface = next(i for i in rule['simulation']['interfaces'].values() if i['simulator'] == 'spectre')
            args['netlist_terminal_map'] = interface['terminal_map']
            deck = interface['term_order']
        mapping, mapping_status = terminal_map(device, args)
        terms = deck_order(terms, mapping, deck)
        body = validate_body(self.body_reader(device), pins)
        if rule:
            standard_gate.geometry(rule, body, pins)
        bbox = body["occupied_bbox"]
        ref = binding_ref(args, context, device, body)
        if rule:
            ref = "pdk-adapter:" + digest([ref, rule["revision"]])
        if ref in self.records:
            record = deepcopy(self.records[ref])
            record["validation"] = self._revalidate([record])
            return self.compact(record) if response_mode == "compact" else self.public(record)
        source = {k: args[k] for k in ("snapshot_ref", "device_ref", "revision")}
        source["adapter_ref"] = ref
        source.update(netlist_terminal_map=mapping, netlist_terminal_map_status=mapping_status,
                      model_deck_terminals=deck)
        master = dict(
            id=args["master_id"],
            target=device["target"],
            library_path=device["library"]["resolved_path"],
            revision=args["revision"],
            kind=args["kind"],
            terminals=terms,
            terminals_complete=True,
            parameters=params,
            parameters_complete=True,
            callbacks=callbacks,
            pdk_binding=source,
        )
        classification = device.get("classification") or {}
        explicit_class = args.get("classification")
        if explicit_class is not None:
            require(explicit_class["kind"] != "unknown", "explicit classification is unknown")
            if classification.get("kind") not in (None, "unknown"):
                require(explicit_class["kind"] == classification["kind"]
                        and explicit_class["attributes"] == (
                            classification.get("attributes") or {}),
                        "project classification differs from collected evidence")
            master["classification"] = deepcopy(explicit_class)
        elif classification.get("kind") not in (None, "unknown"):
            master["classification"] = dict(
                kind=classification["kind"],
                attributes=deepcopy(classification.get("attributes") or {}),
                source_ref=args["device_ref"], revision=args["revision"],
            )
        geometry = dict(
            instance=args["master_id"],
            master_revision=args["revision"],
            parameters_digest=digest({}),
            occupied_bbox=bbox,
            terminals=pins,
        )
        if "annotation_bbox" in body:
            geometry["annotation_bbox"] = deepcopy(body["annotation_bbox"])
        validate(master, MASTER)
        validate(geometry, INSTANCE)
        record = dict(
            ok=True,
            stage="pdk_binding",
            master=master,
            geometry=geometry,
            source_ref=ref,
            evidence_kind="session_capture",
            project_ref=args["project_ref"],
            project_policy_ref=args["project_policy_ref"],
            project_choices=deepcopy(args),
            context=context,
            omitted_cdf_actions=omitted,
            geometry_policy="creator_body_and_pin_centers; rectangular_pin_centers",
            body_observation=body,
            # Collected readiness stays visible to the preparation/preview preflight.
            device_readiness=deepcopy(device.get("readiness")),
            simulation_qualified=False,
        )
        if rule:
            record["standard_rule"] = rule
        record["binding_ref"] = ref
        # Check before retaining; rejected/stale records must not evict usable records.
        record["validation"] = self._revalidate([record])
        require(
            len(json.dumps(self.public(record), ensure_ascii=False, indent=2).encode("utf-8")) <= 90000,
            "adapter response exceeds 90,000 bytes; narrow the supported parameter policy",
        )
        require(
            ref in self.records or len(self.records) < 128,
            "adapter capacity reached; start a fresh session/tool process",
        )
        self.session.retain_binding(args["snapshot_ref"], args["device_ref"])
        self.records[ref] = deepcopy(record)
        return self.compact(record) if response_mode == "compact" else self.public(record)

    def _revalidate(self, records):
        groups = {}
        for record in records:
            source = record["master"]["pdk_binding"]
            group = groups.setdefault(source["snapshot_ref"], {})
            group[source["device_ref"]] = source["revision"]
        checks = []
        for snapshot, revisions in groups.items():
            refs = list(revisions)
            for start in range(0, len(refs), 20):
                wanted = refs[start : start + 20]
                check = self.session.call(
                    "revalidate_pdk_bindings",
                    {
                        "snapshot_ref": snapshot,
                        "device_refs": wanted,
                    },
                )
                require(
                    check["status"] == "valid",
                    "live dependencies " + check["status"] + ": " + str(check["items"]),
                )
                require(
                    {i["device_ref"] for i in check["items"]} == set(wanted),
                    "missing validation rows",
                )
                for item in check["items"]:
                    require(
                        item["status"] == "valid"
                        and item["expected_revision"]
                        == item["observed_revision"]
                        == revisions[item["device_ref"]],
                        "live revision differs from selected revision",
                    )
                checks.append(check)
        for record in records:
            standard_gate.current(getattr(self.session, "standard", None), record)
            master = record["master"]
            device = dict(
                target=master["target"], library=dict(resolved_path=master["library_path"])
            )
            body = validate_body(self.body_reader(device), record["geometry"]["terminals"])
            require(body == record["body_observation"], "creator body changed since binding")
        return checks

    def get(self, ref):
        require(isinstance(ref, str) and ref in self.records,
                "adapter unavailable/expired; bind selected device again")
        return self.records[ref]

    def resolve_bindings(self, bindings):
        """Restore complete masters retained by this backend session."""
        if not isinstance(bindings, dict):
            return bindings
        resolved = deepcopy(bindings)
        masters = []
        for master in bindings.get("masters", []):
            source = master.get("pdk_binding") if isinstance(master, dict) else None
            if source is None:
                require(not any(
                    isinstance(master, dict) and row["master"]["target"] == master.get("target")
                    and row["master"]["revision"] == master.get("revision")
                    for row in self.records.values()
                ), "known PDK adapter provenance was removed; reuse binding_ref")
                masters.append(deepcopy(master))
                continue
            require(isinstance(source, dict), "invalid adapter provenance")
            record = self.get(source.get("adapter_ref"))
            candidate = record["master"]
            require(bindings.get("project_ref") == record["project_ref"], "project binding differs")
            for key in ("id", "target", "revision", "library_path", "kind", "pdk_binding"):
                require(master.get(key) == candidate[key],
                        "master identity/provenance altered: " + key)
            masters.append(deepcopy(candidate))
        if "masters" in resolved:
            resolved["masters"] = masters
        return resolved

    def resolve_request(self, args, *, include_geometry=False):
        """Expand opaque selections into full immutable binding and geometry inputs."""
        from .circuit_geometry_schema import VERSION as GEOMETRY_VERSION
        from .circuit_spec_schema import BINDING_VERSION, digest
        from .pdk_binding_schema import BINDING_REFS

        if "binding_refs" not in args:
            if "bindings" in args:
                return {**args, "bindings": self.resolve_bindings(args["bindings"])}
            return args
        validate(args["binding_refs"], BINDING_REFS, "binding_refs")
        require("bindings" not in args and "geometry" not in args,
                "binding_refs supplies bindings and geometry; do not also reconstruct them")
        refs = sorted(args["binding_refs"])
        require(len(set(refs)) == len(refs), "duplicate binding selection")
        records = [self.get(ref) for ref in refs]
        project = records[0]["project_ref"]
        require(all(r["project_ref"] == project for r in records), "selected devices span projects")
        context_keys = ("session_ref", "session_generation", "project_ref", "virtuoso_version")
        require(all(all(r["context"][k] == records[0]["context"][k] for k in context_keys)
                    for r in records),
                "selected devices span sessions or project contexts; bind again")
        masters = [deepcopy(r["master"]) for r in records]
        require(len({m["id"] for m in masters}) == len(masters), "duplicate selected master id")
        snapshot = "binding-set:" + digest(refs)
        spec = deepcopy(args["spec"])
        for key, value in (("project_ref", project), ("binding_snapshot", snapshot)):
            require(key not in spec or spec[key] == value, "selected " + key + " differs")
            spec[key] = value
        bindings = dict(schema=BINDING_VERSION, project_ref=project, snapshot_ref=snapshot,
                        source_ref=records[0]["context"]["session_ref"],
                        evidence_kind="session_capture", masters=masters)
        resolved = {k: deepcopy(v) for k, v in args.items() if k != "binding_refs"}
        resolved.update(spec=spec, bindings=bindings)
        if include_geometry or "layout" in args:
            by_id = {r["master"]["id"]: r for r in records}
            rows = []
            for inst in spec["instances"]:
                require(inst["master"] in by_id, "instance has no retained selected device")
                rows.append({**deepcopy(by_id[inst["master"]]["geometry"]),
                             "instance": inst["id"],
                             "parameters_digest": digest(inst["parameters"])})
            resolved["geometry"] = dict(
                schema=GEOMETRY_VERSION, project_ref=project, binding_snapshot=snapshot,
                binding_digest=digest(bindings), source_ref=bindings["source_ref"],
                evidence_kind="session_capture", coordinate_system="master_local_schematic_uu",
                instances=rows,
            )
        return resolved

    @staticmethod
    def public(record):
        return {k: v for k, v in record.items() if k != "standard_rule"}

    @staticmethod
    def compact(record):
        master = record["master"]
        return dict(ok=True, stage="pdk_binding", binding_ref=record["binding_ref"],
                    master_id=master["id"], target=master["target"],
                    terminals=master["terminals"],
                    writable_parameters=[p for p in master["parameters"] if p["editable"]],
                    parameter_count=len(master["parameters"]), metadata_retained=True,
                    callback_validation="required_at_prepare" if
                    master["callbacks"]["status"] == "required" else "not_required",
                    simulation_qualified=False, next_action="preview_circuit_spec")

    def validate_prepare(self, args):
        masters = {m["id"]: m for m in args["bindings"]["masters"]}
        geos = {g["instance"]: g for g in args["geometry"]["instances"]}
        records = {}
        for inst in args["spec"]["instances"]:
            master = masters[inst["master"]]
            source = master.get("pdk_binding")
            if source is None:
                require(
                    not any(
                        r["master"]["target"] == master["target"]
                        and r["master"]["revision"] == master["revision"]
                        for r in self.records.values()
                    ),
                    "known PDK adapter provenance was removed",
                )
                continue
            ref = source["adapter_ref"]
            record = self.get(ref)
            require(master == record["master"], "master/provenance altered after binding")
            require(
                args["bindings"]["evidence_kind"] == "session_capture"
                and record["project_ref"] == args["bindings"]["project_ref"],
                "project binding differs",
            )
            expected = {
                **record["geometry"],
                "instance": inst["id"],
                "parameters_digest": digest(inst["parameters"]),
            }
            require(geos[inst["id"]] == expected, "geometry differs from bound PDK selection")
            if record.get("standard_rule"):
                standard_gate.values(record["standard_rule"], inst["parameters"],
                                     purpose="testbench" if args["spec"]["kind"] == "testbench" else "circuit")
            records[ref] = record
        return self._revalidate(list(records.values()))

    def validate_execute(self, refs):
        return self._revalidate([self.get(ref) for ref in refs])


def guards(args):
    """Stable native marker, retained with the preparation and required on execution."""
    used = {i["master"] for i in args["spec"]["instances"]}
    return sorted(
        {
            m["pdk_binding"]["adapter_ref"]
            for m in args["bindings"]["masters"]
            if m["id"] in used and "pdk_binding" in m
        }
    )


def require_service(service):
    if service is None:
        raise CircuitSpecError("PDK adapter service required for selected bindings")
    return service
