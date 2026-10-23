"""Map stable skill definitions to native inventory paths and apply session defaults."""

from pathlib import Path


def inventory(rpc, cwd, definitions):
    groups = rpc.request("skills/list", {"cwds": [cwd], "forceReload": True}, timeout=5)["data"]
    rows = []
    for group in groups:
        if group.get("errors"):
            raise ValueError("部分技能无法解析，请核对 SKILL.md")
        for skill in group["skills"]:
            row = {k: skill.get(k) for k in ("name", "path", "enabled", "scope", "pluginId")}
            row["description"] = str(skill.get("description") or "")[:2048]
            row["dependencies"] = (skill.get("dependencies") or {}).get("tools", [])[:64]
            decorate([row], definitions)
            rows.append(row)
    if len(rows) > 512:
        raise ValueError("技能目录超过 512 项显示上限")
    return rows


def apply(rpc, config, cwd, definitions):
    overrides = config.document["manifest"]["skill_overrides"]
    if not overrides:
        return
    rows = inventory(rpc, cwd, definitions)
    for row in overrides:
        selected = str(Path(row["path"]).resolve())
        matches = [s for s in rows if s.get("definition_path") and
                   str(Path(s["definition_path"]).resolve()) == selected]
        if len(matches) != 1:
            raise ValueError("单技能设置不在本会话发现目录中或路径不唯一")
        result = rpc.request("skills/config/write", {"path": matches[0]["path"],
                             "enabled": row["enabled"]}, timeout=3)
        if result.get("effectiveEnabled") != row["enabled"]:
            raise ValueError("原生技能启停未生效")


def decorate(rows, definitions):
    for row in rows:
        matches = [s for s in definitions.get(row.get("pluginId"), [])
                   if s["name"] == row["name"] and s.get("path")]
        row["definition_path"] = (matches[0]["path"] if len(matches) == 1 else
                                  None if row.get("pluginId") or row.get("scope") == "system"
                                  else row["path"])
    return rows


def dependencies(rows, servers, environment_names):
    result = []
    for row in rows:
        for dep in row.get("dependencies", []):
            name, kind = dep.get("value"), dep.get("type")
            match = next((s for s in servers if kind == "mcp" and s["name"] == name), None)
            status = "unverified"
            if kind == "env_var":
                status = "available" if name in environment_names else "missing"
            elif kind == "mcp":
                status = ("missing" if match is None else "available" if
                          match.get("runtimeStatus") == "connected" and not match.get("toolsError")
                          else "unavailable")
            result.append({"skill": row["name"], "path": row["path"], "name": name,
                           "type": kind, "status": status})
    return result


def preview(rows, selection):
    import os
    import stat

    if not isinstance(selection, dict) or set(selection) != {"name", "path"}:
        raise ValueError("技能预览选择无效")
    matches = [r for r in rows if r["name"] == selection["name"] and
               r.get("definition_path") == selection["path"] and r.get("definition_path")]
    if len(matches) != 1:
        raise ValueError("技能不在本次发现目录中或路径不唯一")
    # Read the discovered definition only, never an arbitrary client-supplied path.
    fd = os.open(matches[0]["path"], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 65536:
            raise ValueError("技能正文超过 64 KiB 预览上限或文件类型不支持")
        raw = stream.read(65537)
        after = os.fstat(stream.fileno())
    if (len(raw) != before.st_size or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns):
        raise ValueError("技能文件在预览期间变化，请重新检查")
    return raw.decode("utf-8")
