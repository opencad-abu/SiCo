"""Qt-free, bounded HTML presentation for tool results."""

from __future__ import annotations

import html

from .display import data_html

TITLES = {
    "codex_command": "命令执行", "codex_file_change": "文件改动",
    "codex_web_search": "网页检索", "codex_image_generation": "图像生成",
    "get_context": "设计上下文",
    "get_project_context": "工程上下文",
    "get_entry_context": "设计上下文",
    "read_ade_setup": "ADE 设置",
    "read_ade_history": "History 清单",
    "read_artifact": "归档数据",
    "read_maestro_results": "Maestro 点结果",
    "query_maestro_results": "点结果查询",
    "evaluate_measurement_specs": "测量规格对照",
    "read_maestro_waveform": "瞬态波形",
    "read_maestro_ac_waveform": "AC 波形",
    "measure_waveform": "波形测量",
    "measure_waveform_pair": "双波形测量",
    "evaluate_waveform_specs": "波形规格对照",
    "set_copilot_stage": "任务阶段",
    "list_copilot_data": "数据索引",
    "publish_copilot_report": "发布报告",
    # 历史会话里记录的是旧名字。
    "set_studio_stage": "任务阶段",
    "list_studio_data": "数据索引",
    "publish_studio_report": "发布报告",
}


def text(value):
    return html.escape(str(value if value is not None else ""))


def table(headers, rows, *, wrap=False):
    cell_style = "" if wrap else " style='white-space: nowrap'"
    return (
        "<table border='1' cellspacing='0' cellpadding='5'><tr>"
        + "".join("<th style='white-space: nowrap'>" + text(h) + "</th>" for h in headers)
        + "</tr>"
        + "".join(
            "<tr>"
            + "".join("<td" + cell_style + ">" + text(cell) + "</td>" for cell in row)
            + "</tr>"
            for row in rows
        )
        + "</table>"
    )


def result_html(result, *, context=False):
    try:
        return _result_html(result, context=context)
    except (KeyError, TypeError, AttributeError, ValueError):
        return "<p>结果暂时无法整理为表格；数据明细保留了已收到的内容。</p>"


def context_html(data):
    source = data.get("source")
    source = source if isinstance(source, dict) else data
    cellview = source.get("edit_cellview") or source.get("cellview") or {}
    rows = {}
    if data.get("scope") == "project":
        rows["工程目录"] = data.get("cwd", "")
        rows["工程绑定"] = "有效" if data.get("valid") is True else "不可用"
        rows["原入口窗口"] = "有效" if data.get("source_valid") else "已关闭或切换（以下为原来源）"
    kind = source.get("kind")
    if kind:
        rows["窗口"] = {
            "ciw": "CIW", "schematic": "原理图", "symbol": "Symbol",
            "layout": "版图", "ade": "ADE", "other": "Virtuoso",
        }.get(kind, "Virtuoso")
    if isinstance(cellview, dict):
        for key, label in (("lib", "库"), ("cell", "单元"), ("view", "视图")):
            if cellview.get(key):
                rows[label] = cellview[key]
        if isinstance(cellview.get("modified"), bool):
            rows["存在未保存修改"] = cellview["modified"]
    for key, label in (("test", "Test"), ("history", "History")):
        if data.get(key):
            rows[label] = data[key]
    ade = data.get("ade")
    if isinstance(ade, dict):
        for key, label in (
            ("selected_test", "Test"), ("selected_history", "History"), ("test_count", "Test 数"),
        ):
            if ade.get(key) is not None:
                rows[label] = ade[key]
    design = data.get("design")
    if isinstance(design, dict):
        for key, label in (("instance_count", "实例数"), ("net_count", "网络数")):
            if type(design.get(key)) is int:
                rows[label] = design[key]
    if data.get("valid") is False or data.get("ok") is False:
        rows["来源状态"] = "不可用，待核对"
    elif data.get("valid") is True:
        rows["来源状态"] = "有效"
    if data.get("source") == "simulated":
        rows["数据来源"] = "模拟数据"
    result = data_html(rows) if rows else "<p>暂无可读的设计上下文摘要。</p>"
    if isinstance(ade, dict):
        result += "<p>数据依据：当前 ADE 设置；未读取该 History 的仿真结果。</p>"
    return result


def _result_html(result, *, context=False):
    parts = ["<p>" + text(result.get("summary", "")) + "</p>"]
    if result.get("truncated"):
        parts.append("<p>结果已截断；完整数据已归档。</p>")
        parts.append("<p>" + text(result.get("artifact", {}).get("path", "")) + "</p>")
    data = result.get("data")
    if not isinstance(data, dict):
        if data is not None:
            parts.append(data_html(data))
        return "".join(parts)
    if data.get("truncated"):
        parts.append("<p><b>已达到查询上限；此处只显示部分数据。</b></p>")
    if data.get("contract") not in {"ade_setup.v1", "ade_history.v1"} and (
        context or data.get("schema") == "cad.entry.context.v1"
    ):
        parts.append(context_html(data))
    elif data.get("contract") == "ade_setup.v1":
        parts.append("<p>数据来源：当前 ADE 设置。输出表达式尚未求值。</p>")
        if data.get("bound_history"):
            parts.append(
                "<p>入口 History："
                + text(data["bound_history"])
                + "；下面是当前设置，不代表该 History 的运行配置。</p>"
            )
        parts.append(
            table(
                ["Test", "启用", "Testbench", "Simulator", "输出数"],
                [
                    [
                        test.get("name"),
                        "是" if test.get("enabled") else "否",
                        " / ".join(
                            str(test.get("testbench", {}).get(key, ""))
                            for key in ("lib", "cell", "view")
                        ),
                        test.get("simulator"),
                        test.get("output_count"),
                    ]
                    for test in data.get("tests", [])
                ],
            )
        )
        parts.append(
            "<p>输出配置</p>"
            + table(
                ["Test", "名称", "表达式", "信号"],
                [
                    [
                        test.get("name"),
                        output.get("name"),
                        output.get("expression"),
                        output.get("signal"),
                    ]
                    for test in data.get("tests", [])
                    for output in test.get("outputs", [])
                ],
            )
        )
        parts.append(
            "<p>全局变量</p>"
            + table(
                ["名称", "配置值"],
                [[var.get("name"), var.get("value")] for var in data.get("global_variables", [])],
            )
        )
        parts.append("<p>Corner：" + text(", ".join(data.get("corners", []))) + "</p>")
    elif data.get("contract") == "ade_history.v1":
        parts.append("<p>History 元数据；计数不代表仿真通过，未读取实际数值或波形。</p>")
        if not data.get("histories"):
            parts.append("<p>当前 session 尚无 History。</p>")
        parts.append(
            table(
                ["History", "完成点 / 总点", "结果引用"],
                [
                    [
                        row.get("name"),
                        " / ".join(str(row["point_counts"][key]) for key in ("completed", "total"))
                        if row.get("point_counts")
                        else "未提供",
                        row.get("results_reference"),
                    ]
                    for row in data.get("histories", [])
                ],
            )
        )
    else:
        parts.append(data_html(data))
    return "".join(parts)
