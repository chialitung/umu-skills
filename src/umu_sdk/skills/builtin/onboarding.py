"""账号开通与报名相关 Skill."""

from __future__ import annotations

import sys
from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="batch_onboard_users",
    description="批量创建账号并为其报名指定课程（在当前 student 会话中报名）",
    required_servers=["admin", "student"],
    return_description="每个账号的创建与报名结果",
)
async def batch_onboard_users(
    ctx: SkillContext,
    users: list[dict[str, Any]],
    enroll_id: str,
) -> dict[str, Any]:
    """批量创建账号并报名课程.

    Args:
        users: 每个元素为账号信息字典，推荐格式：
            {"user_name": "", "accounts": "邮箱", "role_type": 1}
            为兼容旧调用，也支持 {"name": "", "email": "", "role": "student"}。
        enroll_id: 报名 ID，来自 stu_get_course_structure 返回的 enroll_id。

    Returns:
        标准返回信封，data 中 reports 包含每条记录的结果。
    """
    total = len(users)
    reports: list[dict[str, Any]] = []

    ctx.logger.info("[batch_onboard_users] 开始处理 %d 条账号", total)

    for index, user in enumerate(users, start=1):
        user_name, accounts, role_type = _normalize_user_info(user)

        print(
            f"[batch_onboard_users] 正在处理第 {index} / {total} 条: {user_name}",
            file=sys.stderr,
        )

        report: dict[str, Any] = {
            "user_name": user_name,
            "accounts": accounts,
            "role_type": role_type,
            "create_success": False,
            "enroll_success": False,
            "error": "",
        }

        if not accounts:
            report["error"] = "缺少账号邮箱（accounts）"
            reports.append(report)
            continue

        # 1. 创建账号
        create_result = await ctx.call_tool(
            server="admin",
            tool="adm_create_account",
            arguments={
                "user_name": user_name,
                "accounts": accounts,
                "role_type": role_type,
            },
        )
        if not create_result["success"]:
            report["error"] = create_result.get("error_message", "创建账号失败")
            reports.append(report)
            continue

        report["create_success"] = True
        user_id = _extract_user_id(create_result.get("data"))
        report["user_id"] = user_id

        # 2. 报名课程（在当前 student 会话中执行）
        enroll_result = await ctx.call_tool(
            server="student",
            tool="stu_enroll_course",
            arguments={
                "enroll_id": enroll_id,
            },
        )
        if not enroll_result["success"]:
            report["error"] = enroll_result.get("error_message", "报名失败")
            reports.append(report)
            continue

        report["enroll_success"] = True
        reports.append(report)

    created = sum(1 for r in reports if r["create_success"])
    enrolled = sum(1 for r in reports if r["enroll_success"])

    ctx.logger.info(
        "[batch_onboard_users] 完成：创建 %d/%d，报名 %d/%d",
        created,
        total,
        enrolled,
        total,
    )

    return {
        "success": True,
        "data": {
            "total": total,
            "created": created,
            "enrolled": enrolled,
            "reports": reports,
        },
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


def _normalize_user_info(user: dict[str, Any]) -> tuple[str, str, int]:
    """将多种用户输入格式归一化为 (user_name, accounts, role_type).

    支持：
    - 推荐格式：{"user_name": "", "accounts": "", "role_type": 1}
    - 兼容格式：{"name": "", "email": "", "role": "student"}
    """
    user_name = user.get("user_name") or user.get("name") or ""
    accounts = user.get("accounts") or user.get("email") or ""

    role_type: int | None = user.get("role_type")
    if role_type is None:
        role = user.get("role", "student")
        role_type = _role_to_type(role)

    return str(user_name), str(accounts), int(role_type)


def _role_to_type(role: Any) -> int:
    """将角色字符串或数字映射为 role_type."""
    if isinstance(role, int):
        return role
    role_str = str(role).lower().strip()
    mapping = {
        "student": 1,
        "学员": 1,
        "teacher": 2,
        "讲师": 2,
        "manager": 3,
        "学习负责人": 3,
        "admin": 4,
        "系统管理员": 4,
    }
    return mapping.get(role_str, 1)


def _extract_user_id(data: Any) -> str | None:
    """从创建账号结果中提取用户 ID."""
    if not isinstance(data, dict):
        return None
    user_id = data.get("user_id") or data.get("userId") or data.get("id")
    return str(user_id) if user_id else None


__all__ = ["batch_onboard_users"]
