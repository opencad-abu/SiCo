"""Bounded source capture failure feedback; never alter archived record identities."""


def geometry_issues(capture):
    rows = capture["assets"].get("schematic", {}).get("rows", [])
    instances = {r["name"]: r for r in rows if r["kind"] == "instance"}
    issues = []
    for row in rows:
        if row["kind"] != "instance_geometry" or row["observation"]["ok"]:
            continue
        instance, observation = instances[row["instance"]], row["observation"]
        issues.append(dict(
            code="source_geometry_unavailable", object_ref=row["instance"],
            master=dict(zip(("library", "cell", "view"),
                            (instance[k] for k in ("libName", "cellName", "viewName")))),
            cause_code=observation["code"], message=observation["message"][:2000],
            next_action="repair_source_and_recapture"))
    return sorted(issues, key=lambda row: row["object_ref"])
