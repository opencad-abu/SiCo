"""Explicit bounded wire representation for verified historical input assets."""

import base64

from ..storage.input_assets import MAX_ASSET
from ..transport.framing import ProtocolError
from .frontend_codec import PAYLOAD_BYTES, digest
from .published import freeze
from .service_protocol import exact_fields
from .service_values import integer

ASSET_RESULT_BYTES = PAYLOAD_BYTES + (MAX_ASSET + 2) // 3 * 4


def result_budget(operation):
    return ASSET_RESULT_BYTES if operation in ("input_detail", "detail") else PAYLOAD_BYTES


def input_record(value):
    if "data" not in value:
        return value
    return dict(value, data=asset_record(value["data"]))


def asset_record(data):
    if type(data) is not bytes or not 0 < len(data) <= MAX_ASSET:
        raise ProtocolError("Invalid verified input asset")
    return dict(size=len(data), sha256=digest(data), base64=base64.b64encode(data).decode("ascii"))


def input_value(value):
    if "data" not in value:
        return value
    return freeze(dict(value, data=asset_value(value["data"])))


def asset_value(row):
    exact_fields(row, {"size", "sha256", "base64"})
    integer(row["size"], 1)
    if (row["size"] > MAX_ASSET or not isinstance(row["base64"], str)
            or len(row["base64"]) != (row["size"] + 2) // 3 * 4):
        raise ProtocolError("Invalid input asset size")
    try:
        data = base64.b64decode(row["base64"], validate=True)
    except ValueError as exc:
        raise ProtocolError("Invalid input asset encoding") from exc
    if len(data) != row["size"] or digest(data) != row["sha256"]:
        raise ProtocolError("Input asset digest mismatch")
    return data


def detail_record(value):
    images = value.get("images", ())
    if len(images) > 1:
        raise ProtocolError("Detail image budget exceeded")
    return dict(value, images=[asset_record(data) for data in images])


def detail_value(value):
    images = value.get("images", ())
    if not isinstance(images, (tuple, list)) or len(images) > 1:
        raise ProtocolError("Invalid detail image inventory")
    return freeze(dict(value, images=[asset_value(row) for row in images]))
