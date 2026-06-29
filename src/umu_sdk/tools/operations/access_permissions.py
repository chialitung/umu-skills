# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程/学习项目访问权限共享业务操作.

Admin 与 Teacher 在访问权限管理上调用的 UMU API 完全相同，本模块将公共逻辑
下沉为无状态业务函数，通过 @umu_operation 注册到对应角色的 MCP server。
"""

from __future__ import annotations

from typing import Any

from ...adapters.mcp.shared_access_permissions import (
    _add_obj_access_accounts,
    _cancel_all_assigned_permissions,
    _format_access_account,
    _get_obj_access_list,
    _get_obj_access_permission,
    _permission_text,
    _remove_obj_access_accounts,
    _search_access_permission_account,
    _set_obj_access_permission,
)
from ...core.client import UMUClient
from ...core.errors import UMUError
from ..decorators import umu_operation


# ---------------------------------------------------------------------------
# 课程访问权限
# ---------------------------------------------------------------------------
@umu_operation(
    name="set_course_access_permission",
    description="设置课程的整体访问权限",
    roles=["teacher", "admin"],
    parameter_docs={
        "group_id": "课程 ID",
        "access_permission": "课程访问权限：0=关闭（任何人不可见），2=企业内公开，3=指定账户/班级/部门/分组可见",
    },
)
async def set_course_access_permission(
    client: UMUClient,
    group_id: str,
    access_permission: int,
) -> dict[str, Any]:
    """设置课程的整体访问权限."""
    detail = _set_obj_access_permission(
        client, group_id, "group", access_permission, update_session_permission=True
    )
    return {
        "group_id": str(group_id),
        "access_permission": access_permission,
        "permission_text": _permission_text(access_permission),
        "update_session_permission": True,
        "detail": detail,
    }


@umu_operation(
    name="get_course_access_permission",
    description="获取课程当前的访问权限设置",
    roles=["teacher", "admin"],
    parameter_docs={"group_id": "课程 ID"},
)
async def get_course_access_permission(client: UMUClient, group_id: str) -> dict[str, Any]:
    """获取课程当前的访问权限设置."""
    selected_int, options, detail = _get_obj_access_permission(client, group_id, "group")
    return {
        "group_id": str(group_id),
        "access_permission": selected_int,
        "permission_text": _permission_text(selected_int) if selected_int >= 0 else "未知",
        "permission_options": options,
        "detail": detail,
    }


@umu_operation(
    name="get_course_access_list",
    description="获取课程当前已授权的访问列表",
    roles=["teacher", "admin"],
    parameter_docs={
        "group_id": "课程 ID",
        "page": "页码",
        "size": "每页数量",
    },
)
async def get_course_access_list(
    client: UMUClient,
    group_id: str,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """获取课程当前已授权的访问列表."""
    items, page_info = _get_obj_access_list(client, group_id, "group", page, size)
    return {
        "group_id": str(group_id),
        "page": page,
        "size": size,
        "page_info": page_info,
        "list": items,
        "total": page_info.get("list_total_num", len(items)),
    }


@umu_operation(
    name="search_access_accounts",
    description="搜索可授权访问课程的账户、班级、部门或分组",
    roles=["teacher", "admin"],
    parameter_docs={
        "group_id": "课程 ID",
        "keyword": "搜索关键词：账户邮箱、姓名、班级名称、部门名称或分组名称，支持模糊匹配",
    },
)
async def search_access_accounts(
    client: UMUClient,
    group_id: str,
    keyword: str,
) -> dict[str, Any]:
    """搜索可授权访问课程的账户、班级、部门或分组."""
    ok, accounts, err = _search_access_permission_account(client, group_id, "group", keyword)
    if not ok:
        raise UMUError(err or "搜索可授权账户失败", code="SEARCH_ACCESS_ACCOUNTS_FAILED")
    return {
        "group_id": str(group_id),
        "keyword": keyword,
        "accounts": [_format_access_account(acc) for acc in accounts],
        "total": len(accounts),
    }


@umu_operation(
    name="add_course_access_accounts",
    description="为课程设置指定账户/班级/部门/分组可见权限",
    roles=["teacher", "admin"],
    parameter_docs={
        "group_id": "课程 ID",
        "accounts": "要添加的账户/班级/部门/分组列表，每个元素需包含 account、account_type(user/class/department/group)、id",
    },
)
async def add_course_access_accounts(
    client: UMUClient,
    group_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """为课程设置指定账户/班级/部门/分组可见权限."""
    if not accounts:
        raise UMUError(
            "未提供要添加的账户/班级",
            code="NO_ACCOUNTS_PROVIDED",
        )
    detail = _add_obj_access_accounts(
        client, group_id, "group", accounts, update_session_permission=True
    )
    return {
        "group_id": str(group_id),
        "added": len(accounts),
        "accounts": accounts,
        "detail": detail,
    }


@umu_operation(
    name="remove_course_access_accounts",
    description="移除课程的指定账户/班级/部门/分组访问权限",
    roles=["teacher", "admin"],
    parameter_docs={
        "group_id": "课程 ID",
        "accounts": "要移除的账户/班级/部门/分组列表，每个元素需包含 account、account_type、id",
    },
)
async def remove_course_access_accounts(
    client: UMUClient,
    group_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """移除课程的指定账户/班级/部门/分组访问权限."""
    if not accounts:
        raise UMUError(
            "未提供要移除的账户/班级",
            code="NO_ACCOUNTS_PROVIDED",
        )
    detail = _remove_obj_access_accounts(
        client, group_id, "group", accounts, update_session_permission=True
    )
    return {
        "group_id": str(group_id),
        "removed": len(accounts),
        "accounts": accounts,
        "detail": detail,
    }


@umu_operation(
    name="cancel_all_assigned_permissions",
    description="取消课程的所有指定访问权限",
    roles=["teacher", "admin"],
    parameter_docs={"group_id": "课程 ID"},
)
async def cancel_all_assigned_permissions(client: UMUClient, group_id: str) -> dict[str, Any]:
    """取消课程的所有指定访问权限."""
    detail = _cancel_all_assigned_permissions(client, group_id, "group")
    return {
        "group_id": str(group_id),
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# 学习项目访问权限
# ---------------------------------------------------------------------------
@umu_operation(
    name="set_program_access_permission",
    description="设置学习项目的整体访问权限",
    roles=["teacher", "admin"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "access_permission": "权限：0=关闭，2=企业内公开，3=指定账户",
    },
)
async def set_program_access_permission(
    client: UMUClient,
    program_id: str,
    access_permission: int,
) -> dict[str, Any]:
    """设置学习项目的整体访问权限."""
    detail = _set_obj_access_permission(
        client, program_id, "program", access_permission, update_session_permission=True
    )
    return {
        "program_id": str(program_id),
        "access_permission": access_permission,
        "permission_text": _permission_text(access_permission),
        "detail": detail,
    }


@umu_operation(
    name="get_program_access_permission",
    description="获取学习项目当前的访问权限设置",
    roles=["teacher", "admin"],
    parameter_docs={"program_id": "学习项目 ID"},
)
async def get_program_access_permission(client: UMUClient, program_id: str) -> dict[str, Any]:
    """获取学习项目当前的访问权限设置."""
    selected_int, options, detail = _get_obj_access_permission(client, program_id, "program")
    return {
        "program_id": str(program_id),
        "access_permission": selected_int,
        "permission_text": _permission_text(selected_int) if selected_int >= 0 else "未知",
        "permission_options": options,
        "detail": detail,
    }


@umu_operation(
    name="get_program_access_list",
    description="获取学习项目当前已授权的访问列表",
    roles=["teacher", "admin"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "page": "页码",
        "size": "每页数量",
    },
)
async def get_program_access_list(
    client: UMUClient,
    program_id: str,
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """获取学习项目当前已授权的访问列表."""
    items, page_info = _get_obj_access_list(client, program_id, "program", page, size)
    return {
        "program_id": str(program_id),
        "page": page,
        "size": size,
        "page_info": page_info,
        "list": items,
        "total": page_info.get("list_total_num", len(items)),
    }


@umu_operation(
    name="search_program_access_accounts",
    description="搜索可授权访问学习项目的账户、班级、部门或分组",
    roles=["teacher", "admin"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "keyword": "搜索关键词：账户邮箱、姓名、班级名称、部门名称或分组名称，支持模糊匹配",
    },
)
async def search_program_access_accounts(
    client: UMUClient,
    program_id: str,
    keyword: str,
) -> dict[str, Any]:
    """搜索可授权访问学习项目的账户、班级、部门或分组."""
    ok, accounts, err = _search_access_permission_account(
        client, program_id, "program", keyword
    )
    if not ok:
        raise UMUError(err or "搜索可授权账户失败", code="SEARCH_PROGRAM_ACCESS_ACCOUNTS_FAILED")
    return {
        "program_id": str(program_id),
        "keyword": keyword,
        "accounts": [_format_access_account(acc) for acc in accounts],
        "total": len(accounts),
    }


@umu_operation(
    name="add_program_access_accounts",
    description="为学习项目添加指定账户/班级/部门/分组可见权限",
    roles=["teacher", "admin"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "accounts": "要添加的账户/班级/部门/分组列表",
    },
)
async def add_program_access_accounts(
    client: UMUClient,
    program_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """为学习项目添加指定账户/班级/部门/分组可见权限."""
    if not accounts:
        raise UMUError(
            "未提供要添加的账户/班级",
            code="NO_ACCOUNTS_PROVIDED",
        )
    detail = _add_obj_access_accounts(
        client, program_id, "program", accounts, update_session_permission=True
    )
    return {
        "program_id": str(program_id),
        "added": len(accounts),
        "accounts": accounts,
        "detail": detail,
    }


@umu_operation(
    name="remove_program_access_accounts",
    description="移除学习项目的指定账户/班级/部门/分组访问权限",
    roles=["teacher", "admin"],
    parameter_docs={
        "program_id": "学习项目 ID",
        "accounts": "要移除的账户/班级/部门/分组列表",
    },
)
async def remove_program_access_accounts(
    client: UMUClient,
    program_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    """移除学习项目的指定账户/班级/部门/分组访问权限."""
    if not accounts:
        raise UMUError(
            "未提供要移除的账户/班级",
            code="NO_ACCOUNTS_PROVIDED",
        )
    detail = _remove_obj_access_accounts(
        client, program_id, "program", accounts, update_session_permission=True
    )
    return {
        "program_id": str(program_id),
        "removed": len(accounts),
        "accounts": accounts,
        "detail": detail,
    }


@umu_operation(
    name="cancel_all_program_permissions",
    description="取消学习项目的所有指定访问权限",
    roles=["teacher", "admin"],
    parameter_docs={"program_id": "学习项目 ID"},
)
async def cancel_all_program_permissions(client: UMUClient, program_id: str) -> dict[str, Any]:
    """取消学习项目的所有指定访问权限."""
    detail = _cancel_all_assigned_permissions(client, program_id, "program")
    return {
        "program_id": str(program_id),
        "detail": detail,
    }
