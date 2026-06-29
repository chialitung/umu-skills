# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""学习项目访问权限管理 Skill.

本 Skill 以 Teacher 子 MCP 中的 canonical 原子工具实现学习项目访问权限管理。
Admin 账号可通过配置 Teacher MCP 或后续角色 fallback 机制复用此能力。
"""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="set_program_access_permission",
    description="设置学习项目的访问权限：企业内公开、指定账户可见或关闭",
    required_capabilities=['permission_management'],
    return_description="设置结果",
)
async def set_program_access_permission(
    ctx: SkillContext,
    program_id: str,
    access_permission: int,
) -> dict[str, Any]:
    """设置学习项目访问权限."""
    result = await ctx.call_capability_tool(capability="permission_management", operation="set_program_access_permission", arguments={"program_id": program_id, "access_permission": access_permission})
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SET_PROGRAM_ACCESS_PERMISSION_FAILED",
            "error_message": result.get("error_message") or "设置学习项目访问权限失败",
            "suggested_action": result.get("suggested_action") or "请确认 program_id 正确",
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


@skill(
    name="get_program_access_permission",
    description="获取学习项目当前的访问权限设置",
    required_capabilities=['permission_management'],
    return_description="当前权限设置",
)
async def get_program_access_permission(
    ctx: SkillContext,
    program_id: str,
) -> dict[str, Any]:
    """获取学习项目当前访问权限."""
    result = await ctx.call_capability_tool(capability="permission_management", operation="get_program_access_permission", arguments={"program_id": program_id})
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_PROGRAM_ACCESS_PERMISSION_FAILED",
            "error_message": result.get("error_message") or "获取学习项目访问权限失败",
            "suggested_action": "请确认 program_id 正确",
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
    name="get_program_access_list",
    description="获取学习项目当前已授权的访问列表",
    required_capabilities=['permission_management'],
    return_description="已授权列表",
)
async def get_program_access_list(
    ctx: SkillContext,
    program_id: str,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """获取学习项目已授权列表."""
    result = await ctx.call_capability_tool(capability="permission_management", operation="get_program_access_list", arguments={"program_id": program_id, "page": page, "size": size})
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_PROGRAM_ACCESS_LIST_FAILED",
            "error_message": result.get("error_message") or "获取学习项目访问列表失败",
            "suggested_action": "请确认 program_id 正确",
            "next_action": "retry",
        }
    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "可继续调用 remove_program_access_accounts 移除指定对象",
        "next_action": "proceed",
    }


@skill(
    name="search_program_access_accounts",
    description="搜索可授权访问学习项目的账户、班级、部门或分组，支持模糊匹配",
    required_capabilities=['permission_management'],
    return_description="候选账户/班级/部门/分组列表",
)
async def search_program_access_accounts(
    ctx: SkillContext,
    program_id: str,
    keyword: str,
) -> dict[str, Any]:
    """搜索可授权访问学习项目的对象."""
    result = await ctx.call_capability_tool(capability="permission_management", operation="search_program_access_accounts", arguments={"program_id": program_id, "keyword": keyword})
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SEARCH_PROGRAM_ACCESS_ACCOUNTS_FAILED",
            "error_message": result.get("error_message") or "搜索可授权对象失败",
            "suggested_action": "请确认 program_id 正确并提供更精确的关键词",
            "next_action": "needs_user_input",
        }
    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "从结果中选择目标对象，调用 add_program_access_accounts 添加权限",
        "next_action": "proceed",
    }


@skill(
    name="add_program_access_accounts",
    description="为学习项目设置指定账户、班级、部门或分组的访问权限",
    required_capabilities=['permission_management'],
    return_description="添加结果",
)
async def add_program_access_accounts(
    ctx: SkillContext,
    program_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """为学习项目添加指定对象."""
    result = await ctx.call_capability_tool(capability="permission_management", operation="add_program_access_accounts", arguments={"program_id": program_id, "accounts": accounts})
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "ADD_PROGRAM_ACCESS_ACCOUNTS_FAILED",
            "error_message": result.get("error_message") or "添加指定对象失败",
            "suggested_action": result.get("suggested_action") or "请确认对象信息正确",
            "next_action": "retry",
        }
    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "可调用 search_program_access_accounts 或查看 UMU 后台确认权限已生效",
        "next_action": "proceed",
    }


@skill(
    name="remove_program_access_accounts",
    description="移除学习项目的指定账户、班级、部门或分组的访问权限",
    required_capabilities=['permission_management'],
    return_description="移除结果",
)
async def remove_program_access_accounts(
    ctx: SkillContext,
    program_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """移除学习项目指定对象."""
    result = await ctx.call_capability_tool(capability="permission_management", operation="remove_program_access_accounts", arguments={"program_id": program_id, "accounts": accounts})
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "REMOVE_PROGRAM_ACCESS_ACCOUNTS_FAILED",
            "error_message": result.get("error_message") or "移除指定对象失败",
            "suggested_action": "请确认对象信息正确",
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
    name="cancel_program_access_permissions",
    description="取消学习项目的所有指定访问权限，清空指定账户/班级列表",
    required_capabilities=['permission_management'],
    return_description="取消结果",
)
async def cancel_program_access_permissions(
    ctx: SkillContext,
    program_id: str,
) -> dict[str, Any]:
    """取消学习项目所有指定权限."""
    result = await ctx.call_capability_tool(capability="permission_management", operation="cancel_all_program_permissions", arguments={"program_id": program_id})
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "CANCEL_PROGRAM_ACCESS_PERMISSIONS_FAILED",
            "error_message": result.get("error_message") or "取消指定权限失败",
            "suggested_action": "请确认 program_id 正确",
            "next_action": "retry",
        }
    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "如需还原为企业内公开，请调用 set_program_access_permission(program_id, 2)",
        "next_action": "proceed",
    }


__all__ = [
    "set_program_access_permission",
    "get_program_access_permission",
    "get_program_access_list",
    "search_program_access_accounts",
    "add_program_access_accounts",
    "remove_program_access_accounts",
    "cancel_program_access_permissions",
]
