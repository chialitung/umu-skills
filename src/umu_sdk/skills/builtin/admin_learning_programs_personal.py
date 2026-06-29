# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Admin 个人视角学习项目列表查询 Skill.

项目访问权限相关 Skill 已迁移至 program_permissions.py，以 Teacher 子 MCP
的 canonical 原子工具实现，供多角色复用。
"""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_admin_personal_learning_programs",
    description="查询管理员视角的学习项目清单，支持我拥有的/协同给我的/我报名的三个视角",
    required_capabilities=['program_management'],
    return_description="学习项目列表",
)
async def list_admin_personal_learning_programs(
    ctx: SkillContext,
    scope: str,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询管理员视角的学习项目清单."""
    arguments: dict[str, Any] = {
        "scope": scope,
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }
    if keywords:
        arguments["keywords"] = keywords

    result = await ctx.call_role_tool(role="admin", operation="list_personal_learning_programs", arguments=arguments)
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_LEARNING_PROGRAMS_FAILED",
            "error_message": result.get("error_message") or "获取学习项目列表失败",
            "suggested_action": result.get("suggested_action") or "请确认管理员已登录",
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


async def _list_programs_by_scope(
    ctx: SkillContext,
    scope: str,
    keywords: str,
    page: int,
    page_size: int,
    fetch_all: bool,
) -> dict[str, Any]:
    """按 scope 调用底层 Skill 的通用封装."""
    return await list_admin_personal_learning_programs(
        ctx, scope=scope, keywords=keywords, page=page, page_size=page_size, fetch_all=fetch_all
    )


@skill(
    name="list_owned_learning_programs_admin",
    description="查询管理员拥有的学习项目清单",
    required_capabilities=['program_management'],
    return_description="学习项目列表",
)
async def list_owned_learning_programs_admin(
    ctx: SkillContext,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询管理员拥有的学习项目清单."""
    return await _list_programs_by_scope(ctx, "owned", keywords, page, page_size, fetch_all)


@skill(
    name="list_cooperated_learning_programs_admin",
    description="查询协同给管理员的学习项目清单",
    required_capabilities=['program_management'],
    return_description="学习项目列表",
)
async def list_cooperated_learning_programs_admin(
    ctx: SkillContext,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询协同给管理员的学习项目清单."""
    return await _list_programs_by_scope(ctx, "cooperated", keywords, page, page_size, fetch_all)


@skill(
    name="list_enrolled_learning_programs_admin",
    description="查询管理员报名的学习项目清单",
    required_capabilities=['program_management'],
    return_description="学习项目列表",
)
async def list_enrolled_learning_programs_admin(
    ctx: SkillContext,
    keywords: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询管理员报名的学习项目清单."""
    return await _list_programs_by_scope(ctx, "enrolled", keywords, page, page_size, fetch_all)


@skill(
    name="delete_learning_program_admin",
    description="管理员删除学习项目",
    required_capabilities=['program_management'],
    return_description="删除结果",
)
async def delete_learning_program_admin(
    ctx: SkillContext,
    program_id: str,
) -> dict[str, Any]:
    """管理员删除学习项目."""
    result = await ctx.call_role_tool(role="admin", operation="delete_learning_program", arguments={"program_id": program_id})
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "DELETE_LEARNING_PROGRAM_FAILED",
            "error_message": result.get("error_message") or "删除学习项目失败",
            "suggested_action": result.get("suggested_action") or "请确认管理员已登录",
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


__all__ = [
    "list_admin_personal_learning_programs",
    "list_owned_learning_programs_admin",
    "list_cooperated_learning_programs_admin",
    "list_enrolled_learning_programs_admin",
    "delete_learning_program_admin",
]
