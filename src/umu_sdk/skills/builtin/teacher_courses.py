"""Teacher 课程查询相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="get_course_categories",
    description="获取当前账号可用的课程分类树",
    required_servers=["teacher"],
    return_description="课程分类树",
)
async def get_course_categories(
    ctx: SkillContext,
) -> dict[str, Any]:
    """获取课程分类."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_get_categories",
        arguments={},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_CATEGORIES_FAILED",
            "error_message": result.get("error_message") or "课程分类获取失败",
            "suggested_action": "请确认讲师已登录",
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
    name="get_course_info",
    description="获取课程基本信息",
    required_servers=["teacher"],
    return_description="课程详情",
)
async def get_course_info(
    ctx: SkillContext,
    group_id: str,
    include_fulltext: bool = False,
) -> dict[str, Any]:
    """获取课程信息."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_get_course",
        arguments={
            "group_id": group_id,
            "include_fulltext": include_fulltext,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_COURSE_FAILED",
            "error_message": result.get("error_message") or "课程信息获取失败",
            "suggested_action": "请确认 group_id 正确",
            "next_action": "needs_user_input",
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
    name="list_my_courses",
    description="列出当前讲师创建的课程",
    required_servers=["teacher"],
    return_description="课程列表及分页信息",
)
async def list_my_courses(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
    order: str = "update_time",
) -> dict[str, Any]:
    """列出我创建的课程."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_list_created_courses",
        arguments={
            "page": page,
            "page_size": page_size,
            "order": order,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_COURSES_FAILED",
            "error_message": result.get("error_message") or "课程列表获取失败",
            "suggested_action": "请确认讲师已登录",
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
    name="submit_course_for_audit",
    description="将课程提交至企业知识库进行审核，管理员审核通过后可被推荐和搜索",
    required_servers=["teacher"],
    return_description="提交结果，包含 release_status 与 audit_status",
)
async def submit_course_for_audit(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """提交课程审核."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_submit_course_for_audit",
        arguments={
            "group_id": group_id,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SUBMIT_COURSE_FOR_AUDIT_FAILED",
            "error_message": result.get("error_message") or "课程审核提交失败",
            "suggested_action": result.get("suggested_action") or "请确认 group_id 正确且讲师已登录",
            "next_action": "needs_user_input",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": "提交成功，等待管理员审核",
        "next_action": "proceed",
    }


__all__ = [
    "get_course_categories",
    "get_course_info",
    "list_my_courses",
    "submit_course_for_audit",
]
