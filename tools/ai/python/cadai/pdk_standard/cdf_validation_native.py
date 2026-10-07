"""Fixed CDF probe adapter over the authenticated current Virtuoso connection."""

import json
import os
import tempfile
import uuid
from pathlib import Path

from ..cdf_results import normalize
from ..circuit_create import skill_literal
from ..skill_result import call_skill
from .cdf_validation_result import literal
from .jsonio import fail, sha


class Native:
    def __init__(self, bridge, workspace):
        self.client = getattr(bridge, "client", None)
        self.workspace = str(Path(workspace).resolve())

    def exchange(self, function, values, token):
        if self.client is None:
            fail("CDF validation requires a connected Virtuoso session", "pdk_source_unavailable")

        def call(name, params):
            ok, result = call_skill(
                self.client,
                name + "(" + " ".join(skill_literal(v) for v in params) + ")",
                native=True,
            )
            if not ok:
                fail(
                    result.get("message", result.get("error", "CDF native call failed")),
                    "pdk_cdf_native_failure",
                )
            return result

        started = False
        try:
            meta = call(function, values)
            started = True
            size = meta.get("bytes")
            if type(size) is not int or not 0 < size <= 2097152:
                fail("Invalid CDF transfer size")
            chunks = []
            offset = 0
            while offset < size:
                data = bytes.fromhex(call("aiCdfRead", ["cdf-transfer:" + token, offset])["hex"])
                if len(data) != min(6000, size - offset):
                    fail("Invalid CDF transfer length")
                chunks.append(data)
                offset += len(data)
            return json.loads(b"".join(chunks).decode("utf-8"))
        finally:
            if started:
                call("aiCdfDrop", ["cdf-transfer:" + token])

    def inspect(self, target, names):
        token = uuid.uuid4().hex
        raw = self.exchange(
            "aiPdkCdfInspect",
            [[target[k] for k in ("library", "cell", "view")], names, token, self.workspace],
            token,
        )
        if not raw.get("ok"):
            fail(raw.get("message", "CDF inspection failed"), "pdk_cdf_inspection_failed")
        return normalize(raw, "inspect_cdf")

    def probe(self, ref, case, order, target, request, inspected=None):
        type_by_name = {
            p["name"]: p.get("cdf_type") for p in (inspected or {}).get("parameters", [])
        }

        def rows(values):
            result = []
            for name in order:
                if name not in values:
                    continue
                cdf_type = type_by_name.get(name)
                typ = {
                    "string": "string",
                    "cyclic": "string",
                    "radio": "string",
                    "int": "integer",
                    "float": "number",
                    "boolean": "boolean",
                }.get(cdf_type)
                if typ is None:
                    fail("Observed CDF type is unsupported: " + name)
                value = (
                    str(values[name])
                    if typ == "string" and not isinstance(values[name], str)
                    else values[name]
                )
                if typ in {"integer", "number"}:
                    number = literal(value)
                    if isinstance(number, (str, bool)):
                        fail("Observed numeric CDF requires a number: " + name)
                    value = int(number) if typ == "integer" else float(number)
                    if typ == "integer" and value != number:
                        fail("Observed integer CDF requires an integral value: " + name)
                result.append([name, typ, value])
            return result

        token = uuid.uuid4().hex
        result = self.exchange(
            "aiPdkCdfCase",
            [
                ref,
                request,
                [target[k] for k in ("library", "cell", "view")],
                rows(case["parameters"]),
                rows(case.get("transition", {})),
                token,
                self.workspace,
            ],
            token,
        )
        return normalize(result, "probe_cdf_parameters")


def evidence(root, name, value):
    raw = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()
    if len(raw) > 4 * 1024 * 1024:
        fail("CDF evidence exceeds 4 MiB")
    path = root / (name + ".json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)  # Exclusive publication; prior evidence is never replaced.
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {"path": str(path), "sha256": sha(raw), "bytes": len(raw)}
