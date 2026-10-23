"""Register explicit local resources before a native thread is loaded."""

import time
from pathlib import Path

from .rpc import RpcError


def register(rpc, config, cwd, managed, installed, publish=lambda: None):
    reconcile_plugins(rpc, config, cwd)
    if not config.configured and not managed:
        return
    roots = [row["path"] for row in config.ready("skill_roots")]
    plugins = config.ready("plugins")
    selected = {row["plugin_id"] for row in plugins}
    for key in sorted(managed - selected):
        # Use the public config writer: CLI dotted-key overrides do not
        # reliably address a quoted plugin id. This runs before thread load.
        rpc.request("config/value/write", {
            "keyPath": 'plugins."' + key + '".enabled',
            "value": False, "mergeStrategy": "replace",
        }, timeout=3)
    rpc.request("skills/extraRoots/set", {"extraRoots": roots}, timeout=5)
    inventory = plugin_inventory(rpc, config, cwd) if plugins else []
    deadline = time.monotonic() + 20
    for row in plugins:
        try:
            # Persist ownership before installation, including an uncertain/partial result.
            managed.add(row["plugin_id"])
            publish()
            fingerprint = [row[k] for k in ("path", "bundle", "manifest_sha256",
                                            "marketplace_sha256")]
            if (installed.get(row["plugin_id"]) == fingerprint
                    and any(p.get("id") == row["plugin_id"] and p.get("installed")
                            and p.get("enabled")
                            for p in inventory)):
                row.update(status="registered", message="已注册，等待运行时检查")
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Plugin registration time budget exceeded")
            # No account or global installation: native cache/config belong to this journal.
            result = rpc.request("plugin/install", {
                "marketplacePath": row["path"], "pluginName": row["name"],
            }, timeout=min(10, remaining))
            installed[row["plugin_id"]] = fingerprint
            row.update(status="registered", message="已注册，等待运行时检查")
            if result.get("appsNeedingAuth"):
                row["auth_required"] = True
                row.update(status="dependency_missing", message="插件应用需要账号授权")
        except (ValueError, OSError, RuntimeError):
            row.update(status="unavailable", message="本地插件注册失败，请核对资源和依赖")
            # Installation may have partially succeeded. Do not load an uncertain plugin.
            raise RpcError("本地插件注册未完成：" + row["id"]) from None
        finally:
            publish()


def reconcile_plugins(rpc, config, cwd):
    """Disable project-home plugins that are not selected by this manifest."""
    selected = set() if config is None else {
        row["plugin_id"] for row in config.ready("plugins")}
    native = rpc.request(
        "config/read", {"includeLayers": False, "cwd": cwd}, timeout=5)
    for identity in (native.get("config") or {}).get("plugins") or {}:
        if identity in selected:
            continue
        rpc.request("config/value/write", {
            "keyPath": 'plugins."' + identity + '".enabled',
            "value": False, "mergeStrategy": "replace",
        }, timeout=3)


def marketplace_cwds(config, cwd):
    return [cwd, *dict.fromkeys(str(Path(r["path"]).parents[2]) for r in config.ready("plugins"))]


def plugin_inventory(rpc, config, cwd):
    response = rpc.request("plugin/installed", {"cwds": marketplace_cwds(config, cwd)}, timeout=5)
    return [item for market in response["marketplaces"] for item in market["plugins"]]
