"""Bounded exact update reviews, independent of the host question text budget."""

from .jsonio import encode, fail

MAX_REVIEW_BYTES = 10000


def validate_scope(scope):
    size = len(encode(scope))
    if size > MAX_REVIEW_BYTES:
        files = "; ".join(
            f"{row['file']}: {len(encode(row))} bytes" for row in scope["changes"]
        )
        fail(f"PDK review is {size} bytes; limit is {MAX_REVIEW_BYTES} bytes "
             f"of UTF-8 formatted before/after differences, not patch input. Files: {files}. "
             "Split by file, then by parameter group within a large CDF file. "
             "Prepare, confirm and apply each batch, then read the new revision before the next batch. "
             "No source validation or publication was performed.", "pdk_data_limit")


def question(scope, ref):
    changes = scope["changes"]
    count = sum(len(row["changes"]) for row in changes)
    return {
        "id": "pdk_data_update", "header": "确认 PDK 数据",
        "question": (f"请核对审阅详情中按文件分组的完整差异：{scope['library']}，"
                     f"{len(changes)} 个文件、{count} 项字段差异。是否将这些变更保存到当前工作区？\n"
                     f"基于修订：{scope['revision']}\n范围：{ref}"),
        "options": [{"label": "确认更新", "description": "将详情所示的全部变更保存到当前工作区"},
                    {"label": "取消更新", "description": "保留当前数据修订"}],
    }
