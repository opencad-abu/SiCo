"""Cadence-domain task-group declarations (first batch).

Groups that reference SiCo host tools are declared by the SiCo adapter and
injected into the same assembly entry; this module never imports it.  Groups
stay ``draft`` until their real-task acceptance passes (see the task-group
design); draft only marks acceptance status, never a restriction on use.
"""

from __future__ import annotations

GROUPS = [
    {
        "id": "cds_reference",
        "owner": "cadai",
        "status": "reviewed",
        "purpose": "查找和读取站点安装的 Cadence 手册",
        "keywords": ["Cadence 手册", "manual", "文档", "SKILL 手册", "help", "手册"],
        "tools": ["cds_help"],
        "subgroups": [],
        "conditions": [],
        "recovery": [],
        "connections": [
            "唯一广告名为 cds_help；tool_help 为不广告的兼容调用名。",
            "站点文档缺失时明确返回，不让模型反复 probe。",
        ],
    },
    {
        "id": "schematic_read",
        "owner": "cadai",
        "status": "reviewed",
        "purpose": "读取原理图、查实例和器件参数",
        "keywords": ["读取原理图", "查看实例", "器件参数", "schematic", "读网表", "原理图"],
        "tools": [
            "inspect_schematic", "snapshot_schematic", "query_schematic",
            "get_schematic_item", "inspect_cdf",
        ],
        "subgroups": [],
        "conditions": [],
        "recovery": [],
        "connections": [
            "按需要读取；检查现有器件不自动启动 PDK 维护。",
            "原理图过大时先 snapshot_schematic，再用 query/get 读取片段。",
        ],
    },
    {
        "id": "pdk_consume",
        "owner": "cadai",
        "status": "reviewed",
        "purpose": "选取并绑定已发布 PDK 器件",
        "keywords": ["选择器件", "PDK 器件", "绑定器件", "device", "pdk device", "器件"],
        "tools": [
            "init_pdk", "get_pdk_preparation", "search_pdk_devices", "get_pdk_device",
            "get_pdk_data", "bind_pdk_device", "revalidate_pdk_bindings",
        ],
        "subgroups": [],
        "conditions": [],
        "recovery": [],
        "connections": [
            "遵循已发布包的使用资格与参数规则，不做 PDK 维护。",
            "绑定前用 get_pdk_data section=cdf 读取该器件的已确认可写参数（write）"
            "与执行顺序（execution.order）；bind_pdk_device 按已发布标准规则精确校验这两项。",
            "binding_ref 由 bind_pdk_device 产生，供创建/仿真输入引用。",
        ],
    },
    {
        "id": "symbol_prepare",
        "owner": "cadai",
        "status": "reviewed",
        "purpose": "检查或创建 DUT symbol",
        "keywords": ["symbol", "符号", "管脚", "DUT symbol", "创建符号"],
        "tools": [
            "inspect_symbol_ports", "preview_template_symbol",
            "get_symbol_binding", "inspect_template_symbol",
            "preflight_circuit_project",
        ],
        "subgroups": [],
        "conditions": [
            {
                "when": "DUT 无 symbol 且接口已确认需要创建",
                "tools": [
                    {"tool": "execute_circuit_operation", "operation": "create_template_symbol"},
                    {"tool": "execute_circuit_operation", "operation": "draw_symbol"},
                ],
            },
        ],
        "recovery": [],
        "connections": [
            "先查已有 symbol 并复用，不覆盖；接口不一致时停止 TB 创建。",
            "创建走受控入口，preview_ref/digest 必须保留。",
        ],
    },
    {
        "id": "circuit_create",
        "owner": "cadai",
        "status": "reviewed",
        "purpose": "准备并创建电路",
        "keywords": ["创建电路", "画电路", "生成原理图", "circuit creation", "新建电路"],
        "tools": [
            "begin_circuit_task", "inspect_circuit_target", "inspect_master_evidence",
            "preview_circuit_spec", "preview_circuit_geometry", "prepare_circuit_creation",
            "inspect_created_circuit", "release_created_circuit",
            "get_circuit_operation", "preflight_circuit_project",
        ],
        "subgroups": [],
        "conditions": [
            {
                "when": "preview/prepare 通过且任务模式已确认",
                "tools": [
                    {"tool": "execute_circuit_operation",
                     "operation": "create_circuit_from_plan"},
                ],
            },
        ],
        "recovery": ["get_circuit_operation", "inspect_created_circuit"],
        "connections": [
            "写操作只走受控入口；恢复用 get_circuit_operation，未知写状态不重放。",
            "创建后必须读回核对，release 只释放上下文不删除既有视图。",
            "几何证据只从工具取：bind_pdk_device 返回的 geometry 行（occupied_bbox、端子锚点、"
            "master_revision）或 binding_refs 让 preview/prepare 自动解析 bindings+geometry；"
            "只读核对用 get_pdk_device 的 geometry section；任意已保存 master 的视图与"
            "symbol body 证据用 inspect_master_evidence 批量读取（occupied_bbox、pin 中心"
            "与建议逃逸方向，逃逸最终由绑定链决定）。",
            "参数构造用 mcp_help 的 schema/example 取权威模式与受控样例（spec/bindings/"
            "geometry/layout 形状）；不要用 shell 从旧会话附件或站点文档重建历史参数。",
            "所有 OpenAccess 数据都通过平台工具（CDNS-IC/EMPY-AE/SNPS-CC）的 API 读写："
            "不得用 shell 或落盘镜像/预览产物访问 OA 数据库；grep/find 等文本检索只用于"
            "日志与输出文本。",
        ],
    },
    {
        "id": "library_create",
        "owner": "cadai",
        "status": "draft",
        "purpose": "准备并创建工程库，按用户选择绑定工艺",
        "keywords": ["新建库", "创建库", "设计库", "library", "create library",
                     "绑定工艺", "library create"],
        "tools": [
            "begin_circuit_task", "prepare_library",
            "create_circuit_library", "preflight_circuit_project",
            "get_circuit_operation",
        ],
        "subgroups": [],
        "conditions": [
            {
                "when": "prepare_ref 已就绪且库名/路径/工艺选择已确认",
                "tools": [
                    {"tool": "execute_circuit_operation",
                     "operation": "create_circuit_library"},
                ],
            },
        ],
        "recovery": ["get_circuit_operation"],
        "connections": [
            "可直接用 execute_circuit_operation(create_circuit_library) 提供 library/可选工艺；"
            "prepare_library 仅用于分步审阅，旧名 prepare_circuit_library 保留兼容。",
            "已有库使用另一工艺时先询问用户：新建兼容库、明确确认后 reattach，或停止。",
            "创建后必须读取库路径、工艺绑定和视图类型；失败或未知状态用原 request_id 查询，不重放。",
        ],
    },
    {
        "id": "cdf_edit",
        "owner": "cadai",
        "status": "reviewed",
        "purpose": "在既有原理图上做受控参数编辑（实例 CDF 更新链路）",
        "keywords": ["改参数", "修改参数", "编辑电路", "编辑实例", "cdf 更新", "参数更新",
                     "circuit edit", "cdf update"],
        "tools": [
            "begin_circuit_task", "inspect_circuit_target", "inspect_cdf",
            "prepare_cdf_update", "apply_cdf_update", "get_circuit_operation",
            "preflight_circuit_project",
        ],
        "subgroups": [],
        "conditions": [
            {
                "when": "cdf_ref 新鲜、prepare 通过且目标仍为 intent=edit",
                "tools": [
                    {"tool": "execute_circuit_operation",
                     "operation": "apply_cdf_update"},
                ],
            },
        ],
        "recovery": ["get_circuit_operation", "inspect_cdf"],
        "connections": [
            "prepare_cdf_update 只读不写、不跑回调；写入只走受控入口 execute_circuit_operation。",
            "失败或未知结果用 get_circuit_operation 与同一 request_id 恢复，禁止重放；"
            "写前自动留备份并按序回读。",
        ],
    },
    {
        "id": "testbench_create",
        "owner": "cadai",
        "status": "reviewed",
        "purpose": "为既有 DUT 建测试平台",
        "keywords": ["创建TB", "测试平台", "testbench", "仿真环境搭建", "TB"],
        "tools": [],
        "subgroups": ["schematic_read", "symbol_prepare", "pdk_consume", "circuit_create"],
        "conditions": [],
        "recovery": [],
        "connections": [
            "必须先确认 DUT symbol 接口；不能复制 DUT 内部器件替代实例。",
            "组合只合并成员，不自动执行整套步骤；按任务阶段选择调用。",
        ],
    },
    {
        "id": "simulation_run",
        "owner": "cadai",
        "status": "reviewed",
        "purpose": "运行已定义仿真并保持终态核对",
        "keywords": ["跑仿真", "运行仿真", "simulation", "spectre", "仿真"],
        "tools": [
            "preview_simulation_recipe", "inspect_simulation_setup",
            "get_simulation_recipe_status", "stop_simulation_recipe",
            "release_simulation_setup",
            "preflight_circuit_project",
        ],
        "subgroups": [],
        "conditions": [
            {
                "when": "setup 已定义且前置数据确认",
                "tools": [
                    {"tool": "execute_circuit_operation",
                     "operation": "create_simulation_setup"},
                    {"tool": "execute_circuit_operation", "operation": "run_simulation_setup"},
                ],
            },
        ],
        "recovery": [
            "get_simulation_recipe_status", "stop_simulation_recipe",
            "release_simulation_setup",
        ],
        "connections": [
            "run_ref → status/stop；未终态前恢复能力持续可达。",
            "停机与释放必须核对回执，不自动重放。",
        ],
    },
]

# Owner-local index; the assembled map comes from task_group_resolver.merge.
GROUPS_BY_ID = {group["id"]: group for group in GROUPS}

__all__ = ["GROUPS", "GROUPS_BY_ID"]
