"""Curated worked examples for the shared MCP workflows, served by mcp_help.

An example is a shape reference, not a second contract: every fragment is
resolved by tests through the real preview validators, and any value the live
session must supply carries an explicit ``REPLACE:`` token.  Nothing here
executes a tool, touches EDA or calls a model; the help layer owns dispatch,
budgets and availability.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterable
from typing import Any

SCHEMA = "sico.mcp.examples.v1"
BUDGET_BYTES = 8192
REPLACE_TOKEN = "REPLACE:"

# One authoritative worked chain: a grounded inverting testbench over a design
# symbol, a vdc and a vpulse, with one basic/gnd per grounded terminal.  The
# numbers are illustrative but self-consistent: tests materialize the tokens
# below and run the real preview validators over the result.
_PARTS_JSON = """{
"spec": {
  "binding_snapshot": "REPLACE: binding snapshot_ref",
  "instances": [
   {
    "connections": {"A":"A","VDD":"VDD","VSS":"VSS","Y":"Y"},
    "id": "I_DUT",
    "master": "m_dut",
    "name": "I_myInv_1",
    "parameters": {},
    "role": "dut",
    "unconnected": {}
   },
   {
    "connections": {"MINUS":"VSS","PLUS":"VDD"},
    "id": "VDD_SRC",
    "master": "m_vdc",
    "name": "VDD_SRC",
    "parameters": {"vdc":"2.5"},
    "role": "stimulus",
    "unconnected": {}
   },
   {
    "connections": {"MINUS":"VSS","PLUS":"A"},
    "id": "VIN_SRC",
    "master": "m_vpulse",
    "name": "VIN_SRC",
    "parameters": {"per":"10n","pw":"5n","td":"0","tf":"10p","tr":"10p","v1":"0","v2":"2.5"},
    "role": "stimulus",
    "unconnected": {}
   },
   {
    "connections": {"gnd!":"VSS"},
    "id": "GND_DUT",
    "master": "m_gnd",
    "name": "GND_DUT",
    "parameters": {},
    "role": "auxiliary",
    "unconnected": {}
   },
   {
    "connections": {"gnd!":"VSS"},
    "id": "GND_VDD",
    "master": "m_gnd",
    "name": "GND_VDD",
    "parameters": {},
    "role": "auxiliary",
    "unconnected": {}
   },
   {
    "connections": {"gnd!":"VSS"},
    "id": "GND_VIN",
    "master": "m_gnd",
    "name": "GND_VIN",
    "parameters": {},
    "role": "auxiliary",
    "unconnected": {}
   }
  ],
  "kind": "testbench",
  "nets": [
   {"name":"A","scope":"local"},
   {"name":"VDD","scope":"local"},
   {"name":"VSS","scope":"ground"},
   {"name":"Y","scope":"local"}
  ],
  "notes": [],
  "ports": [{"direction":"output","name":"Y","net":"Y"}],
  "project_ref": "REPLACE: project_ref from get_project_context",
  "schema": "cad.circuit.spec.v1",
  "target": {"library":"myDesignLib","cell":"tb_myinv","view":"schematic"}
 },
"bindings": {
  "evidence_kind": "session_capture",
  "masters": [
   {
    "callbacks": {"status":"none"},
    "id": "m_dut",
    "kind": "design",
    "library_path": "REPLACE: absolute path of the myDesignLib library",
    "parameters": [],
    "parameters_complete": true,
    "revision": "REPLACE: live master revision",
    "target": {"library":"myDesignLib","cell":"myInv","view":"symbol"},
    "terminals": [
     {"direction":"input","name":"A"},
     {"direction":"inputOutput","name":"VDD"},
     {"direction":"inputOutput","name":"VSS"},
     {"direction":"output","name":"Y"}
    ],
    "terminals_complete": true
   },
   {
    "callbacks": {"status":"none"},
    "id": "m_vdc",
    "kind": "device",
    "library_path": "REPLACE: absolute path of the analogLib library",
    "parameters": [{"editable":true,"name":"vdc","type":"string"}],
    "parameters_complete": true,
    "revision": "REPLACE: live master revision",
    "target": {"cell":"vdc","library":"analogLib","view":"symbol"},
    "terminals": [{"direction":"inputOutput","name":"PLUS"},{"direction":"inputOutput","name":"MINUS"}],
    "terminals_complete": true
   },
   {
    "callbacks": {"status":"none"},
    "id": "m_vpulse",
    "kind": "device",
    "library_path": "REPLACE: absolute path of the analogLib library",
    "parameters": [
     {"editable":true,"name":"v1","type":"string"},
     {"editable":true,"name":"v2","type":"string"},
     {"editable":true,"name":"td","type":"string"},
     {"editable":true,"name":"tr","type":"string"},
     {"editable":true,"name":"tf","type":"string"},
     {"editable":true,"name":"pw","type":"string"},
     {"editable":true,"name":"per","type":"string"}
    ],
    "parameters_complete": true,
    "revision": "REPLACE: live master revision",
    "target": {"cell":"vpulse","library":"analogLib","view":"symbol"},
    "terminals": [{"direction":"inputOutput","name":"PLUS"},{"direction":"inputOutput","name":"MINUS"}],
    "terminals_complete": true
   },
   {
    "callbacks": {"status":"none"},
    "id": "m_gnd",
    "kind": "device",
    "library_path": "REPLACE: absolute path of the basic library",
    "parameters": [],
    "parameters_complete": true,
    "revision": "REPLACE: live master revision",
    "target": {"cell":"gnd","library":"basic","view":"symbol"},
    "terminals": [{"direction":"inputOutput","name":"gnd!"}],
    "terminals_complete": true
   }
  ],
  "project_ref": "REPLACE: project_ref from get_project_context",
  "schema": "cad.circuit.bindings.v1",
  "snapshot_ref": "REPLACE: binding snapshot_ref",
  "source_ref": "REPLACE: selection source_ref"
 },
"geometry": {
  "binding_digest": "REPLACE: binding_digest from preview_circuit_spec",
  "binding_snapshot": "REPLACE: binding snapshot_ref",
  "coordinate_system": "master_local_schematic_uu",
  "evidence_kind": "session_capture",
  "instances": [
   {
    "instance": "I_DUT",
    "master_revision": "REPLACE: live master revision",
    "occupied_bbox": [[-0.3125,-1.875],[3.3125,0]],
    "parameters_digest": "REPLACE: digest of this instance parameters",
    "terminals": [
     {"anchors":[{"escape":"left","id":"p0","xy":[-0.3125,-1.5]}],"name":"A"},
     {"anchors":[{"escape":"left","id":"p0","xy":[-0.3125,-0.375]}],"name":"VDD"},
     {"anchors":[{"escape":"left","id":"p0","xy":[-0.3125,-0.75]}],"name":"VSS"},
     {"anchors":[{"escape":"right","id":"p0","xy":[3.3125,-0.9375]}],"name":"Y"}
    ]
   },
   {
    "instance": "VDD_SRC",
    "master_revision": "REPLACE: live master revision",
    "occupied_bbox": [[-0.06875,-0.375],[0.06875,0]],
    "parameters_digest": "REPLACE: digest of this instance parameters",
    "terminals": [
     {"anchors":[{"escape":"up","id":"p0","xy":[0,0]}],"name":"PLUS"},
     {"anchors":[{"escape":"down","id":"p0","xy":[0,-0.375]}],"name":"MINUS"}
    ]
   },
   {
    "instance": "VIN_SRC",
    "master_revision": "REPLACE: live master revision",
    "occupied_bbox": [[-0.19375,-0.375],[0.06875,0]],
    "parameters_digest": "REPLACE: digest of this instance parameters",
    "terminals": [
     {"anchors":[{"escape":"up","id":"p0","xy":[0,0]}],"name":"PLUS"},
     {"anchors":[{"escape":"down","id":"p0","xy":[0,-0.375]}],"name":"MINUS"}
    ]
   },
   {
    "instance": "GND_DUT",
    "master_revision": "REPLACE: live master revision",
    "occupied_bbox": [[-0.0625,-0.25],[0.0625,0]],
    "parameters_digest": "REPLACE: digest of this instance parameters",
    "terminals": [{"anchors":[{"escape":"up","id":"p0","xy":[0,0]}],"name":"gnd!"}]
   },
   {
    "instance": "GND_VDD",
    "master_revision": "REPLACE: live master revision",
    "occupied_bbox": [[-0.0625,-0.25],[0.0625,0]],
    "parameters_digest": "REPLACE: digest of this instance parameters",
    "terminals": [{"anchors":[{"escape":"up","id":"p0","xy":[0,0]}],"name":"gnd!"}]
   },
   {
    "instance": "GND_VIN",
    "master_revision": "REPLACE: live master revision",
    "occupied_bbox": [[-0.0625,-0.25],[0.0625,0]],
    "parameters_digest": "REPLACE: digest of this instance parameters",
    "terminals": [{"anchors":[{"escape":"up","id":"p0","xy":[0,0]}],"name":"gnd!"}]
   }
  ],
  "project_ref": "REPLACE: project_ref from get_project_context",
  "schema": "cad.circuit.geometry.v1",
  "source_ref": "REPLACE: geometry source_ref"
 },
"layout": {
  "anchor_choices": {},

  "clearance": 0.25,
  "grid": 0.0625,
  "instances": {
   "GND_DUT": {"xy":[-1,-0.75],"orientation":"R270"},
   "GND_VDD": {"xy":[-3.5,-0.5],"orientation":"R0"},
   "GND_VIN": {"xy":[-3.5,-3],"orientation":"R0"},
   "I_DUT": {"xy":[0,0],"orientation":"R0"},
   "VDD_SRC": {"xy":[-3.5,0.5],"orientation":"R0"},
   "VIN_SRC": {"xy":[-3.5,-1.875],"orientation":"R0"}
  },
  "pin_links": [
   [{"instance":"VDD_SRC","terminal":"MINUS"},{"instance":"GND_VDD","terminal":"gnd!"}],
   [{"instance":"VIN_SRC","terminal":"MINUS"},{"instance":"GND_VIN","terminal":"gnd!"}],
   [{"instance":"I_DUT","terminal":"VSS"},{"instance":"GND_DUT","terminal":"gnd!"}]
  ],
  "ports": {"Y":{"escape":"left","role":"signal","xy":[6.5,-0.9375]}},
  "routing": {"mode":"stub"},
  "stub_length": 0.625
 }
}
"""
PARTS = json.loads(_PARTS_JSON)

_SPEC_REPLACE = [
    "spec.project_ref", "spec.binding_snapshot",
    "bindings.project_ref", "bindings.snapshot_ref", "bindings.source_ref",
    "bindings.masters[].revision", "bindings.masters[].library_path",
]
_GEOMETRY_REPLACE = _SPEC_REPLACE + [
    "geometry.project_ref", "geometry.binding_snapshot", "geometry.source_ref",
    "geometry.instances[].master_revision", "geometry.instances[].parameters_digest",
    "geometry.binding_digest", "preview_digest",
]
_CHAIN_NOTES = [
    "形状参考：所有 REPLACE: 值必须替换为本会话实时数据；digest/ref 只能取自对应工具或上一步预览返回。",
    "VSS 用 scope=ground；每个接地点配一个 basic/gnd 实例，并用 layout.pin_links 连到目标接地端子（gnd!）。",
    "design master（role=dut）实例名必须是 I_<cell>_<n>；锚点与 bbox 用 inspect_master_evidence 读取；多锚点端子需在 layout.anchor_choices 选 pN。",
    "parameters_digest 规则：sha256(json.dumps(parameters,sort_keys=True,"
    "separators=(\",\",\":\"),ensure_ascii=False).encode(\"utf-8\"))；空 {} = "
    "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a。",
]

EXAMPLES: dict[str, dict[str, Any]] = {
    "begin_circuit_task": {
        "summary": "开始一个后台电路任务，task_ref 形如 task:<task_id>。",
        "arguments": {"task_id": "myinv_bg_1", "presentation": "background",
                      "choice_confirmed": True},
        "notes": ["presentation=background 不打开编辑器窗口；choice_confirmed 表示用户已确认呈现方式。"],
    },
    "inspect_circuit_target": {
        "summary": "确认目标与已有视图策略后再决定 create/reuse/replace。",
        "arguments": {"target": {"library": "myDesignLib", "cell": "tb_myinv",
                                 "view": "schematic"},
                      "task_ref": "REPLACE: task_ref from begin_circuit_task",
                      "request_id": "tb_myinv-inspect-1"},
        "replace": ["task_ref"],
    },
    "preflight_circuit_project": {
        "summary": "带确切输出目标与工作区事实的工程预检。",
        "arguments": {
            "presentation": "background",
            "targets": [{"library": "myDesignLib", "cell": "tb_myinv", "view": "schematic",
                         "view_type": "schematic", "intent": "create"}],
            "libraries": [{"library": "myDesignLib",
                           "expected_path": "REPLACE: absolute path of the myDesignLib library"}],
            "directories": [{"path": "REPLACE: project workspace path", "purpose": "project",
                             "min_free_bytes": 104857600, "min_free_inodes": 1000}],
            "model_files": [],
        },
        "replace": ["targets[]", "libraries[].expected_path", "directories[].path"],
        "notes": ["纯电路任务只提供 project 目录，不要附加 run 目录；"
                  "ADE 仿真运行目录由 ADE 自行创建。"],
    },
    "inspect_master_evidence": {
        "summary": "批量读取已保存 master 的视图表与 symbol body 证据。",
        "arguments": {"targets": [{"lib": "myDesignLib", "cell": "myInv", "view": "symbol"},
                                  {"lib": "basic", "cell": "gnd"}]},
        "notes": ["返回 occupied_bbox、pin 中心与建议逃逸方向（与绑定链同源）；binding_ready=false 时按 reason 修正。"],
    },
    "preview_circuit_spec": {
        "summary": "最小接地 TB 的 spec+bindings 形状参考。",
        "parts": ["spec", "bindings"],
        "notes": _CHAIN_NOTES + [
            "spec 级字段以此为准：connections 只在 spec.instances[] 实例级出现，spec 顶层没有该字段；"
            "未列出的字段会被 schema 拒绝。"],
        "replace": _SPEC_REPLACE,
    },
    "preview_circuit_geometry": {
        "summary": "同一 TB 的完整几何预览样例（spec/bindings/geometry/layout）。",
        "parts": ["spec", "bindings", "geometry", "layout"],
        "arguments": {"preview_digest": "REPLACE: preview_digest from preview_circuit_spec"},
        "notes": _CHAIN_NOTES + [
            "preview_digest 必须来自同一 spec/bindings 的 preview_circuit_spec 结果，否则报 preview_digest differs。"],
        "replace": _GEOMETRY_REPLACE,
    },
    "prepare_circuit_creation": {
        "summary": "创建前准备：五个大参数与几何预览相同，digest 全部来自上一步预览。",
        "parts": ["spec", "bindings", "geometry", "layout"],
        "arguments": {
            "preview_digest": "REPLACE: preview_digest from preview_circuit_spec",
            "geometry_plan_digest": "REPLACE: geometry_plan_digest from preview_circuit_geometry",
            "task_ref": "REPLACE: task_ref from begin_circuit_task",
            "request_id": "tb_myinv-prepare-1",
        },
        "notes": [
            "大参数与 preview_circuit_geometry 示例相同，另加两个 digest 与 task/request 引用；"
            "重发必须与预览载荷逐字节一致（数组顺序、含端子/参数列表，计入摘要），"
            "任何改动都要重新预览，否则报 preview_digest differs。"],
        "replace": _GEOMETRY_REPLACE + ["geometry_plan_digest", "task_ref"],
    },
    "execute_circuit_operation": {
        "summary": "受控入口封装：create_circuit_from_plan 只带 prepare 回执与 request_id。",
        "arguments": {
            "operation": "create_circuit_from_plan",
            "arguments": {
                "prepare_ref": "REPLACE: prepare_ref from prepare_circuit_creation",
                "geometry_plan_digest": "REPLACE: geometry_plan_digest from preview_circuit_geometry",
                "request_id": "tb_myinv-create-1",
            },
        },
        "notes": ["同一 operation/request_id 重复调用只返回状态，不会重复写入。"],
    },
    "inspect_cdf": {
        "summary": "读取既有实例的有效 CDF（instance 填所在 schematic 的直接实例名，不是 master）。",
        "arguments": {"target": {"library": "myDesignLib", "cell": "tb_myinv",
                                 "view": "schematic"},
                      "instance": "R1", "limit": 16},
        "notes": ["返回的 cdf_ref 供 prepare_cdf_update 使用；翻页用 offset/limit；"
                  "省略 instance 读 master CDF，此时 target 指 master symbol。"],
    },
    "prepare_cdf_update": {
        "summary": "基于新鲜 cdf_ref 准备实例参数更新（只读准备，不写、不跑回调）。",
        "arguments": {
            "cdf_ref": "REPLACE: cdf_ref from a fresh inspect_cdf of the same instance",
            "task_ref": "REPLACE: task_ref from begin_circuit_task",
            "request_id": "tb_myinv-cdf-1",
            "parameters": {"r": "2k"},
            "callback_order": ["r"],
        },
        "notes": ["callback_order 与 parameters 必须同名同集合、逐名一次；"
                  "preflight 确认 intent=edit 后用 execute_circuit_operation"
                  "(operation=apply_cdf_update, prepare_ref, request_id) 写入；"
                  "未知结果用 get_circuit_operation 按原 request_id 恢复，禁止重放。"],
        "replace": ["cdf_ref", "task_ref"],
    },
    "get_circuit_operation": {
        "summary": "按 operation/request_id 读取已登记操作的回执（恢复入口）。",
        "arguments": {"operation": "apply_cdf_update",
                      "request_id": "REPLACE: request_id passed to execute_circuit_operation",
                      "refresh": True},
        "notes": ["refresh=true 读原始 live 身份与回执；false 只读本地记录。"
                  "缺失回执/半写/会话不符都不允许自动重放；operation_not_found 只说明没有"
                  "本地执行记录。"],
        "replace": ["request_id"],
    },
    "preview_simulation_recipe": {
        "summary": "最小瞬态 Spectre recipe 形状参考（target=新建 Maestro；design=已保存 TB）。",
        "arguments": {"recipe": {
            "schema": "cad.simulation.recipe.v1",
            "target": {"library": "myDesignLib", "cell": "tb_myinv", "view": "maestro"},
            "variables": {"vdd": "2.5"},
            "temperature_c": 27,
            "tests": [{
                "name": "tran_inv",
                "design": {"library": "myDesignLib", "cell": "tb_myinv",
                           "view": "schematic"},
                "simulator": "spectre",
                "variables": {"vdd": "2.5"},
                "models": [{"path": "REPLACE: absolute path of the spectre model file",
                            "section": "tt"}],
                "analyses": [{"name": "tran", "options": {"stop": "10n"}}],
                "outputs": [{"name": "Y", "kind": "signal", "value": "/Y",
                             "signal_type": "net", "plot": True, "save": True}],
            }],
        }},
        "notes": [
            "signal 输出 value 用绝对路径（如 /Y、端子 /V0/PLUS）；tran 必须显式 stop，"
            "ac 必须 start/stop；表达式输出需 plot=true 才会求值。",
            "models=[] 表示沿用 ADE 会话已设置的 modelFiles（init_pdk/libInit 之后）；"
            "需要固定或复现时显式列出 path+section。request_id 只允许字母/数字/下划线/"
            "连字符（不要用冒号）；measure_waveform 的窗口单位写 ns（不要写 n）。",
            "表达式输出用 ADE 计算器函数；网络/端子电压写 VT(\"/net\")，"
            "不要用未定义的 V(...)（真实 RDB 表达式日志会报 eval: undefined function V）。",
            "preview 只做本地校验并返回 recipe_digest（models_verified/simulation_qualified "
            "均为 false）；建立与运行走 execute_circuit_operation(operation="
            "create_simulation_setup / run_simulation_setup) 并携带 recipe 与 "
            "recipe_digest/task_ref/request_id；运行前先 inspect_simulation_setup；"
            "completed 不代表规格合格。"],
        "replace": ["recipe.tests[].models[].path"],
    },
    "inspect_simulation_setup": {
        "summary": "核验保留 setup 的 corner/sweep、模型文件与结果 context_ref（不保存改动）。",
        "arguments": {"setup_ref": "REPLACE: setup_ref from create_simulation_setup"},
        "notes": ["context_ref 用于读取通用仿真结果；本调用不保存、不改动 setup。"],
        "replace": ["setup_ref"],
    },
    "get_simulation_recipe_status": {
        "summary": "读取确切保留 history 的进度与测试状态。",
        "arguments": {"run_ref": "REPLACE: run_ref from run_simulation_setup"},
        "notes": ["不推断合格、不关闭前台窗口；completed 只说明本次运行完成。"],
        "replace": ["run_ref"],
    },
    "stop_simulation_recipe": {
        "summary": "请求取消确切保留 history（保留 UI，随后轮询状态）。",
        "arguments": {"run_ref": "REPLACE: run_ref from run_simulation_setup"},
        "notes": ["stop 请求不证明仿真进程已终止；结果未知时不自动重放。"],
        "replace": ["run_ref"],
    },
    "release_simulation_setup": {
        "summary": "释放空闲且未改动的 setup（保留前台 UI）。",
        "arguments": {"setup_ref": "REPLACE: setup_ref from create_simulation_setup"},
        "notes": ["只释放空闲未改动 setup；失败/未知状态保留，不自动重放。"],
        "replace": ["setup_ref"],
    },
}


def tool_view(name: str) -> dict[str, Any] | None:
    """One assembled example for a tool, or None when none is curated."""
    row = EXAMPLES.get(name)
    if row is None:
        return None
    arguments = {part: copy.deepcopy(PARTS[part]) for part in row.get("parts", [])}
    arguments.update(copy.deepcopy(row.get("arguments", {})))
    view: dict[str, Any] = {
        "schema": SCHEMA,
        "tool": name,
        "summary": row["summary"],
        "arguments": arguments,
    }
    for key in ("notes", "replace"):
        if row.get(key):
            view[key] = copy.deepcopy(row[key])
    return view


def group_rows(names: Iterable[str]) -> list[dict[str, Any]]:
    """Registered examples among the given member names, in supplied order."""
    return [{"tool": name, "summary": EXAMPLES[name]["summary"]}
            for name in names if name in EXAMPLES]


__all__ = ["BUDGET_BYTES", "EXAMPLES", "PARTS", "REPLACE_TOKEN", "SCHEMA",
           "group_rows", "tool_view"]
