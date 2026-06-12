"""Teacher 小节管理相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="add_video_section",
    description="为课程添加视频小节",
    required_servers=["teacher"],
    return_description="小节创建结果（含 section_id）",
)
async def add_video_section(
    ctx: SkillContext,
    group_id: str,
    session_title: str,
    video_resource_id: str,
    cover_image_path: str | None = None,
    cover_resource_id: str | None = None,
) -> dict[str, Any]:
    """添加视频小节."""
    arguments: dict[str, Any] = {
        "group_id": group_id,
        "session_title": session_title,
        "video_resource_id": video_resource_id,
    }
    if cover_image_path:
        arguments["cover_image_path"] = cover_image_path
    if cover_resource_id:
        arguments["cover_resource_id"] = cover_resource_id

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_video_section",
        arguments=arguments,
    )
    return _handle_section_result(result, "视频小节")


@skill(
    name="add_article_section",
    description="为课程添加文章小节",
    required_servers=["teacher"],
    return_description="小节创建结果（含 section_id）",
)
async def add_article_section(
    ctx: SkillContext,
    group_id: str,
    session_title: str,
    article_content: str,
    cover_image_path: str | None = None,
    cover_resource_id: str | None = None,
) -> dict[str, Any]:
    """添加文章小节."""
    arguments: dict[str, Any] = {
        "group_id": group_id,
        "session_title": session_title,
        "article_content": article_content,
    }
    if cover_image_path:
        arguments["cover_image_path"] = cover_image_path
    if cover_resource_id:
        arguments["cover_resource_id"] = cover_resource_id

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_article_section",
        arguments=arguments,
    )
    return _handle_section_result(result, "文章小节")


@skill(
    name="add_infographic_section",
    description="为课程添加图文小节",
    required_servers=["teacher"],
    return_description="小节创建结果（含 section_id）",
)
async def add_infographic_section(
    ctx: SkillContext,
    group_id: str,
    session_title: str,
    content_blocks: list[dict[str, Any]],
    cover_image_path: str | None = None,
) -> dict[str, Any]:
    """添加图文小节."""
    arguments: dict[str, Any] = {
        "group_id": group_id,
        "session_title": session_title,
        "content_blocks": content_blocks,
    }
    if cover_image_path:
        arguments["cover_image_path"] = cover_image_path

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_infographic_section",
        arguments=arguments,
    )
    return _handle_section_result(result, "图文小节")


@skill(
    name="add_document_section",
    description="为课程添加文档小节",
    required_servers=["teacher"],
    return_description="小节创建结果（含 section_id）",
)
async def add_document_section(
    ctx: SkillContext,
    group_id: str,
    section_title: str,
    document_resource_id: str | None = None,
    document_file_path: str | None = None,
) -> dict[str, Any]:
    """添加文档小节."""
    arguments: dict[str, Any] = {
        "group_id": group_id,
        "section_title": section_title,
    }
    if document_resource_id:
        arguments["document_resource_id"] = document_resource_id
    if document_file_path:
        arguments["document_file_path"] = document_file_path

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_document_section",
        arguments=arguments,
    )
    return _handle_section_result(result, "文档小节")


@skill(
    name="add_survey_section",
    description="为课程添加问卷小节",
    required_servers=["teacher"],
    return_description="小节创建结果（含 section_id）",
)
async def add_survey_section(
    ctx: SkillContext,
    group_id: str,
    session_title: str,
    questions_json: str,
) -> dict[str, Any]:
    """添加问卷小节."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_survey_section",
        arguments={
            "group_id": group_id,
            "session_title": session_title,
            "questions_json": questions_json,
        },
    )
    return _handle_section_result(result, "问卷小节")


@skill(
    name="add_exam_section",
    description="为课程添加考试小节",
    required_servers=["teacher"],
    return_description="小节创建结果（含 section_id）",
)
async def add_exam_section(
    ctx: SkillContext,
    group_id: str,
    session_title: str,
    questions_json: str,
) -> dict[str, Any]:
    """添加考试小节."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_exam_section",
        arguments={
            "group_id": group_id,
            "session_title": session_title,
            "questions_json": questions_json,
        },
    )
    return _handle_section_result(result, "考试小节")


@skill(
    name="add_signin_section",
    description="为课程添加签到小节",
    required_servers=["teacher"],
    return_description="小节创建结果（含 section_id）",
)
async def add_signin_section(
    ctx: SkillContext,
    group_id: str,
    session_title: str,
    signin_info_json: str,
) -> dict[str, Any]:
    """添加签到小节."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_create_signin_section",
        arguments={
            "group_id": group_id,
            "session_title": session_title,
            "signin_info_json": signin_info_json,
        },
    )
    return _handle_section_result(result, "签到小节")


@skill(
    name="list_course_sections",
    description="列出课程的所有小节",
    required_servers=["teacher"],
    return_description="小节列表",
)
async def list_course_sections(
    ctx: SkillContext,
    group_id: str,
) -> dict[str, Any]:
    """列出课程小节."""
    result = await ctx.call_tool(
        server="teacher",
        tool="tch_list_sections",
        arguments={"group_id": group_id},
    )

    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "LIST_SECTIONS_FAILED",
            "error_message": result.get("error_message") or "小节列表获取失败",
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


def _handle_section_result(result: dict[str, Any], section_type: str) -> dict[str, Any]:
    """统一处理小节创建结果."""
    if not result["success"]:
        return {
            "success": False,
            "data": result.get("data"),
            "error_code": result.get("error_code") or "CREATE_SECTION_FAILED",
            "error_message": result.get("error_message") or f"{section_type}创建失败",
            "suggested_action": "请检查 group_id、资源 ID 和参数格式",
            "next_action": "retry",
        }

    return {
        "success": True,
        "data": result.get("data"),
        "error_code": "",
        "error_message": "",
        "suggested_action": f"{section_type}创建成功，可继续添加更多小节",
        "next_action": "proceed",
    }


__all__ = [
    "add_video_section",
    "add_article_section",
    "add_infographic_section",
    "add_document_section",
    "add_survey_section",
    "add_exam_section",
    "add_signin_section",
    "list_course_sections",
]
