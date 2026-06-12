"""Admin 账号管理相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_accounts",
    description="查询企业账号列表",
    required_servers=["admin"],
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

    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_accounts",
        arguments=arguments,
    )

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
    required_servers=["admin"],
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

    result = await ctx.call_tool(
        server="admin",
        tool="adm_disable_account",
        arguments=arguments,
    )

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
    required_servers=["admin"],
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

    result = await ctx.call_tool(
        server="admin",
        tool="adm_enable_account",
        arguments=arguments,
    )

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


__all__ = ["list_accounts", "disable_account", "enable_account"]
