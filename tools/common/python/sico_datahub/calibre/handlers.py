"""DataHub envelope handlers for the explicit Calibre runner."""

from __future__ import annotations

from pathlib import Path

from ..codec import canonical_json
from ..envelope import Envelope, Meta, Page
from ..errors import HubError
from ..limits import MAX_PAYLOAD_BYTES
from .runner import CalibreRunner
from .runset import catalog_drc_runset

DRC_CONTRACT = "sico.calibre.drc.v1"
LVS_CONTRACT = "sico.calibre.lvs.v1"
_SCHEMAS = {
    "probe": ("calibre.drc.probe.v1", "calibre.lvs.probe.v1"),
    "catalog": ("calibre.drc.catalog.v1", None),
    "check": ("calibre.drc.check.v1", "calibre.lvs.check.v1"),
    "submit": ("calibre.drc.submit.v1", "calibre.lvs.submit.v1"),
    "status": ("calibre.drc.status.v1", "calibre.lvs.status.v1"),
    "result": ("calibre.drc.result.v1", "calibre.lvs.result.v1"),
    "export": (None, "calibre.lvs.export.v1"),
}
_MARGIN = 512


def _meta():
    return Meta(source="bridge", target="calibre")


def _response(request, payload, *, page=None):
    return Envelope(
        contract=request.contract,
        version=request.version,
        action=request.action,
        id=request.id,
        payload=payload,
        page=page,
        meta=_meta(),
    )


def _schema(kind, action):
    schema = _SCHEMAS[action][0 if kind == "drc" else 1]
    if schema is None:
        raise HubError("capability_not_available", "Calibre action is unavailable")
    return schema


def _size(value):
    return len(canonical_json(value).encode("utf-8"))


def _fits(value):
    return _size(value) <= MAX_PAYLOAD_BYTES - _MARGIN


def _compact_item(item):
    if not isinstance(item, dict):
        return item
    compact = dict(item)
    if isinstance(compact.get("message"), str):
        compact["message"] = compact["message"][:512]
    if "details" in compact:
        compact["details"] = {}
    return compact


def _paged(render, items, offset, limit):
    total = len(items)
    start = min(offset, total)
    end = min(start + limit, total)
    payload = render(items[start:end])
    if not _fits(payload):
        while end - start > 1:
            end = start + max(1, (end - start) // 2)
            payload = render(items[start:end])
            if _fits(payload):
                break
        else:
            if end > start:
                payload = render([_compact_item(items[start])])
            if not _fits(payload):
                raise HubError("result_too_large", "Calibre page exceeds its limit")
    truncated = end < total
    page = Page(
        offset=offset,
        limit=limit,
        total=total,
        next=end if truncated else None,
        truncated=truncated,
    )
    return payload, page


def _bounded_counts(counts):
    if not isinstance(counts, dict):
        return {}, False
    values = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return dict(values[:64]), len(values) > 64


def _job_payload(receipt, kind, action):
    payload = receipt.to_dict()
    payload["schema"] = _schema(kind, action)
    return payload


def _result_payload(receipt, result, kind, offset, limit):
    values = dict(result)
    key = "violations" if kind == "drc" else "findings"
    counts_key = "checks" if kind == "drc" else "counts"
    counts, truncated = _bounded_counts(values.get(counts_key))
    values[counts_key] = counts
    values[counts_key + "_truncated"] = truncated

    def render(items):
        payload = receipt.to_dict()
        payload["schema"] = _schema(kind, "result")
        values[key] = items
        payload["result"] = values
        return payload

    return _paged(render, list(values.get(key) or ()), offset, limit)


def _probe(kind, runner):
    schema = _schema(kind, "probe")

    def handle(request):
        if request.payload["tool_root"] != str(runner.calibre_root):
            raise HubError("invalid_arguments", "Tool root does not match this bridge")
        return _response(request, {
            "schema": schema,
            "tool_root": str(runner.calibre_root),
            "executable": str(runner.calibre_executable),
            "available": True,
            "read_only": True,
            "tool_version": runner.tool_version,
        })

    return handle


def _submit(kind, runner):
    def handle(request):
        receipt = runner.submit(
            kind,
            request.payload["run_ref"],
            request.payload["config_path"],
            request.payload["work_root"],
        )
        return _response(request, _job_payload(receipt, kind, "submit"))

    return handle


def _status(kind, runner):
    def handle(request):
        receipt = runner.status(request.payload["run_ref"])
        return _response(request, _job_payload(receipt, kind, "status"))

    return handle


def _result(kind, runner):
    def handle(request):
        receipt, result = runner.result(request.payload["run_ref"])
        payload, page = _result_payload(
            receipt,
            result,
            kind,
            request.payload.get("offset", 0),
            request.payload.get("limit", 50),
        )
        return _response(request, payload, page=page)

    return handle


def _check(kind, runner):
    def handle(request):
        receipt = runner.submit_candidate(
            request.payload["run_ref"],
            request.payload["config_path"],
            request.payload["work_root"],
            request.payload["candidate_path"],
            request.payload.get("bbox"),
            kind=kind,
            source_path=(
                request.payload.get("source_path") if kind == "lvs" else None
            ),
        )
        return _response(request, _job_payload(receipt, kind, "check"))

    return handle


def _export(runner):
    def handle(request):
        payload = runner.export_layout(
            request.payload["export_ref"],
            request.payload["config_path"],
            request.payload["work_root"],
        )
        payload["schema"] = "calibre.lvs.export.v1"
        return _response(request, payload)

    return handle


def catalog_handler(runner):
    def handle(request):
        runset = _runset_path(request.payload["runset_path"])
        catalog = catalog_drc_runset(
            runset,
            calibre=str(runner.calibre_executable),
            timeout=60.0,
            cache_dir=str(runner.store.cache),
            expand_tvf=True,
            tool_version=runner.tool_version,
        )
        groups = list(catalog.groups)
        layers = list(catalog.layers[:32])

        def render(items):
            counts = {
                name: catalog.counts.get(name, 0)
                for name in items
            }
            members = {
                name: list(catalog.members.get(name, ()))[:32]
                for name in items
                if name in catalog.members
            }
            return {
                "schema": _schema("drc", "catalog"),
                "runset": catalog.runset.to_dict(),
                "dependencies_sha256": catalog.dependencies_sha256,
                "source": catalog.source,
                "error": catalog.error,
                "tool_version": catalog.tool_version,
                "layers": layers,
                "layers_truncated": len(catalog.layers) > len(layers),
                "counts": counts,
                "members": members,
                "members_truncated": any(
                    len(catalog.members.get(name, ())) > 32 for name in items
                ),
            }

        payload, page = _paged(
            render,
            groups,
            request.payload.get("offset", 0),
            request.payload.get("limit", 50),
        )
        return _response(request, payload, page=page)

    return handle


def calibre_handlers(runner: CalibreRunner):
    """Return all Calibre bridge routes backed by one runner instance."""
    handlers = {}
    for kind, contract in (("drc", DRC_CONTRACT), ("lvs", LVS_CONTRACT)):
        handlers[(contract, "probe")] = _probe(kind, runner)
        handlers[(contract, "submit")] = _submit(kind, runner)
        handlers[(contract, "status")] = _status(kind, runner)
        handlers[(contract, "result")] = _result(kind, runner)
    handlers[(DRC_CONTRACT, "catalog")] = catalog_handler(runner)
    handlers[(DRC_CONTRACT, "check")] = _check("drc", runner)
    handlers[(LVS_CONTRACT, "check")] = _check("lvs", runner)
    handlers[(LVS_CONTRACT, "export")] = _export(runner)
    return handlers


def _runset_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file():
        raise HubError("invalid_arguments", "DRC runset must name an absolute file")
    return path


__all__ = [
    "DRC_CONTRACT",
    "LVS_CONTRACT",
    "calibre_handlers",
]
