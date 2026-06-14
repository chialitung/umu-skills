"""MCP server 共享工具函数."""

from __future__ import annotations

import sys
from typing import Any

from ...core.client import UMUClient


def get_login_identity(client: UMUClient) -> dict[str, str]:
    """获取当前登录用户的身份标识信息.

    调用 /uapi/v1/user/get，提取用户邮箱、姓名及企业信息。
    如果请求失败或响应格式异常，返回空字典。

    Returns:
        {
            "user_id": ...,       # 通常是 umu_id / student_id / teacher_id 的字符串形式
            "email": ...,
            "user_name": ...,
            "enterprise_id": ...,
            "enterprise_name": ...,
        }
    """
    try:
        r = client.get(client.desktop_url("/uapi/v1/user/get"))
        data = r.get("data", {})
        enterprise_info = data.get("enterprise_info", {}) or {}
        return {
            "user_id": str(data.get("umu_id", "") or data.get("user_id", "")),
            "email": data.get("email", "") or "",
            "user_name": data.get("user_name", "") or "",
            "enterprise_id": str(enterprise_info.get("enterprise_id", "")),
            "enterprise_name": enterprise_info.get("show_name", "")
            or enterprise_info.get("real_name", "")
            or enterprise_info.get("enterprise_name", "")
            or "",
        }
    except Exception:
        return {}


def format_login_summary(
    username: str,
    source: str,
    identity: dict[str, Any],
) -> str:
    """格式化登录摘要，用于日志输出（不暴露密码）."""
    enterprise_id = identity.get("enterprise_id", "") or "unknown"
    enterprise_name = identity.get("enterprise_name", "") or "unknown"
    user_name = identity.get("user_name", "") or username
    return (
        f"{username} (来源: {source}, 企业: {enterprise_id}/{enterprise_name}, "
        f"用户: {user_name})"
    )


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
    """打印分页进度到 stderr（遵循 CLAUDE.md 分页进度上报规则）.

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


__all__ = [
    "get_login_identity",
    "format_login_summary",
    "report_pagination_progress",
]
