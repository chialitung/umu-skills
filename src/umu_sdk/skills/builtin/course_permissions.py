# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程访问权限与自动关闭管理 Skill.

本 Skill 以 Teacher 子 MCP 中的 canonical 原子工具实现课程访问权限管理。
Admin 账号可通过配置 Teacher MCP 或后续角色 fallback 机制复用此能力。
"""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="set_course_access_permission",
    description="设置课程的访问权限：企业内公开、指定账户可见或关闭",
    required_servers=["teacher"],
    return_description="设置结果",
)
async def set_course_access_permission(
    ctx: SkillContext,
    group_id: str,
    access_permission: int,
) -> dict[str, Any]:
    """设置课程访问权限.

    access_permission 取值：
    - 0：关闭（任何人不可见）
    - 2：企业内公开
    - 3：指定账户可见，设置后需继续调用 add_course_access_accounts 添加账户/班级
    """
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_set_course_access_permission",
        arguments={
            "group_id": group_id,
            "access_permission": access_permission,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SET_COURSE_ACCESS_PERMISSION_FAILED",
            "error_message": result.get("error_message") or "设置课程访问权限失败",
            "suggested_action": result.get("suggested_action") or "请确认 group_id 正确且已登录",
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
    name="search_course_access_accounts",
    description="搜索可授权访问课程的账户、班级、部门或分组，支持模糊匹配",
    required_servers=["teacher"],
    return_description="候选账户/班级/部门/分组列表",
)
async def search_course_access_accounts(
    ctx: SkillContext,
    group_id: str,
    keyword: str,
) -> dict[str, Any]:
    """搜索可授权访问课程的账户、班级、部门或分组."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_search_access_accounts",
        arguments={
            "group_id": group_id,
            "keyword": keyword,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SEARCH_ACCESS_ACCOUNTS_FAILED",
            "error_message": result.get("error_message") or "搜索可授权账户失败",
            "suggested_action": "请确认 group_id 正确并提供更精确的关键词",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "从结果中选择目标账户/班级/部门/分组，调用 add_course_access_accounts 添加权限",
        "next_action": "proceed",
    }


@skill(
    name="add_course_access_accounts",
    description="为课程设置指定账户、班级、部门或分组的访问权限",
    required_servers=["teacher"],
    return_description="添加结果",
)
async def add_course_access_accounts(
    ctx: SkillContext,
    group_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """为课程设置指定账户/班级/部门/分组可见.

    accounts 每个元素需包含：
    - account: 邮箱、班级名称、部门名称或分组名称
    - account_type: user / class / department / group
    - id: 对应 ID
    - class_id: 班级类型必填（可调用 search_course_access_accounts 获取）
    - department_id: 部门类型必填
    - user_group_id: 分组类型必填
    """
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_add_course_access_accounts",
        arguments={
            "group_id": group_id,
            "accounts": accounts,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "ADD_COURSE_ACCESS_ACCOUNTS_FAILED",
            "error_message": result.get("error_message") or "添加指定账户失败",
            "suggested_action": result.get("suggested_action") or "请确认账户信息正确，课程已设置为指定账户可见",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "可调用 search_course_access_accounts 或查看 UMU 后台确认权限已生效",
        "next_action": "proceed",
    }


@skill(
    name="remove_course_access_accounts",
    description="移除课程的指定账户、班级、部门或分组的访问权限",
    required_servers=["teacher"],
    return_description="移除结果",
)
async def remove_course_access_accounts(
    ctx: SkillContext,
    group_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """移除课程的指定账户/班级/部门/分组访问权限."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_remove_course_access_accounts",
        arguments={
            "group_id": group_id,
            "accounts": accounts,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "REMOVE_COURSE_ACCESS_ACCOUNTS_FAILED",
            "error_message": result.get("error_message") or "移除指定账户失败",
            "suggested_action": "请确认账户信息正确",
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
    name="cancel_course_access_permissions",
    description="取消课程的所有指定访问权限，清空指定账户/班级列表",
    required_servers=["teacher"],
    return_description="取消结果",
)
async def cancel_course_access_permissions(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """取消课程的所有指定访问权限.

    如需还原为企业内公开，请在调用后继续调用 set_course_access_permission(group_id, 2)。
    """
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_cancel_all_assigned_permissions",
        arguments={"group_id": group_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "CANCEL_COURSE_ACCESS_PERMISSIONS_FAILED",
            "error_message": result.get("error_message") or "取消指定权限失败",
            "suggested_action": "请确认 group_id 正确",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "如需还原为企业内公开，请调用 set_course_access_permission(group_id, 2)",
        "next_action": "proceed",
    }


@skill(
    name="get_course_access_permission",
    description="获取课程当前的访问权限设置",
    required_servers=["teacher"],
    return_description="当前权限设置",
)
async def get_course_access_permission(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """获取课程当前的访问权限设置."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_get_course_access_permission",
        arguments={"group_id": group_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_COURSE_ACCESS_PERMISSION_FAILED",
            "error_message": result.get("error_message") or "获取课程访问权限失败",
            "suggested_action": "请确认 group_id 正确",
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
    name="get_course_access_list",
    description="获取课程当前已授权的访问列表",
    required_servers=["teacher"],
    return_description="已授权列表",
)
async def get_course_access_list(
    ctx: SkillContext,
    group_id: str,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """获取课程当前已授权的账户、班级、部门或分组列表."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_get_course_access_list",
        arguments={
            "group_id": group_id,
            "page": page,
            "size": size,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_COURSE_ACCESS_LIST_FAILED",
            "error_message": result.get("error_message") or "获取课程访问列表失败",
            "suggested_action": "请确认 group_id 正确",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "可继续调用 remove_course_access_accounts 移除指定对象",
        "next_action": "proceed",
    }


@skill(
    name="set_course_auto_close",
    description="设置课程的定时自动关闭时间",
    required_servers=["teacher"],
    return_description="设置结果",
)
async def set_course_auto_close(
    ctx: SkillContext,
    group_id: str,
    close_time: str,
    custom_tips: str | None = None,
) -> dict[str, Any]:
    """设置课程定时自动关闭.

    close_time 支持格式如：2026-06-30 10:00、2026-06-30T10:00:00、2026年6月30日10点。
    也可通过 custom_tips 自定义提示文本。
    """
    arguments: dict[str, Any] = {"group_id": group_id, "close_time": close_time}
    if custom_tips is not None:
        arguments["custom_tips"] = custom_tips

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_set_course_auto_close",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SET_COURSE_AUTO_CLOSE_FAILED",
            "error_message": result.get("error_message") or "设置课程定时自动关闭失败",
            "suggested_action": result.get("suggested_action") or "请确认 group_id 正确且时间格式有效",
            "next_action": "needs_user_input" if result.get("error_code") == "INVALID_CLOSE_TIME" else "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "可通过 UMU 后台查看课程的自动关闭提示",
        "next_action": "proceed",
    }


@skill(
    name="cancel_course_auto_close",
    description="取消课程的定时自动关闭",
    required_servers=["teacher"],
    return_description="取消结果",
)
async def cancel_course_auto_close(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """取消课程定时自动关闭."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_cancel_course_auto_close",
        arguments={"group_id": group_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "CANCEL_COURSE_AUTO_CLOSE_FAILED",
            "error_message": result.get("error_message") or "取消课程定时自动关闭失败",
            "suggested_action": "请确认 group_id 正确",
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
    "set_course_access_permission",
    "get_course_access_permission",
    "get_course_access_list",
    "search_course_access_accounts",
    "add_course_access_accounts",
    "remove_course_access_accounts",
    "cancel_course_access_permissions",
    "set_course_auto_close",
    "cancel_course_auto_close",
]
