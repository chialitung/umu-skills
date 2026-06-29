# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Admin 企业知识库课程审核相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_course_audit_records",
    description="查询企业知识库课程审核记录，支持待审核/已通过/已拒绝三种状态及多条件筛选",
    required_capabilities=['course_audit'],
    return_description="课程审核记录列表及分页信息",
)
async def list_course_audit_records(
    ctx: SkillContext,
    audit_status: int,
    course_keywords: str = "",
    access_code: str = "",
    owner_keywords: str = "",
    owner_uids: str = "",
    category_id: str = "",
    filter_last_passed: bool = False,
    sort_field: str = "submit_time",
    sort_order: str = "desc",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询企业知识库课程审核记录.

    支持通过 audit_status 筛选待审核/已通过/已拒绝课程；
    支持通过 course_keywords 模糊匹配课程名称、access_code 匹配访问码；
    支持通过 owner_keywords 自动解析拥有者 UID，或直接传入 owner_uids；
    支持通过 category_id 按课程分类筛选；
    支持过滤上次审核通过的课程并按提交时间排序。
    """
    arguments: dict[str, Any] = {
        "audit_status": audit_status,
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
        "sort_field": sort_field,
        "sort_order": sort_order,
        "filter_last_passed": filter_last_passed,
    }
    if course_keywords:
        arguments["course_keywords"] = course_keywords
    if access_code:
        arguments["access_code"] = access_code
    if owner_keywords:
        arguments["owner_keywords"] = owner_keywords
    if owner_uids:
        arguments["owner_uids"] = owner_uids
    if category_id:
        arguments["category_id"] = category_id

    result = await ctx.call_role_tool(role="admin", operation="list_course_audit_records", arguments=arguments)

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_COURSE_AUDIT_RECORDS_FAILED",
            "error_message": result.get("error_message") or "课程审核记录获取失败",
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
    name="audit_course",
    description="对企业知识库课程执行通过、拒绝或撤销提交操作",
    required_capabilities=['course_audit'],
    return_description="审核操作结果",
)
async def audit_course(
    ctx: SkillContext,
    group_ids: str,
    action: str,
    reason: str = "",
    add_to_blacklist: bool = False,
) -> dict[str, Any]:
    """对企业知识库课程执行审核操作.

    - 通过审核后，课程进入企业知识库，可被管理员转发/推荐，企业内其他学员可搜索学习。
    - 拒绝审核后，课程被设置为拒绝状态。
    - 撤销提交后，课程回到未提交状态，仍可编辑和分享，但管理员不会推荐，其他学员搜索不到。
    """
    arguments: dict[str, Any] = {
        "group_ids": group_ids,
        "action": action,
    }
    if reason:
        arguments["reason"] = reason
    if add_to_blacklist:
        arguments["add_to_blacklist"] = add_to_blacklist

    result = await ctx.call_role_tool(role="admin", operation="audit_course", arguments=arguments)

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "AUDIT_COURSE_FAILED",
            "error_message": result.get("error_message") or "课程审核操作失败",
            "suggested_action": "请确认管理员已登录且课程 ID 正确",
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
    name="list_course_categories",
    description="查询企业课程分类列表",
    required_capabilities=['course_audit'],
    return_description="课程分类列表",
)
async def list_course_categories(
    ctx: SkillContext,
    is_with_course_num: bool = False,
) -> dict[str, Any]:
    """查询企业课程分类列表."""
    result = await ctx.call_role_tool(role="admin", operation="list_course_categories", arguments={"is_with_course_num": is_with_course_num})

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_COURSE_CATEGORIES_FAILED",
            "error_message": result.get("error_message") or "课程分类获取失败",
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
    name="list_course_blacklist",
    description="查询课程提交黑名单",
    required_capabilities=['course_audit'],
    return_description="黑名单用户列表及分页信息",
)
async def list_course_blacklist(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 15,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询课程提交黑名单."""
    result = await ctx.call_role_tool(role="admin", operation="list_course_blacklist", arguments={
            "page": page,
            "page_size": page_size,
            "fetch_all": fetch_all,
        })

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_COURSE_BLACKLIST_FAILED",
            "error_message": result.get("error_message") or "黑名单获取失败",
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
    name="manage_course_blacklist",
    description="将用户加入或移出课程提交黑名单",
    required_capabilities=['course_audit'],
    return_description="黑名单操作结果",
)
async def manage_course_blacklist(
    ctx: SkillContext,
    umu_id: str,
    action: str,
) -> dict[str, Any]:
    """将用户加入或移出课程提交黑名单.

    被加入黑名单的账户，其提交的所有课程必须进入审核流程。
    """
    result = await ctx.call_role_tool(role="admin", operation="save_course_blacklist", arguments={
            "umu_id": umu_id,
            "action": action,
        })

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "MANAGE_COURSE_BLACKLIST_FAILED",
            "error_message": result.get("error_message") or "黑名单操作失败",
            "suggested_action": "请确认管理员已登录且 umu_id 正确",
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
    "list_course_audit_records",
    "audit_course",
    "list_course_categories",
    "list_course_blacklist",
    "manage_course_blacklist",
]
