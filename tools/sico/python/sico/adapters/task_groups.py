"""SiCo host task-group declarations.

Groups whose members include host-owned tools are declared here, next to the
tools that own them, and injected into the same assembly entry as the
cadai-owned groups (``sico.adapters.assistant_tools``); cadai never imports
back into this tree.  Status stays ``draft`` until the group passes its real
acceptance in the task-group design.
"""

from __future__ import annotations

from cadai.task_group_schema import merge
from cadai.task_groups import GROUPS as CADAI_GROUPS

HOST_GROUPS = [
    {
        "id": "virtuoso_ui",
        "owner": "sico",
        "status": "reviewed",
        "purpose": "打开 Library Manager、查询或操作 Virtuoso 窗口",
        "keywords": [
            "打开library manager", "library manager", "库管理器", "打开窗口",
            "窗口信息", "window",
        ],
        "tools": [
            "get_project_context", "search_skill_api", "get_skill_api",
            "check_skill", "eval_skill", "list_windows", "inspect_window",
        ],
        "subgroups": [],
        "conditions": [],
        "recovery": [],
        "connections": [
            "参考信息足够时直接执行；缺手册证据再查 cds_reference。",
            "verification profile 不因引用 eval_skill 而获得 eval 执行权。",
        ],
    },
    {
        "id": "results_analyze",
        "owner": "sico",
        "status": "reviewed",
        "purpose": "读取、查询和评价已有结果",
        "keywords": ["看结果", "读结果", "测量指标", "results", "dc 结果", "结果"],
        "tools": [
            "bind_result_context", "read_maestro_results",
            "query_maestro_results", "evaluate_measurement_specs",
        ],
        "subgroups": [],
        "conditions": [
            {
                "when": "需要波形或 AC 指标",
                "tools": [
                    "read_maestro_waveform", "measure_waveform", "measure_ac_response",
                ],
            },
        ],
        "recovery": [],
        "connections": [
            "已有结果只读；不因历史涉及仿真就暴露整个创建链或启动仿真。",
        ],
    },
    {
        "id": "engineering_report",
        "owner": "sico",
        "status": "draft",
        "purpose": "记录工程阶段、证据和结论",
        "keywords": ["记录阶段", "工程报告", "审阅结论", "report", "阶段"],
        "tools": [
            "set_copilot_stage", "plan_copilot_stages", "update_copilot_stage",
            "list_copilot_data",
            "prepare_copilot_audit", "publish_copilot_report",
        ],
        "subgroups": [],
        "conditions": [],
        "recovery": [],
        "connections": [
            "普通开窗口任务不强制引用；工程审阅仍按现有流程。",
            "工程阶段/报告工具在 workbench、desktop_compat，以及 circuit/simulation "
            "窄档提供；其他窄档仍按目录预算边界，调用前先确认当前档位。",
        ],
    },
]

# Merged declarations for this host: one validated map, computed once.
TASK_GROUPS = merge(CADAI_GROUPS, HOST_GROUPS)

__all__ = ["HOST_GROUPS", "TASK_GROUPS"]
