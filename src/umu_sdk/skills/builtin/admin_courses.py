# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Admin 课程查询与管理相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="list_courses",
    description="查询企业课程清单，支持按名称/标签/访问码/创建人/权限/审核状态等筛选",
    required_servers=["admin"],
    return_description="课程列表及分页信息",
)
async def list_courses(
    ctx: SkillContext,
    keywords: str = "",
    owner_keywords: str = "",
    owner_uids: str = "",
    access_permission: int | None = None,
    source: str = "",
    is_course_in_lib: int | None = None,
    audit_status: int | None = None,
    start_day: str = "",
    end_day: str = "",
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """列出企业课程清单.

    支持通过 keywords 同时模糊匹配课程名称、标签、访问码；
    支持通过 owner_keywords 自动解析创建人 UID，或直接传入 owner_uids；
    支持按课程权限、来源、知识库状态、审核状态、创建时间范围筛选。
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
    if source:
        arguments["source"] = source
    if is_course_in_lib is not None:
        arguments["is_course_in_lib"] = is_course_in_lib
    if audit_status is not None:
        arguments["audit_status"] = audit_status
    if start_day:
        arguments["start_day"] = start_day
    if end_day:
        arguments["end_day"] = end_day

    result = await ctx.call_tool(
        server="admin",
        tool="adm_list_courses",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_COURSES_FAILED",
            "error_message": result.get("error_message") or "课程列表获取失败",
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
    name="set_course_auto_close_admin",
    description="管理员设置课程的定时自动关闭时间",
    required_servers=["admin"],
    return_description="设置结果",
)
async def set_course_auto_close_admin(
    ctx: SkillContext,
    group_id: str,
    close_time: str,
    custom_tips: str | None = None,
) -> dict[str, Any]:
    """管理员设置课程定时自动关闭.

    close_time 支持格式如：2026-06-30 10:00、2026-06-30T10:00:00、2026年6月30日10点。
    也可通过 custom_tips 自定义提示文本。
    """
    arguments: dict[str, Any] = {"group_id": group_id, "close_time": close_time}
    if custom_tips is not None:
        arguments["custom_tips"] = custom_tips

    result = await ctx.call_tool(
        server="admin",
        tool="adm_set_course_auto_close",
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
    name="cancel_course_auto_close_admin",
    description="管理员取消课程的定时自动关闭",
    required_servers=["admin"],
    return_description="取消结果",
)
async def cancel_course_auto_close_admin(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """管理员取消课程定时自动关闭."""
    result = await ctx.call_tool(
        server="admin",
        tool="adm_cancel_course_auto_close",
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
    "list_courses",
    "set_course_auto_close_admin",
    "cancel_course_auto_close_admin",
]
