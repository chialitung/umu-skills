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
    name="get_course_auto_close_admin",
    description=(
        "管理员查询课程的定时自动关闭时间（自动关闭、定时关闭、关闭时间、到期时间）。"
        "此操作只读取自动关闭配置，不会修改访问权限或报名开关。"
    ),
    required_servers=["admin", "teacher"],
    return_description="查询结果",
)
async def get_course_auto_close_admin(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """管理员查询课程自动关闭时间设置.

    通过 teacher 子 MCP 的原子工具查询，admin 凭据会自动 fallback 登录。

    常见表达：查看课程什么时候自动关闭、查询课程的关闭时间/到期时间。
    注意：这与访问权限、报名开关、课程小节无关。
    """
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_get_course_auto_close",
        arguments={"group_id": group_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_COURSE_AUTO_CLOSE_FAILED",
            "error_message": result.get("error_message") or "查询课程自动关闭时间失败",
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
    name="set_course_auto_close_admin",
    description=(
        "管理员设置课程的定时自动关闭时间（自动关闭、定时关闭、关闭时间、到期时间）。"
        "例如：把课程 X 的自动关闭时间设为 2028-05-21 12:30。"
        "此操作只修改自动关闭时间，不会修改访问权限、报名开关或课程小节。"
    ),
    required_servers=["admin", "teacher"],
    return_description="设置结果",
)
async def set_course_auto_close_admin(
    ctx: SkillContext,
    group_id: str,
    close_time: str,
    custom_tips: str | None = None,
) -> dict[str, Any]:
    """管理员设置课程定时自动关闭.

    先查询当前状态，再通过 teacher 子 MCP 设置关闭时间。
    close_time 支持格式如：2026-06-30 10:00、2026-06-30T10:00:00、2028年5月21日12点。
    也可通过 custom_tips 自定义提示文本。

    常见表达：设置课程自动关闭时间、定时关闭课程、把课程关闭时间设为某时、课程某时到期。
    注意：此操作只修改自动关闭时间，不修改谁能看（访问权限）、是否需要报名（报名开关）或课程内容。
    """
    previous = await ctx.call_tool(
        server="teacher",
        tool="tch_get_course_auto_close",
        arguments={"group_id": group_id},
    )
    if not previous["success"]:
        return {
            "success": False,
            "data": previous.get("data"),
            "error_code": previous.get("error_code") or "GET_COURSE_AUTO_CLOSE_FAILED",
            "error_message": previous.get("error_message") or "查询当前自动关闭设置失败",
            "suggested_action": "请确认 group_id 正确",
            "next_action": "retry",
        }

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
        "data": {
            "previous_state": previous.get("data"),
            "new_state": result.get("data"),
        },
        "error_code": "",
        "error_message": "",
        "suggested_action": "可通过 UMU 后台查看课程的自动关闭提示",
        "next_action": "proceed",
    }


@skill(
    name="cancel_course_auto_close_admin",
    description=(
        "管理员取消课程的定时自动关闭（关闭自动关闭、取消到期时间、移除自动关闭设置）。"
        "此操作只清除自动关闭时间，不会修改访问权限、报名开关或课程小节。"
    ),
    required_servers=["admin", "teacher"],
    return_description="取消结果",
)
async def cancel_course_auto_close_admin(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """管理员取消课程定时自动关闭.

    先查询当前状态，再通过 teacher 子 MCP 取消关闭时间并清空提示文案。

    常见表达：取消课程自动关闭、关闭课程的定时关闭、移除课程的到期时间。
    注意：此操作只清除自动关闭时间，不修改访问权限、报名开关或课程内容。
    """
    previous = await ctx.call_tool(
        server="teacher",
        tool="tch_get_course_auto_close",
        arguments={"group_id": group_id},
    )
    if not previous["success"]:
        return {
            "success": False,
            "data": previous.get("data"),
            "error_code": previous.get("error_code") or "GET_COURSE_AUTO_CLOSE_FAILED",
            "error_message": previous.get("error_message") or "查询当前自动关闭设置失败",
            "suggested_action": "请确认 group_id 正确",
            "next_action": "retry",
        }

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_cancel_course_auto_close",
        arguments={"group_id": group_id, "clear_tips": True},
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
        "data": {
            "previous_state": previous.get("data"),
            "new_state": result.get("data"),
        },
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }


__all__ = [
    "list_courses",
    "get_course_auto_close_admin",
    "set_course_auto_close_admin",
    "cancel_course_auto_close_admin",
]
