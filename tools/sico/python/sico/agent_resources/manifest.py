"""Select and read bounded resource documents for settings and runtime consumers."""

import hashlib
import os
from pathlib import Path
import stat

from cadai.env_names import value as env_value

from ..storage.roots import state_root
from ..transport.framing import strict_json
from .schema import MAX_MANIFEST, normalize_manifest, path_value


def default_path(project):
    return state_root(project) / "ai/agent-resources.json"


def read_object(path):
    # NONBLOCK avoids hanging the settings worker on a named pipe.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("外部资源清单必须是普通文件")
        raw = stream.read(MAX_MANIFEST + 1)
    if len(raw) > MAX_MANIFEST:
        raise ValueError("外部资源清单超过 64 KiB")
    value = strict_json(raw)
    if not isinstance(value, dict):
        raise ValueError("外部资源清单必须是 JSON 对象")
    return value, hashlib.sha256(raw).hexdigest()


def read_manifest(path, *, required=False):
    try:
        value, revision = read_object(path)
    except FileNotFoundError:
        if required or Path(path).is_symlink():
            raise ValueError("外部资源清单不可读或格式无效：" + str(path)) from None
        value, revision = {"version": 1}, None
    except (ValueError, OSError, RuntimeError):
        raise ValueError("外部资源清单不可读或格式无效：" + str(path)) from None
    try:
        manifest = normalize_manifest(value)
    except (ValueError, OSError, RuntimeError):
        raise ValueError("外部资源清单不可读或格式无效：" + str(path)) from None
    return {"path": str(path), "manifest": manifest, "revision": revision}


def read_selection(settings, project):
    options = getattr(settings, "options", {})
    environment = getattr(settings, "environment", {})
    explicit = options.get("external_resources")
    source = "provider"
    if not explicit:
        explicit = env_value(environment, "EXTERNAL_RESOURCES")
        source = "environment"
    path = path_value(explicit) if explicit else default_path(project)
    return {**read_manifest(path, required=bool(explicit)),
            "source": source if explicit else "project",
            "editable": not explicit and Path(path).parents[1].name == ".sico"}
