# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Admin 数据查询相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="get_learning_records",
    description="查询企业学员课程学习明细",
    required_capabilities=['data_query'],
    return_description="学习记录列表及分页信息",
)
async def get_learning_records(
    ctx: SkillContext,
    start_day: str = "",
    end_day: str = "",
    student_keywords: str = "",
    course_title: str = "",
    department_ids: str = "",
    group_ids: str = "",
    class_ids: str = "",
    class_names: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询学习记录."""
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }
    if start_day:
        arguments["start_day"] = start_day
    if end_day:
        arguments["end_day"] = end_day
    if student_keywords:
        arguments["student_keywords"] = student_keywords
    if course_title:
        arguments["course_title"] = course_title
    if department_ids:
        arguments["department_ids"] = department_ids
    if group_ids:
        arguments["group_ids"] = group_ids
    if class_ids:
        arguments["class_ids"] = class_ids
    if class_names:
        arguments["class_names"] = class_names

    result = await ctx.call_role_tool(role="admin", operation="list_learning_records", arguments=arguments)

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_LEARNING_RECORDS_FAILED",
            "error_message": result.get("error_message") or "学习记录获取失败",
            "suggested_action": "请确认管理员已登录",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


__all__ = ["get_learning_records"]
