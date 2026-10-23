"""Project resource settings over the authenticated frontend, without task admission."""

from copy import deepcopy

from cadai.optional_resources import DATA_SOURCE_ENV, effective_data_sources

from ..agent_resources import data_sources as agent_data
from ..agent_resources import mode, model, snapshot, store
from ..agent_resources.manifest import read_selection
from ..agent_resources.schema import KINDS, upgrade_manifest
from ..storage import session_snapshot
from ..storage.history import SessionReader
from ..transport.framing import ProtocolError
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress


def scope(hub, address):
    if address is None:
        return None
    value = SessionAddress.from_record(address)
    if (value.project_id, value.service_id) != (hub.descriptor.project_id, hub.descriptor.service_id):
        raise ProtocolError("Settings belong to another project or service")
    if value.session.runtime_id is not None:
        hub.controller(address)
    return value


def observed(read):
    try:
        value = read()
        return {"status": "available", **value, "manifest": upgrade_manifest(value["manifest"])}
    except (ValueError, OSError, RuntimeError):
        return {"status": "unavailable", "message": "配置不可读或格式无效，请核对原文件"}


def selected_defaults(hub, address):
    if address is not None and address.session.runtime_id is not None:
        provider = hub.owner.provider_for(address.session.session_id)
        return read_selection(provider, hub.owner.project)
    return hub.owner.resource_defaults()


def session_view(hub, address):
    if address is None:
        return None
    if address.session.runtime_id is not None:
        try:
            value = hub.workspace.worker.command(address.session, "resource_status").result()
            hub.controller(address.record())
            return {"status": "available", "recorded": False, "snapshot": value}
        except (ValueError, RuntimeError, OSError):
            return {"status": "unavailable", "message": "会话资源无法检查，工程默认仍可管理"}
    reader = SessionReader(hub.workspace.root, address.session.session_id)
    choice, inventory = None, {}
    for event in reader.stream_events():
        if event["kind"] == snapshot.EVENT:
            if choice is not None and event["payload"] != choice:
                return {"status": "unavailable", "message": "历史资源选择快照存在冲突"}
            choice = event["payload"]
        elif event["kind"] == "codex.resources.updated":
            inventory = event["payload"]
    if choice is None:
        return {"status": "unavailable", "message": "此历史会话没有资源选择快照"}
    try:
        choice = snapshot.validate(choice)
    except (ValueError, TypeError, OSError):
        return {"status": "unavailable", "message": "历史资源选择快照无效"}
    value = deepcopy(inventory)
    value["configuration"] = {"state": "frozen", "effective": choice["selection"],
                              "origin": choice["origin"], "drift": []}
    return {"status": "available", "recorded": True, "snapshot": value}


def _session_data_sources(hub, address):
    if address is None:
        return None
    try:
        if address.session.runtime_id is not None:
            environment = hub.owner.dependencies_for(address.session.session_id).environment
        else:
            reader = SessionReader(hub.workspace.root, address.session.session_id)
            saved = session_snapshot.read(reader.directory)
            if saved is None:
                return {"status": "unavailable", "message": "历史会话未记录数据源路径"}
            environment = saved["environment"]
            if not any(name in environment for name in DATA_SOURCE_ENV.values()):
                return {"status": "unavailable", "message": "历史会话未记录数据源路径"}
    except (ValueError, OSError, RuntimeError, KeyError, TypeError):
        return {"status": "unavailable", "message": "会话数据源路径不可读"}
    try:
        return {"status": "available", "values": effective_data_sources(
            environment, hub.owner.project, project={})}
    except (ValueError, OSError, RuntimeError):
        return {"status": "unavailable", "message": "会话数据源路径不可读"}


def data_sources_view(hub, address, *, document=None):
    if document is None:
        try:
            document = agent_data.read(hub.owner.project)
            status, message = "available", ""
        except (ValueError, OSError, RuntimeError) as exc:
            document = {"path": str(agent_data.default_path(hub.owner.project)),
                        "revision": None, "editable": False}
            status, message = "unavailable", str(exc)[:512]
    else:
        status, message = "available", ""
    try:
        environment = hub.owner.resource_settings().environment
        effective = effective_data_sources(
            environment, hub.owner.project, project=document)
    except (ValueError, OSError, RuntimeError):
        effective = {}
    return {"status": status, "message": message, "path": document["path"],
            "revision": document["revision"],
            "editable": document.get("editable", False),
            "project": {kind: document.get(kind) for kind in DATA_SOURCE_ENV},
            "effective": effective, "session": _session_data_sources(hub, address)}


def read(hub, params):
    exact_fields(params, {"address"})
    address = scope(hub, params["address"])
    try:
        session = session_view(hub, address)
    except (ValueError, RuntimeError, OSError, TypeError):
        session = {"status": "unavailable", "message": "会话资源记录不可读，工程默认仍可管理"}
    try:
        workflow = {"status": "available", **mode.read(hub.owner.project)}
    except (ValueError, OSError, RuntimeError):
        workflow = {"status": "unavailable", "message": "智能体模式不可读或格式无效"}
    try:
        model_settings = {"status": "available", **model.read(hub.owner.project)}
    except (ValueError, OSError, RuntimeError):
        model_settings = {"status": "unavailable", "message": "模型设置不可读或格式无效"}
    result = {"project": str(hub.owner.project),
              "supported_kinds": [*KINDS, "mcp_servers", "skill_overrides", "knowledge_sources"],
              "project_defaults": observed(lambda: store.read_project(hub.owner.project)),
              "selected_defaults": observed(lambda: selected_defaults(hub, address)),
              "session": session,
              "pdk_workflow": workflow,
              "model_settings": model_settings,
              "data_sources": data_sources_view(hub, address),
              "defaults_scope": "session" if address and address.session.runtime_id else "service"}
    scope(hub, params["address"])
    return result


def save(hub, params):
    exact_fields(params, {"address", "manifest", "expected_revision"})
    scope(hub, params["address"])
    try:
        value = store.save_project(hub.owner.project, params["manifest"],
                                   expected_revision=params["expected_revision"])
        return {"outcome": "saved", "document": {
            **value, "manifest": upgrade_manifest(value["manifest"])}}
    except store.ResourceConflictError:
        return {"outcome": "conflict", "message": "工程默认已被修改；草稿已保留，请重新读取并核对"}
    except store.ResourceSaveUncertainError:
        return {"outcome": "uncertain", "message": "文件已替换，但持久化未确认；请重新读取核对"}
    except BlockingIOError:
        return {"outcome": "rejected", "message": "另一处正在保存，请稍后重试；草稿已保留"}
    except ValueError as exc:
        return {"outcome": "rejected", "message": str(exc)[:512]}
    except (OSError, RuntimeError):
        return {"outcome": "uncertain", "message": "保存未确认；草稿已保留，请重新读取核对"}


def save_mode(hub, params):
    exact_fields(params, {"address", "pdk_workflow", "expected_revision"})
    scope(hub, params["address"])
    try:
        value = mode.save(
            hub.owner.project,
            params["pdk_workflow"],
            expected_revision=params["expected_revision"],
        )
        return {"outcome": "saved", "document": {"status": "available", **value}}
    except mode.ModeConflictError:
        return {"outcome": "conflict", "message": "智能体模式已被修改；请重新读取后重试"}
    except mode.ModeSaveUncertainError:
        return {"outcome": "uncertain", "message": "智能体模式已替换，但持久化未确认；请重新读取核对"}
    except BlockingIOError:
        return {"outcome": "rejected", "message": "另一处正在保存智能体模式，请稍后重试"}
    except ValueError as exc:
        return {"outcome": "rejected", "message": str(exc)[:512]}
    except (OSError, RuntimeError):
        return {"outcome": "uncertain", "message": "保存未确认；请重新读取核对"}


def save_model(hub, params):
    exact_fields(params, {"address", "backend", "provider", "expected_revision"})
    scope(hub, params["address"])
    try:
        value = model.save(
            hub.owner.project,
            params["backend"],
            params["provider"],
            expected_revision=params["expected_revision"],
        )
        return {"outcome": "saved", "document": {"status": "available", **value}}
    except model.ModelConflictError:
        return {"outcome": "conflict", "message": "模型设置已被修改；请重新读取后重试"}
    except model.ModelSaveUncertainError:
        return {"outcome": "uncertain", "message": "模型设置已替换，但持久化未确认；请重新读取核对"}
    except BlockingIOError:
        return {"outcome": "rejected", "message": "另一处正在保存模型设置，请稍后重试"}
    except ValueError as exc:
        return {"outcome": "rejected", "message": str(exc)[:512]}
    except (OSError, RuntimeError):
        return {"outcome": "uncertain", "message": "保存未确认；请重新读取核对"}


def save_data_sources(hub, params):
    exact_fields(params, {"address", "pdk_data", "circuit_templates", "expected_revision"})
    scope(hub, params["address"])
    try:
        value = agent_data.save(
            hub.owner.project,
            params["pdk_data"],
            params["circuit_templates"],
            expected_revision=params["expected_revision"],
        )
        return {"outcome": "saved", "document": data_sources_view(
            hub, params["address"], document=value)}
    except agent_data.DataSourcesConflictError:
        return {"outcome": "conflict", "message": "数据源设置已被修改；请重新读取后重试"}
    except agent_data.DataSourcesSaveUncertainError:
        return {"outcome": "uncertain", "message": "数据源设置已替换，但持久化未确认；请重新读取核对"}
    except BlockingIOError:
        return {"outcome": "rejected", "message": "另一处正在保存数据源设置，请稍后重试"}
    except ValueError as exc:
        return {"outcome": "rejected", "message": str(exc)[:512]}
    except (OSError, RuntimeError):
        return {"outcome": "uncertain", "message": "保存未确认；请重新读取核对"}
