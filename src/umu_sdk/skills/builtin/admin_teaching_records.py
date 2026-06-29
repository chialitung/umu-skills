# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Admin 授课记录相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="get_teaching_records",
    description="查询讲师授课记录，支持按审核状态、讲师关键词、课程名称、访问码等多条件组合筛选",
    required_capabilities=['teaching_records'],
    return_description="授课记录列表及分页信息",
)
async def get_teaching_records(
    ctx: SkillContext,
    audit_status: str,
    teacher_umu_ids: str = "",
    teacher_keywords: str = "",
    course_keywords: str = "",
    access_code: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询讲师授课记录."""
    arguments: dict[str, Any] = {
        "audit_status": audit_status,
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }

    for key, value in {
        "teacher_umu_ids": teacher_umu_ids,
        "teacher_keywords": teacher_keywords,
        "course_keywords": course_keywords,
        "access_code": access_code,
    }.items():
        if value:
            arguments[key] = value

    result = await ctx.call_role_tool(role="admin", operation="list_teaching_records", arguments=arguments)

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_TEACHING_RECORDS_FAILED",
            "error_message": result.get("error_message") or "授课记录获取失败",
            "suggested_action": result.get("suggested_action") or "请确认管理员已登录",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": result.get("suggested_action", ""),
        "next_action": "proceed",
    }


__all__ = ["get_teaching_records"]
