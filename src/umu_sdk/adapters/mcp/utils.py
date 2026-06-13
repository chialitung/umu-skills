"""MCP server 共享工具函数."""

from __future__ import annotations

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


__all__ = ["get_login_identity", "format_login_summary"]
