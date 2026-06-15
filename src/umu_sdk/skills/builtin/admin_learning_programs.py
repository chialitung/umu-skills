# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Admin 学习项目管理相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_learning_programs",
    description="查询企业学习项目清单，支持按名称/标签/访问码/创建人/权限/知识库/分类/创建时间等筛选",
    required_servers=["admin"],
    return_description="学习项目列表及分页信息",
)
async def list_learning_programs(
    ctx: SkillContext,
    keywords: str = "",
    owner_keywords: str = "",
    owner_uids: str = "",
    access_permission: int | None = None,
    is_in_program_lib: int | None = None,
    category_id: str = "",
    start_day: str = "",
    end_day: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """列出企业学习项目清单.

    支持通过 keywords 同时模糊匹配学习项目名称、标签、访问码；
    支持通过 owner_keywords 自动解析创建人 UID，或直接传入 owner_uids；
    支持按项目权限、企业知识库状态、课程分类、创建时间范围筛选。
    """
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }
    if keywords:
        arguments["keywords"] = keywords
    if owner_keywords:
        arguments["owner_keywords"] = owner_keywords
    if owner_uids:
        arguments["owner_uids"] = owner_uids
    if access_permission is not None:
        arguments["access_permission"] = access_permission
    if is_in_program_lib is not None:
        arguments["is_in_program_lib"] = is_in_program_lib
    if category_id:
        arguments["category_id"] = category_id
    if start_day:
        arguments["start_day"] = start_day
    if end_day:
        arguments["end_day"] = end_day

    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_learning_programs",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_LEARNING_PROGRAMS_FAILED",
            "error_message": result.get("error_message") or "学习项目列表获取失败",
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


__all__ = ["list_learning_programs"]
