"""Compare standard saved graphs without requiring a mounted OA or private capture."""

from ..template_graph import match
from ..template_schema import TemplateError
from .codec import VERSION
from .reader import locate
from .topology import graph


def exact(catalog, args):
    if args["omitted_groups"]:
        raise TemplateError("omitted_groups requires match_mode=core")
    graphs, revision = [], None
    for key in ("template_ref", "target_ref"):
        ref = args[key]
        if ref.startswith("ctpl_"):
            item, revision = locate(catalog, ref)
            graphs.append(graph(item["documents"]["topology"]))
        else:
            graphs.append(catalog.get(ref)["topology"])
    if revision is None:
        from .reader import snapshot

        _, revision = snapshot(catalog)
    return dict(
        ok=True,
        schema_version=VERSION,
        revision=revision,
        template_ref=args["template_ref"],
        target_ref=args["target_ref"],
        **match(*graphs, args["port_map"]),
    )
