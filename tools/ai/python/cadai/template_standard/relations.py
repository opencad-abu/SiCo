"""Project the installed geometry relation derivation as flat preferences."""

from ..template_relations import RelationEvidenceUnavailable, relations


def project(record, gap):
    try:
        derived = relations(record)
    except RelationEvidenceUnavailable as exc:
        gap("relation", "items", str(exc), "unsupported_feature", "upgrade_reader")
        return None
    ids = {d["source_name"]: d["id"] for d in record["topology"]["devices"]}
    items = {}
    for table, kind, axis in (("rows", "row", "x"), ("columns", "column", "y")):
        for row in derived[table]:
            items["r" + str(len(items))] = dict(
                kind=kind,
                axis=axis,
                devices=sorted(ids[name] for name in row["members"]),
                basis="source_geometry",
            )
    for row in derived["mirrored_pairs"]:
        items["r" + str(len(items))] = dict(
            kind="mirror",
            axis="y",
            devices=sorted([ids[row["a"]], ids[row["b"]]]),
            basis="source_geometry",
        )
    for row in derived["alignment"]:
        items["r" + str(len(items))] = dict(
            kind="alignment",
            axis="y",
            devices=sorted([ids[row["a"]], ids[row["b"]]]),
            basis="source_geometry",
        )
    for row in derived["gaps"]:
        gap("relation", row.get("device", "items"), row["code"])
    return dict(source="relations", depends_on=["capture"], items=items)
