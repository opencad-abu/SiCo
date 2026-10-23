"""Validate live capture identity and publish one immutable private template."""

from pathlib import Path

from .template_capture import parse_capture, read_bytes
from .template_schema import TemplateError, arguments


def import_capture(catalog, artifact, spool, expected=None):
    if expected is not None:
        expected = arguments("extract_circuit_templates", expected)
    catalog.ensure_private_destination()
    path = Path(artifact["path"])
    if path.resolve().parent != Path(spool).resolve() or path.is_symlink():
        raise TemplateError("capture artifact is outside the session spool")
    raw = read_bytes(path)
    capture = parse_capture(raw)
    if capture["sha256"] != artifact["sha256"] or len(raw) != artifact["size"]:
        raise TemplateError("capture artifact digest/size mismatch")
    if expected:
        from .template_classification import SEMANTICS

        for name, asset in capture["assets"].items():
            header = asset["header"]
            if header.get("classification_semantics") != SEMANTICS:
                raise TemplateError(
                    "live capture requires current classification semantics; reload collector")
            if (header["lib"], header["cell"], header["view"]) != (
                expected["library"],
                expected["cell"],
                expected[name + "_view"],
            ):
                raise TemplateError("capture source does not match requested cell/view")
        reported = list(capture["assets"]) + [r["asset"] for r in capture["missing"]]
        if sorted(reported) != sorted(expected["assets"]):
            raise TemplateError("capture assets do not match requested assets")
    return store_capture(catalog, capture, raw, expected or {})


def store_capture(catalog, capture, raw, options, *, category="reference", provenance=None):
    """Publish new captures only through the standard JSON authority."""
    from .template_standard.collect import collect

    return collect(catalog, capture, raw, options, category=category, provenance=provenance)
