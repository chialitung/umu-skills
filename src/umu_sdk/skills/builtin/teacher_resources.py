"""Teacher 资源管理相关 Skill."""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill


@skill(
    name="upload_scorm_resource",
    description="上传 SCORM 包到讲师资源库",
    required_servers=["teacher"],
    return_description="上传后的资源信息（含 resource_id）",
)
async def upload_scorm_resource(
    ctx: SkillContext,
    file_path: str,
    title: str | None = None,
    auto_rename: bool = False,
) -> dict[str, Any]:
    """上传 SCORM 包."""
    ctx.logger.info("[upload_scorm_resource] 上传文件: %s", file_path)

    arguments: dict[str, Any] = {"file_path": file_path}
    if title:
        arguments["name"] = title
    if auto_rename:
        arguments["auto_rename"] = True

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_upload_scorm",
        arguments=arguments,
    )

    if not result["success"]:
        return _skill_error("UPLOAD_FAILED", result, "SCORM 上传失败")

    return _skill_ok(result.get("data"), "SCORM 上传成功，可继续创建课程小节")


@skill(
    name="upload_document_resource",
    description="上传文档到讲师文档库",
    required_servers=["teacher"],
    return_description="上传后的文档信息（含 resource_id）",
)
async def upload_document_resource(
    ctx: SkillContext,
    file_path: str,
    title: str | None = None,
    skip_existing: bool = False,
) -> dict[str, Any]:
    """上传文档资源."""
    ctx.logger.info("[upload_document_resource] 上传文件: %s", file_path)

    arguments: dict[str, Any] = {"file_path": file_path}
    if title:
        arguments["name"] = title
    if skip_existing:
        arguments["skip_existing"] = True

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_upload_document",
        arguments=arguments,
    )

    if not result["success"]:
        return _skill_error("UPLOAD_FAILED", result, "文档上传失败")

    return _skill_ok(result.get("data"), "文档上传成功")


@skill(
    name="upload_video_resource",
    description="上传音视频到讲师音视频库",
    required_servers=["teacher"],
    return_description="上传后的音视频信息（含 resource_id）",
)
async def upload_video_resource(
    ctx: SkillContext,
    file_path: str,
    title: str | None = None,
) -> dict[str, Any]:
    """上传音视频资源."""
    ctx.logger.info("[upload_video_resource] 上传文件: %s", file_path)

    arguments: dict[str, Any] = {"file_path": file_path}
    if title:
        arguments["name"] = title

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_upload_audio_video",
        arguments=arguments,
    )

    if not result["success"]:
        return _skill_error("UPLOAD_FAILED", result, "音视频上传失败")

    return _skill_ok(result.get("data"), "音视频上传成功")


@skill(
    name="list_scorm_resources",
    description="列出讲师 SCORM/音视频资源",
    required_servers=["teacher"],
    return_description="资源列表及分页信息",
)
async def list_scorm_resources(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
    search_keyword: str | None = None,
) -> dict[str, Any]:
    """列出 SCORM/音视频资源（media_type=videoweike，ext_type=scorm 可过滤 SCORM）."""
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
        "media_type": "videoweike",
        "ext_type": "scorm",
    }
    if search_keyword:
        arguments["search_keyword"] = search_keyword

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_list_resources",
        arguments=arguments,
    )

    if not result["success"]:
        return _skill_error("LIST_FAILED", result, "资源列表获取失败")

    return _skill_ok(result.get("data"))


@skill(
    name="list_document_resources",
    description="列出讲师文档资源",
    required_servers=["teacher"],
    return_description="文档列表及分页信息",
)
async def list_document_resources(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
    search_keyword: str | None = None,
) -> dict[str, Any]:
    """列出文档资源."""
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }
    if search_keyword:
        arguments["search_keyword"] = search_keyword

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_list_documents",
        arguments=arguments,
    )

    if not result["success"]:
        return _skill_error("LIST_FAILED", result, "文档列表获取失败")

    return _skill_ok(result.get("data"))


@skill(
    name="list_video_resources",
    description="列出讲师音视频资源",
    required_servers=["teacher"],
    return_description="音视频列表及分页信息",
)
async def list_video_resources(
    ctx: SkillContext,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
    search_keyword: str | None = None,
) -> dict[str, Any]:
    """列出音视频资源."""
    arguments: dict[str, Any] = {
        "page": page,
        "page_size": page_size,
        "fetch_all": fetch_all,
    }
    if search_keyword:
        arguments["search_keyword"] = search_keyword

    result = await ctx.call_tool(
        server="teacher",
        tool="tch_list_audio_videos",
        arguments=arguments,
    )

    if not result["success"]:
        return _skill_error("LIST_FAILED", result, "音视频列表获取失败")

    return _skill_ok(result.get("data"))


def _skill_ok(data: Any, suggested_action: str = "") -> dict[str, Any]:
    """构造 Skill 成功返回."""
    return {
        "success": True,
        "data": data,
        "error_code": "",
        "error_message": "",
        "suggested_action": suggested_action,
        "next_action": "proceed",
    }


def _skill_error(
    error_code: str,
    result: dict[str, Any],
    default_message: str,
    suggested_action: str = "",
) -> dict[str, Any]:
    """构造 Skill 失败返回."""
    return {
        "success": False,
        "data": result.get("data"),
        "error_code": result.get("error_code") or error_code,
        "error_message": result.get("error_message") or default_message,
        "suggested_action": suggested_action or "请检查参数或重试",
        "next_action": "retry",
    }


__all__ = [
    "upload_scorm_resource",
    "upload_document_resource",
    "upload_video_resource",
    "list_scorm_resources",
    "list_document_resources",
    "list_video_resources",
]
