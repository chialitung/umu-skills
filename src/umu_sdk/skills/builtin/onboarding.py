"""账号开通与报名相关 Skill."""

from __future__ import annotations

import sys
from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="batch_onboard_users",
    description="批量创建学员账号并为其报名指定课程",
    required_servers=["admin", "student"],
    return_description="每个账号的创建与报名结果",
)
async def batch_onboard_users(
    ctx: SkillContext,
    users: list[dict[str, Any]],
    course_identifier: str,
) -> dict[str, Any]:
    """批量创建学员账号并报名课程.

    Args:
        users: 每个元素为 {"name": "", "phone": "", "email": ""} 的字典列表。
        course_identifier: 要报名的课程标识。

    Returns:
        标准返回信封，data 中 reports 包含每条记录的结果。
    """
    total = len(users)
    reports: list[dict[str, Any]] = []

    ctx.logger.info("[batch_onboard_users] 开始处理 %d 条账号", total)

    for index, user in enumerate(users, start=1):
        name = user.get("name", "")
        phone = user.get("phone", "")
        email = user.get("email", "")
        print(
            f"[batch_onboard_users] 正在处理第 {index} / {total} 条: {name}",
            file=sys.stderr,
        )

        report: dict[str, Any] = {
            "name": name,
            "phone": phone,
            "email": email,
            "create_success": False,
            "enroll_success": False,
            "error": "",
        }

        # 1. 创建账号
        create_result = await ctx.call_tool(
            server="admin",
            tool="adm_create_account",
            arguments={
                "name": name,
                "phone": phone,
                "email": email,
                "role": "student",
            },
        )
        if not create_result["success"]:
            report["error"] = create_result.get("error_message", "创建账号失败")
            reports.append(report)
            continue

        report["create_success"] = True
        user_id = _extract_user_id(create_result.get("data"))
        report["user_id"] = user_id

        # 2. 报名课程
        enroll_result = await ctx.call_tool(
            server="student",
            tool="stu_enroll_course",
            arguments={
                "course_identifier": course_identifier,
                # 如果子 MCP 支持按 user_id 代报名可传入
                "user_id": user_id,
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


def _extract_user_id(data: Any) -> str | None:
    """从创建账号结果中提取用户 ID."""
    if not isinstance(data, dict):
        return None
    user_id = data.get("user_id") or data.get("userId") or data.get("id")
    return str(user_id) if user_id else None


__all__ = ["batch_onboard_users"]
