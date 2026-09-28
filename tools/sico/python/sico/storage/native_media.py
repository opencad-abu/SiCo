"""Decode only inline raster evidence; paths and remote URLs remain references."""

import base64
import binascii
from urllib.parse import urlsplit


def web_url(value):
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        if (url.scheme in {"http", "https"} and url.hostname
                and not url.username and not url.password):
            return value
    except ValueError:
        pass
    return None


def raster_data(value):
    if not isinstance(value, str) or not value or len(value) > 12 * 1024 * 1024:
        return None
    encoded = value
    if value.startswith("data:"):
        header, separator, encoded = value.partition(",")
        if not separator or header not in {"data:image/png;base64", "data:image/jpeg;base64",
                                            "data:image/webp;base64", "data:image/gif;base64"}:
            return None
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        return None
    if not (data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")
            or data.startswith((b"GIF87a", b"GIF89a"))
            or data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        return None
    return data
