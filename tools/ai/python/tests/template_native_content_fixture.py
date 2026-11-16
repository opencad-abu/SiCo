"""Synthetic DD observations; native acceptance separately compares real OA file bytes."""

import copy

from template_qualification_fixture import recount, routed_rows

from cadai.template_native_content import SCHEMA
from cadai.template_schema import digest


def guard(rows):
    header, body = None, []
    for row in rows:
        if row["kind"] == "asset_header":
            header, body = row, []
        elif row["kind"] == "asset_footer":
            instances = [r for r in body if r["kind"] == "instance"]
            views = [
                dict(
                    target=[header[k] for k in ("lib", "cell", "view")],
                    role="source",
                    library_path=header["library_path"],
                    oa_view_type="maskLayout" if header["asset"] == "layout" else "schematic",
                    parameterized=False,
                    instance_count=len(instances),
                    mosaic_count=0,
                    terminals=[
                        dict(name=r["name"], direction=r["direction"], width=r["numBits"])
                        for r in body
                        if r["kind"] == "terminal"
                    ],
                    files=[dict(name="source.oa", sha256=digest(body))],
                )
            ]
            for inst in instances:
                target = [inst[k] for k in ("libName", "cellName", "viewName")]
                if any(v["target"] == target for v in views):
                    continue
                views.append(
                    dict(
                        target=target,
                        role="master",
                        library_path="/pdk",
                        oa_view_type=inst["master_oa_view_type"],
                        parameterized=False,
                        instance_count=0,
                        mosaic_count=0,
                        terminals=[
                            dict(name=n, direction="inputOutput", width=1)
                            for n in inst["terminal_names"]
                        ],
                        files=[dict(name="master.oa", sha256=digest(target))],
                    )
                )
            header["native_content"] = dict(schema=SCHEMA, views=views)
            row["native_content_after"] = copy.deepcopy(header["native_content"])
        else:
            body.append(row)
    return recount(rows)


def layout_rows(*, schematic=True, bindings=True):
    rows = routed_rows()
    header = dict(rows[1], asset="layout", view="layout")
    header.pop("wire_net_semantics")
    header.pop("electrical_observation")
    body = [
        dict(kind="dependency_guard", before="layout", after="layout"),
        dict(
            kind="layout_context",
            conn_ref=["CELLVIEW", "demo", "INV", "schematic", ""],
            source=dict(object_type="cellView", library="demo", cell="INV", view="schematic"),
            binding_status="query_available_unverified" if bindings else "binding_unavailable",
            flatten_policy="skip_missing_instances_no_reconstruction",
        ),
    ]
    for i in range(2):
        body.append(
            dict(
                kind="instance",
                name="P" + str(i),
                libName="pdk",
                cellName="mos",
                viewName="layout",
                xy=[i, 0],
                orient="R0",
                master_available=True,
                schematic_available=False,
                terminal_names=[],
                master_oa_view_type="maskLayout",
                builtin_library=None,
            )
        )
        if bindings:
            obj = dict(
                object_type="inst", name="M" + str(i), library="demo", cell="INV", view="schematic"
            )
            body.append(
                dict(
                    kind="binding",
                    instance="P" + str(i),
                    status="observed",
                    objects=[[obj]],
                    siblings=[],
                    permuted_terms=None,
                )
            )
    prefix = rows[:-1] if schematic else rows[:1]
    prefix[0]["assets"] = ["schematic", "layout"] if schematic else ["layout"]
    return guard(
        prefix
        + [
            header,
            *body,
            dict(kind="asset_footer", complete=True),
            dict(kind="bundle_footer", complete=True, asset_count=len(prefix[0]["assets"])),
        ]
    )
