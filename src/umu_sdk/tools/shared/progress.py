# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""分页进度上报工具.

所有自动分页 / 全量获取循环都应通过此模块向 stderr 报告进度，
避免写入 stdout 破坏 MCP stdio 通信。
"""

from __future__ import annotations

import sys
from typing import Any


def report_pagination_progress(
    tool_name: str,
    current_page: int,
    fetched_count: int,
    total_count: int,
    batch_size: int,
    *,
    is_complete: bool = False,
    is_safety_limit: bool = False,
    file: Any | None = None,
) -> None:
    """打印分页进度到 stderr（遵循项目分页进度上报规则）.

    Args:
        tool_name: 工具名前缀，如 "adm_list_accounts"。
        current_page: 当前已获取的页码。
        fetched_count: 累计已获取条数。
        total_count: 预期总条数（首次响应中获得）。
        batch_size: 每页条数。
        is_complete: 为 True 时打印完成提示。
        is_safety_limit: 为 True 时打印 50 页安全上限警告。
        file: 输出目标，默认 None 表示使用当前 sys.stderr。
    """
    if file is None:
        file = sys.stderr

    prefix = f"[{tool_name}]"

    if is_complete:
        print(
            f"{prefix} 获取完成，共 {fetched_count} 条，合计 {current_page} 页",
            file=file,
        )
        return

    if is_safety_limit:
        print(
            f"{prefix} 警告：达到 50 页安全上限，停止获取"
            f"（已获取 {fetched_count} 条）",
            file=file,
        )
        return

    progress_pct = ""
    if total_count > 0:
        pct = min(100, int(fetched_count / total_count * 100))
        progress_pct = f" ({pct}%)"

    if total_count > 0 and current_page == 1:
        estimated_pages = max(1, (total_count + batch_size - 1) // batch_size)
        print(
            f"{prefix} 共 {total_count} 条，预计 {estimated_pages} 页",
            file=file,
        )

    print(
        f"{prefix} 已获取第 {current_page} 页，"
        f"累计 {fetched_count} / {total_count} 条{progress_pct}",
        file=file,
    )
