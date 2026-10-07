"""PDK choice policy and the user-facing selection contract."""

from __future__ import annotations

from .pdk_data import binding
from .pdk_normalize import digest
from .pdk_schema import PdkUnavailable

# Same infrastructure libraries hidden by aiEntryLibraries, not process choices.
BASE_LIBRARIES = frozenset(
    {
        "avTech",
        "analogLib",
        "basic",
        "cdsDefTechLib",
        "US_8ths",
        "ahdlLib",
        "bmslib",
        "rfExamples",
        "rfLib",
        "rfTlineLib",
        "sample",
    }
)
DESIGN_TOOLS = frozenset(
    {
        "bind_pdk_device",
        "prepare_template_circuit",
        "prepare_circuit_template_plan",
        "preview_template_placement",
        "prepare_circuit_edit",
        "execute_circuit_edit",
        "inspect_circuit_target",
        "preview_circuit_spec",
        "preview_circuit_geometry",
        "prepare_circuit_creation",
        "create_circuit_from_plan",
        "preview_template_symbol",
        "create_template_symbol",
        "draw_symbol",
        "prepare_circuit_library",
        "create_circuit_library",
        "create_circuit_config",
        "preview_simulation_recipe",
        "create_simulation_setup",
        "run_simulation_setup",
        "execute_circuit_operation",
    }
)
INSTRUCTIONS = (
    "已有设计先检查库的工艺绑定；inspect_schematic/inspect_cdf 不需要全库采集。"
    "电路设计前调用 get_pdk_preparation，已有 target_library 技术绑定优先；"
    "仅工艺未定时使用宿主 selection_question 询问并复用真实答复。"
    "先读取已发布 PDK；有合格数据时不启动全库采集。生成流程使用 prepare_pdk_data/advance_pdk_collection，遵守 next_batch 和取消。"
    "采集 ready 仅表示事实采集完成，绝不表示可以设计。新包按 SICO-PDK-DATA 1.1.0 存入"
    "当前工作区 .sico/ai/pdk-data；原始证据和兼容捕获缓存单独保存。"
    "接着调用 get_pdk_data；默认只列正式发布且指定用途已确认 allow 的器件。"
    "调查采集结果使用 include_unconfirmed=true；section=missing 分页列出生成验收缺项。"
    "正常设计只读消费已发布的电路/仿真数据；绑定缺项不得在设计会话中调用"
    "prepare_pdk_data_update 或 apply_pdk_data_update。缺项应报告 pdk_generation_incomplete，"
    "由独立的 SICO_PDK_WORKFLOW=generation 进程集中完成确认，再调用 publish_pdk_data 验收和发布；"
    "正常设计不启用生成模式。仅检测到源依赖变化或现场结果与数据不符时才进入维护更新。"
    "不得把 editable、模型存在或 interface 分层当成允许使用/写入。推荐步长不替代硬网格。"
    "查询和缺失检查不会启动 probe 或 IV。每个标准文件和响应最多 16 KiB，按修订游标分页；"
    "源内容未变时复用规则，不因换会话重复询问。路径迁移需明确 roots 绑定并核验内容。"
    "旧 search_pdk_devices/get_pdk_device 仅用于会话绑定兼容，不代表设计资格。"
    "bind/prepare/execute 必须经过有效标准规则门槛；未迁移的旧数据不能自动放行。"
    "analogLib/basic 的内置原始数据继续只读，设计使用同样需要工作区标准规则。"
)


def selection_prompt(candidates):
    """Build a bounded requestUserInput-compatible PDK choice question."""
    options = []
    for row in candidates:
        description = (row.get("resolved_path") or "已解析的 Virtuoso 工艺库")[:800]
        if row.get("recommended"):
            description += "；编辑器绑定的 technology（仅推荐）"
        options.append({"label": row["library"], "description": description})
    question = "本次电路任务使用哪个 PDK？"
    if not candidates:
        question = "当前没有可确认的 PDK。请指定本次使用的工艺库及其项目库绑定。"
    elif len(options) > 6 or any(len(o["label"]) > 200 for o in options):
        # Keep every candidate available through the evidence record, with
        # free text when the native input control cannot show the full set.
        question += "请填写候选工艺库的精确名称。"
        options = []
    return {
        "id": "pdk_library",
        "header": "选择 PDK",
        "question": question,
        "options": options,
    }


def require_selection(info, prompt=selection_prompt):
    """Return the stable response used while a PDK choice is pending."""
    return {
        **info,
        "status": "selection_required",
        "selection_required": True,
        "choice_confirmed": False,
        "user_input_required": True,
        "selection_question": prompt(info["candidates"]),
        "next_action": "ask_user_to_select_pdk",
    }


def describe(ctx, catalogs, issues, view, target_library, recommended, data_summary):
    """Describe selection evidence without selecting or retaining session state."""
    libraries = {row["name"]: row for row in ctx["libraries"]}
    bound_library = None
    if target_library:
        target = libraries.get(target_library)
        if target is None:
            raise PdkUnavailable("target_library_missing", "Design library is not resolved")
        bound_library = target.get("technology_library")
        recommended = bound_library
    elif recommended:
        bound_library = recommended
    technologies = {row.get("technology_library") for row in ctx["libraries"]}
    names = ((technologies | set(catalogs)) - BASE_LIBRARIES) - {None, ""}
    candidates = []
    for name in sorted(names):
        lib = libraries.get(name)
        if lib is None:
            continue
        candidates.append(
            {
                "library": name,
                "resolved_path": lib["resolved_path"],
                "recommended": name == recommended,
                "data_source": catalogs.get(name, {}).get("source"),
                "technology_library": lib.get("technology_library"),
                "data_summary": data_summary(catalogs.get(name)),
            }
        )
    candidates.sort(key=lambda row: (not row["recommended"], row["library"]))
    scope = digest(
        [
            ctx["session_ref"],
            ctx["session_generation"],
            ctx["project_ref"],
            view,
            recommended,
            target_library,
            [binding(ctx, row["library"]) for row in candidates],
        ]
    )
    result = {
        "ok": True,
        "schema_version": "cad.pdk.preparation.v1",
        "read_only": True,
        "status": "preparation_required",
        "view": view,
        "selection_ref": scope,
        "candidates": candidates,
        "recommended_library": recommended,
        "target_library": target_library,
        "bound_library": bound_library,
        "selection_required": False,
        "choice_confirmed": False,
        "device_scan_started": False,
        "issues": issues,
        "notices": [row for catalog in catalogs.values() for row in catalog.get("notices", [])],
        "available_libraries": sorted(libraries),
        "next_action": "prepare_pdk_data",
    }
    return result


def choose_library(args, info, selected, choice_validator, base_libraries, builtin_libraries):
    """Return a permitted library and whether a fresh selection prompt is needed."""
    candidates = info["candidates"]
    library = args.get("library") or selected
    if not library and len(candidates) == 1:
        library = candidates[0]["library"]
    support = library in base_libraries
    if (
        support
        and library not in builtin_libraries
        and len(candidates) > 1
        and info["selection_required"]
    ):
        return None, False
    candidate_names = {row["library"] for row in candidates}
    if not support and (not library or library not in candidate_names):
        return None, True
    if (
        not support
        and library != info.get("bound_library")
        and (len(candidates) != 1 or library != candidates[0]["library"])
    ):
        if (not args.get("choice_confirmed") and library != selected) or (
            choice_validator and not choice_validator(library, info)
        ):
            return None, True
    library = library or candidates[0]["library"]
    return library, False
