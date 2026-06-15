# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Admin 讲师列表相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="get_instructors",
    description="查询企业讲师列表，支持认证状态、讲师标签、部门、分组、账号关键词等多条件组合筛选",
    required_servers=["admin"],
    return_description="讲师列表及分页信息",
)
async def get_instructors(
    ctx: SkillContext,
    certification_status: str = "",
    tag_ids: str = "",
    tag_names: str = "",
    department_ids: str = "",
    department_names: str = "",
    group_ids: str = "",
    group_names: str = "",
    account_keyword: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询企业讲师列表."""
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }

    for key, value in {
        "certification_status": certification_status,
        "tag_ids": tag_ids,
        "tag_names": tag_names,
        "department_ids": department_ids,
        "department_names": department_names,
        "group_ids": group_ids,
        "group_names": group_names,
        "account_keyword": account_keyword,
    }.items():
        if value:
            arguments[key] = value

    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_instructors",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_INSTRUCTORS_FAILED",
            "error_message": result.get("error_message") or "讲师列表获取失败",
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


__all__ = ["get_instructors"]
