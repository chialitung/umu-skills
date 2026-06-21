# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Student 学习流程相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="resolve_course_identifier",
    description="解析课程标识（访问码/短域名/URL）为课程信息",
    required_servers=["student"],
    return_description="解析后的课程信息（group_id, s_key 等）",
)
async def resolve_course_identifier(
    ctx: SkillContext,
    course_identifier: str,
) -> dict[str, Any]:
    """解析课程标识."""
    result = await ctx.call_tool(
        server="student",
        tool="stu_resolve_course_url",
        arguments={"course_identifier": course_identifier},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "RESOLVE_FAILED",
            "error_message": result.get("error_message") or "课程标识解析失败",
            "suggested_action": "请确认课程访问码/短域名/URL 正确",
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
    name="list_my_courses_student",
    description="列出当前学员的课程",
    required_servers=["student"],
    return_description="课程列表及分页信息",
)
async def list_my_courses_student(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """列出我的课程."""
    result = await ctx.call_tool(
        server="student",
        tool="stu_get_my_courses",
        arguments={
            "page": page,
            "page_size": page_size,
            "fetch_all": fetch_all,
        },
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_COURSES_FAILED",
            "error_message": result.get("error_message") or "课程列表获取失败",
            "suggested_action": "请确认学员已登录",
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
    name="complete_browse_lesson",
    description="完成浏览类小节（视频/文章）",
    required_servers=["student"],
    return_description="小节完成结果",
)
async def complete_browse_lesson(
    ctx: SkillContext,
    element_id: str,
    duration_seconds: int = 0,
) -> dict[str, Any]:
    """完成浏览小节."""
    result = await ctx.call_tool(
        server="student",
        tool="stu_browse_lesson",
        arguments={"element_id": element_id, "duration_seconds": duration_seconds},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "BROWSE_FAILED",
            "error_message": result.get("error_message") or "小节浏览失败",
            "suggested_action": "请确认 element_id 正确",
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
    name="complete_scorm_section",
    description="完成 SCORM 1.2 小节，支持状态、得分、学习时长",
    required_servers=["student"],
    return_description="SCORM 小节完成结果",
)
async def complete_scorm_section(
    ctx: SkillContext,
    element_id: str,
    group_id: str = "",
    status: str = "passed",
    score: int | None = None,
    duration_seconds: int = 0,
    lesson_location: str = "",
    suspend_data_json: str = "",
    interactions_json: str = "",
    scorm_launch_url: str = "",
) -> dict[str, Any]:
    """完成 SCORM 1.2 格式小节。"""
    arguments: dict[str, Any] = {"element_id": element_id}
    if group_id:
        arguments["group_id"] = group_id
    if status != "passed":
        arguments["status"] = status
    if score is not None:
        arguments["score"] = score
    if duration_seconds > 0:
        arguments["duration_seconds"] = duration_seconds
    if lesson_location:
        arguments["lesson_location"] = lesson_location
    if suspend_data_json:
        arguments["suspend_data_json"] = suspend_data_json
    if interactions_json:
        arguments["interactions_json"] = interactions_json
    if scorm_launch_url:
        arguments["scorm_launch_url"] = scorm_launch_url

    result = await ctx.call_tool(
        server="student",
        tool="stu_complete_scorm_section",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "SCORM_COMPLETE_FAILED",
            "error_message": result.get("error_message") or "SCORM 小节完成失败",
            "suggested_action": "请确认 element_id 与 scorm_launch_url 正确",
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
    name="complete_checkin",
    description="完成签到小节",
    required_servers=["student"],
    return_description="签到结果",
)
async def complete_checkin(
    ctx: SkillContext,
    element_id: str,
) -> dict[str, Any]:
    """完成普通签到."""
    result = await ctx.call_tool(
        server="student",
        tool="stu_check_in",
        arguments={"element_id": element_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "CHECKIN_FAILED",
            "error_message": result.get("error_message") or "签到失败",
            "suggested_action": "请确认 element_id 对应签到小节",
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
    name="complete_rating_checkin",
    description="完成评分签到小节",
    required_servers=["student"],
    return_description="评分签到结果",
)
async def complete_rating_checkin(
    ctx: SkillContext,
    element_id: str,
    rating: int,
    comment: str = "",
) -> dict[str, Any]:
    """完成评分签到."""
    result = await ctx.call_tool(
        server="student",
        tool="stu_check_in_with_rating",
        arguments={"element_id": element_id, "rating": rating, "comment": comment},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "RATING_CHECKIN_FAILED",
            "error_message": result.get("error_message") or "评分签到失败",
            "suggested_action": "请确认 rating 在 1-5 之间",
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
    name="check_lesson_completion",
    description="查询小节完成状态",
    required_servers=["student"],
    return_description="小节完成状态",
)
async def check_lesson_completion(
    ctx: SkillContext,
    element_id: str,
    group_id: str = "",
) -> dict[str, Any]:
    """获取小节状态."""
    arguments: dict[str, Any] = {"element_id": element_id}
    if group_id:
        arguments["group_id"] = group_id

    result = await ctx.call_tool(
        server="student",
        tool="stu_get_lesson_status",
        arguments=arguments,
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "GET_STATUS_FAILED",
            "error_message": result.get("error_message") or "小节状态获取失败",
            "suggested_action": "请确认 element_id 正确",
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


__all__ = [
    "resolve_course_identifier",
    "list_my_courses_student",
    "complete_browse_lesson",
    "complete_scorm_section",
    "complete_checkin",
    "complete_rating_checkin",
    "check_lesson_completion",
]
