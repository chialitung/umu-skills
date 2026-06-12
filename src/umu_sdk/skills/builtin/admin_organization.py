"""Admin 组织架构相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_departments",
    description="列出企业部门",
    required_servers=["admin"],
    return_description="部门列表",
)
async def list_departments(
    ctx: SkillContext,
) -> dict[str, Any]:
    """列出部门."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_departments",
        arguments={},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_DEPARTMENTS_FAILED",
            "error_message": result.get("error_message") or "部门列表获取失败",
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


@skill(
    name="list_groups",
    description="列出企业分组",
    required_servers=["admin"],
    return_description="分组列表及分页信息",
)
async def list_groups(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """列出分组."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_groups",
        arguments={"page": page, "page_size": page_size},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_GROUPS_FAILED",
            "error_message": result.get("error_message") or "分组列表获取失败",
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


@skill(
    name="list_classes",
    description="列出企业班级",
    required_servers=["admin"],
    return_description="班级列表及分页信息",
)
async def list_classes(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """列出班级."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_classes",
        arguments={"page": page, "page_size": page_size, "fetch_all": fetch_all},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_CLASSES_FAILED",
            "error_message": result.get("error_message") or "班级列表获取失败",
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


__all__ = ["list_departments", "list_groups", "list_classes"]
