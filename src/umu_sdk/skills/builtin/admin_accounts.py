# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Admin 账号管理相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_accounts",
    description="查询企业账号列表",
    required_capabilities=['account_management'],
    return_description="账号列表及分页信息",
)
async def list_accounts(
    ctx: SkillContext,
    keywords: str = "",
    role_type: int | None = None,
    account_status: int | None = None,
    page: int = 1,
    page_size: int = 500,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """列出企业账号."""
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }
    if keywords:
        arguments["keywords"] = keywords
    if role_type is not None:
        arguments["role_type"] = role_type
    if account_status is not None:
        arguments["account_status"] = account_status

    result = await ctx.call_role_tool(role="admin", operation="list_accounts", arguments=arguments)

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_ACCOUNTS_FAILED",
            "error_message": result.get("error_message") or "账号列表获取失败",
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
    name="disable_account",
    description="禁用企业账号",
    required_capabilities=['account_management'],
    return_description="禁用结果",
)
async def disable_account(
    ctx: SkillContext,
    umu_id: str = "",
    email: str = "",
    effective_time: str = "",
) -> dict[str, Any]:
    """禁用账号."""
    if not umu_id and not email:
        return {
            "success": False,
            "data": None,
            "error_code": "MISSING_IDENTIFIER",
            "error_message": "必须提供 umu_id 或 email",
            "suggested_action": "调用 list_accounts 查询账号信息",
            "next_action": "needs_user_input",
        }

    arguments: dict[str, Any] = {}
    if umu_id:
        arguments["umu_id"] = umu_id
    if email:
        arguments["email"] = email
    if effective_time:
        arguments["effective_time"] = effective_time

    result = await ctx.call_role_tool(role="admin", operation="disable_account", arguments=arguments)

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "DISABLE_ACCOUNT_FAILED",
            "error_message": result.get("error_message") or "禁用账号失败",
            "suggested_action": result.get("suggested_action") or "请确认账号标识正确",
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
    name="enable_account",
    description="启用企业账号",
    required_capabilities=['account_management'],
    return_description="启用结果",
)
async def enable_account(
    ctx: SkillContext,
    umu_id: str = "",
    email: str = "",
) -> dict[str, Any]:
    """启用账号."""
    if not umu_id and not email:
        return {
            "success": False,
            "data": None,
            "error_code": "MISSING_IDENTIFIER",
            "error_message": "必须提供 umu_id 或 email",
            "suggested_action": "调用 list_accounts 查询账号信息",
            "next_action": "needs_user_input",
        }

    arguments: dict[str, Any] = {}
    if umu_id:
        arguments["umu_id"] = umu_id
    if email:
        arguments["email"] = email

    result = await ctx.call_role_tool(role="admin", operation="enable_account", arguments=arguments)

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "ENABLE_ACCOUNT_FAILED",
            "error_message": result.get("error_message") or "启用账号失败",
            "suggested_action": result.get("suggested_action") or "请确认账号标识正确",
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
    name="update_account",
    description="编辑企业账号信息",
    required_capabilities=['account_management'],
    return_description="更新结果，包含旧值、新值与 warnings",
)
async def update_account(
    ctx: SkillContext,
    umu_id: str = "",
    email: str = "",
    user_name: str | None = None,
    email_new: str | None = None,
    login_name: str | None = None,
    phone: str | None = None,
    number: str | None = None,
    role_type: int | None = None,
    platform_permission: int | None = None,
    department_ids: str | None = None,
    group_ids: str | None = None,
    manager_group_ids: str | None = None,
) -> dict[str, Any]:
    """编辑企业账号信息."""
    if not umu_id and not email:
        return {
            "success": False,
            "data": None,
            "error_code": "MISSING_IDENTIFIER",
            "error_message": "必须提供 umu_id 或 email 之一",
            "suggested_action": "调用 list_accounts 查询账号信息",
            "next_action": "needs_user_input",
        }

    arguments: dict[str, Any] = {}
    if umu_id:
        arguments["umu_id"] = umu_id
    if email:
        arguments["email"] = email
    if email_new is not None:
        arguments["new_email"] = email_new
    if user_name is not None:
        arguments["user_name"] = user_name
    if login_name is not None:
        arguments["login_name"] = login_name
    if phone is not None:
        arguments["phone"] = phone
    if number is not None:
        arguments["number"] = number
    if role_type is not None:
        arguments["role_type"] = role_type
    if platform_permission is not None:
        arguments["platform_permission"] = platform_permission
    if department_ids is not None:
        arguments["department_ids"] = department_ids
    if group_ids is not None:
        arguments["group_ids"] = group_ids
    if manager_group_ids is not None:
        arguments["manager_group_ids"] = manager_group_ids

    result = await ctx.call_role_tool(role="admin", operation="update_account", arguments=arguments)

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "UPDATE_ACCOUNT_FAILED",
            "error_message": result.get("error_message") or "账号更新失败",
            "suggested_action": result.get("suggested_action") or "请确认账号标识与权限正确",
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
    "list_accounts",
    "disable_account",
    "enable_account",
    "update_account",
]
