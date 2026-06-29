# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程与小节管理相关共享业务操作.

本模块提供课程创建、小节创建/列表/详情/删除/可见性切换等无状态业务函数，
可被 Teacher/Admin MCP server 复用。
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from ...adapters.mcp.course_builder import CourseBuilder
from ...adapters.mcp.cos_upload import (
    ScormUploader,
    validate_file_path,
)
from ...adapters.mcp.document_upload import (
    DocumentUploader,
    validate_document_path,
)
from ...adapters.mcp.image_upload import ImageUploader
from ...adapters.mcp.utils import fuzzy_filter_items
from ...core.client import UMUClient
from ..decorators import umu_operation

logger = logging.getLogger("umu.operations.section_management")

# 文档小节文件大小限制：100MB
_MAX_DOCUMENT_SIZE_BYTES = 100 * 1024 * 1024


def _upload_image_if_needed(
    client: UMUClient,
    image_path: str | None,
    media_type: str = "picweike",
) -> tuple[str | None, str]:
    """按需上传图片，返回 (resource_id, error_message_or_empty).

    上传失败时返回 (None, error_message)，但不会阻止主流程。
    """
    if not image_path:
        return None, ""

    try:
        uploader = ImageUploader(client, client.base_url)
        result = uploader.upload(image_path, media_type=media_type)
        logger.info("图片上传成功: resource_id=%s", result.resource_id)
        return result.resource_id, ""
    except Exception as e:
        msg = str(e)
        logger.warning("图片上传失败（非致命）: %s", msg)
        return None, msg


async def _upload_scorm_if_needed(
    client: UMUClient,
    scorm_file_path: str | None,
    scorm_resource_id: str | None,
    default_name: str,
) -> str | None:
    """按需上传 SCORM，返回 resource_id.

    如果提供了 scorm_resource_id，直接返回。
    如果提供了 scorm_file_path，上传后返回 resource_id。
    出错时抛出异常。
    """
    if scorm_resource_id:
        return scorm_resource_id

    if not scorm_file_path:
        return None

    validate_file_path(scorm_file_path)

    uploader = ScormUploader(client, client.base_url)
    result = await uploader.run(scorm_file_path, name=default_name)

    if not result.resource_id:
        raise RuntimeError("SCORM 上传成功但返回的 resource_id 为空")

    logger.info("SCORM 上传成功: resource_id=%s", result.resource_id)
    return result.resource_id


async def _upload_document_if_needed(
    client: UMUClient,
    document_file_path: str | None,
    document_resource_id: str | None,
    default_name: str,
) -> str | None:
    """按需上传文档，返回 resource_id.

    如果提供了 document_resource_id，直接返回。
    如果提供了 document_file_path，上传后返回 resource_id。
    出错时抛出异常。文件大小限制 100MB。
    """
    if document_resource_id:
        return document_resource_id

    if not document_file_path:
        return None

    validate_document_path(document_file_path)

    file_size = os.path.getsize(document_file_path)
    if file_size > _MAX_DOCUMENT_SIZE_BYTES:
        raise ValueError(
            f"文档文件大小 {file_size / (1024 * 1024):.2f}MB 超过限制 100MB"
        )

    uploader = DocumentUploader(client, client.base_url)
    result = await uploader.run(document_file_path, name=default_name)

    if not result.resource_id:
        raise RuntimeError("文档上传成功但返回的 resource_id 为空")

    logger.info("文档上传成功: resource_id=%s", result.resource_id)
    return result.resource_id


@umu_operation(
    name="create_course",
    description="创建不含小节的空课程",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "title": "课程标题，2-100 字符",
        "course_type": "课程形式：1=在线课程，2=面授课程，3=混合课程",
        "category_ids": "课程分类 ID 列表，可选。与 category_names 二选一",
        "category_names": '课程分类名称列表，支持完整路径如 ["课程系列 > 新能力系列 > 客户思维"]',
        "tags": "课程标签文本列表，可选",
        "cover_image_path": "本地封面图片路径（jpg/png），可选",
        "bg_image_path": "本地背景图片路径（jpg/png），可选",
        "desc_plain": "纯文本课程介绍",
        "desc_richtext": "富文本课程介绍（HTML 格式），可选",
        "start_date": "课程起始日期，格式 YYYY-MM-DD，可选",
        "start_time_str": "课程起始时间，格式 HH:MM，可选",
        "end_time_str": "课程结束时间，格式 HH:MM，可选",
    },
)
async def create_course(
    client: UMUClient,
    title: str,
    course_type: int = 1,
    category_ids: list[str] | None = None,
    category_names: list[str] | None = None,
    tags: list[str] | None = None,
    cover_image_path: str | None = None,
    bg_image_path: str | None = None,
    desc_plain: str = "",
    desc_richtext: str = "",
    start_date: str = "",
    start_time_str: str = "",
    end_time_str: str = "",
) -> dict[str, Any]:
    """创建不含小节的空课程.

    Args:
        client: 已登录的 UMUClient 实例。
        title: 课程标题。
        course_type: 课程形式。
        category_ids: 分类 ID 列表。
        category_names: 分类名称列表。
        tags: 标签列表。
        cover_image_path: 封面图本地路径。
        bg_image_path: 背景图本地路径。
        desc_plain: 纯文本介绍。
        desc_richtext: 富文本介绍。
        start_date: 起始日期。
        start_time_str: 起始时间。
        end_time_str: 结束时间。

    Returns:
        包含 group_id 等课程信息的字典。
    """
    if category_ids is None:
        category_ids = []
    if tags is None:
        tags = []

    builder = CourseBuilder(client)

    cover_url = ""
    if cover_image_path:
        try:
            img_uploader = ImageUploader(client, client.base_url)
            cover_result = img_uploader.upload(cover_image_path, media_type="picweike")
            cover_url = cover_result.file_url
            logger.info("封面上传成功: %s", cover_url)
        except Exception as e:
            logger.warning("封面上传失败（非致命）: %s", e)

    bg_url = ""
    if bg_image_path:
        try:
            img_uploader = ImageUploader(client, client.base_url)
            bg_result = img_uploader.upload(bg_image_path, media_type="picweike")
            bg_url = bg_result.file_url
            logger.info("背景图上传成功: %s", bg_url)
        except Exception as e:
            logger.warning("背景图上传失败（非致命）: %s", e)

    course = builder.create_course(
        title=title,
        desc_plain=desc_plain,
        desc_richtext=desc_richtext,
        cover_url=cover_url,
        bg_url=bg_url,
        category_ids=category_ids,
        category_names=category_names,
        tags=tags,
        start_date=start_date,
        start_time=start_time_str,
        end_time=end_time_str,
    )

    group_id = course["group_id"]
    course["course_url"] = f"{client.base_url}/course/?groupId={group_id}"
    return course


@umu_operation(
    name="create_scorm_section",
    description="在课程中创建 SCORM 类型小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "section_title": "小节标题",
        "scorm_resource_id": "已有 SCORM 资源 ID。与 scorm_file_path 二选一",
        "scorm_file_path": "本地 SCORM zip 文件路径。与 scorm_resource_id 二选一",
        "section_cover_path": "小节封面图片路径（jpg/png），可选",
        "duration_minutes": "预计学习时长（分钟），可选",
        "is_required": "是否为必修小节",
    },
)
async def create_scorm_section(
    client: UMUClient,
    group_id: str,
    section_title: str,
    scorm_resource_id: str | None = None,
    scorm_file_path: str | None = None,
    section_cover_path: str | None = None,
    duration_minutes: int = 0,
    is_required: bool = True,
) -> dict[str, Any]:
    """在课程中创建 SCORM 类型小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        section_title: 小节标题。
        scorm_resource_id: 已有 SCORM 资源 ID。
        scorm_file_path: 本地 SCORM zip 文件路径。
        section_cover_path: 小节封面图片路径。
        duration_minutes: 预计学习时长。
        is_required: 是否必修。

    Returns:
        包含 session_id、group_id、title、resource_id 等信息的字典。
    """
    if not scorm_resource_id and not scorm_file_path:
        raise ValueError(
            "必须提供 scorm_resource_id（已有 SCORM）或 scorm_file_path（上传新 SCORM）之一"
        )

    builder = CourseBuilder(client)

    actual_resource_id = await _upload_scorm_if_needed(
        client, scorm_file_path, scorm_resource_id, section_title
    )
    actual_resource_id = actual_resource_id or ""

    cover_resource_id, _ = _upload_image_if_needed(
        client, section_cover_path, media_type="picweike"
    )

    session = builder.create_scorm_session(
        group_id=group_id,
        session_title=section_title,
        resource_id=actual_resource_id,
        cover_resource_id=cover_resource_id,
        duration_minutes=duration_minutes,
        is_required=is_required,
    )

    return {
        "session_id": session["session_id"],
        "group_id": group_id,
        "title": section_title,
        "resource_id": actual_resource_id,
        "cover_resource_id": cover_resource_id or None,
        "is_required": is_required,
    }


@umu_operation(
    name="create_video_section",
    description="在课程中创建视频类型小节并绑定视频资源",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "session_title": "视频小节标题",
        "video_resource_id": "视频资源 ID",
        "cover_image_path": "封面图本地路径（jpg/png）",
        "cover_resource_id": "已上传的封面图资源 ID",
        "desc_plain": "纯文本视频说明",
        "desc_richtext": "富文本视频说明（HTML）",
        "is_required": "是否必修",
        "allow_drag_track": "是否允许拖动播放条",
        "allow_adjust_speed": "是否允许倍速播放",
        "min_duration_seconds": "最小学习时长（秒）",
        "max_duration_seconds": "学习时长统计上限（秒）",
        "desc_first_remind": "首次进入小节页是否弹出视频说明",
        "tags": "视频标签列表",
        "sort_order": "排序序号",
    },
)
async def create_video_section(
    client: UMUClient,
    group_id: str,
    session_title: str,
    video_resource_id: str,
    cover_image_path: str | None = None,
    cover_resource_id: str | None = None,
    desc_plain: str | None = None,
    desc_richtext: str | None = None,
    is_required: bool = True,
    allow_drag_track: bool = True,
    allow_adjust_speed: bool = True,
    min_duration_seconds: int = 0,
    max_duration_seconds: int = 0,
    desc_first_remind: bool = False,
    tags: list[str] | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    """在课程中创建视频类型小节并绑定视频资源.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        session_title: 小节标题。
        video_resource_id: 视频资源 ID。
        cover_image_path: 封面图本地路径。
        cover_resource_id: 已上传的封面图资源 ID。
        desc_plain: 纯文本视频说明。
        desc_richtext: 富文本视频说明。
        is_required: 是否必修。
        allow_drag_track: 是否允许拖动播放条。
        allow_adjust_speed: 是否允许倍速播放。
        min_duration_seconds: 最小学习时长。
        max_duration_seconds: 学习时长统计上限。
        desc_first_remind: 是否首次进入弹出视频说明。
        tags: 视频标签列表。
        sort_order: 排序序号。

    Returns:
        包含 session_id 和绑定资源信息的字典。
    """
    builder = CourseBuilder(client)
    return builder.create_video_section(
        group_id=group_id,
        session_title=session_title,
        video_resource_id=video_resource_id,
        cover_image_path=cover_image_path or "",
        cover_resource_id=cover_resource_id or "",
        desc_plain=desc_plain or "",
        desc_richtext=desc_richtext or "",
        is_required=is_required,
        allow_drag_track=allow_drag_track,
        allow_adjust_speed=allow_adjust_speed,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
        desc_first_remind=desc_first_remind,
        tags=tags,
        sort_order=sort_order,
    )


@umu_operation(
    name="create_article_section",
    description="在课程中创建文章类型小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "session_title": "文章小节标题",
        "article_content": "文章 HTML 内容",
        "cover_image_path": "封面图本地路径",
        "cover_resource_id": "已上传的封面图资源 ID",
        "is_required": "是否必修",
        "type_name": "小节类型标签",
        "min_duration_seconds": "最小学习时长（秒）",
        "max_duration_seconds": "学习时长统计上限（秒）",
        "show_course_creator_info": "是否展示课程创建者信息",
        "show_article_reading_speed": "是否展示文章字数和阅读速度",
        "is_comment_time_visible": "是否允许学员查看发言提交时间",
        "enable_comment": "是否开启发言区",
        "tags": "文章标签列表",
        "sort_order": "排序序号",
    },
)
async def create_article_section(
    client: UMUClient,
    group_id: str,
    session_title: str,
    article_content: str,
    cover_image_path: str | None = None,
    cover_resource_id: str | None = None,
    is_required: bool = True,
    type_name: str = "",
    min_duration_seconds: int = 0,
    max_duration_seconds: int = 0,
    show_course_creator_info: bool = True,
    show_article_reading_speed: bool = True,
    is_comment_time_visible: bool = True,
    enable_comment: bool = True,
    tags: list[str] | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    """在课程中创建文章类型小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        session_title: 小节标题。
        article_content: 文章 HTML 内容。
        cover_image_path: 封面图本地路径。
        cover_resource_id: 已上传的封面图资源 ID。
        is_required: 是否必修。
        type_name: 小节类型标签。
        min_duration_seconds: 最小学习时长。
        max_duration_seconds: 学习时长统计上限。
        show_course_creator_info: 是否展示课程创建者信息。
        show_article_reading_speed: 是否展示文章字数和阅读速度。
        is_comment_time_visible: 是否允许学员查看发言提交时间。
        enable_comment: 是否开启发言区。
        tags: 文章标签列表。
        sort_order: 排序序号。

    Returns:
        包含 session_id 和绑定资源信息的字典。
    """
    builder = CourseBuilder(client)
    return builder.create_article_section(
        group_id=group_id,
        session_title=session_title,
        article_content=article_content,
        cover_image_path=cover_image_path or "",
        cover_resource_id=cover_resource_id or "",
        is_required=is_required,
        type_name=type_name,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
        show_course_creator_info=show_course_creator_info,
        show_article_reading_speed=show_article_reading_speed,
        is_comment_time_visible=is_comment_time_visible,
        enable_comment=enable_comment,
        tags=tags,
        sort_order=sort_order,
    )


@umu_operation(
    name="create_infographic_section",
    description="在课程中创建图文类型小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "session_title": "图文小节标题",
        "content_blocks": '图文内容块列表，每项为 {"type": "image"|"text", "content": "..."}',
        "cover_image_path": "封面图本地路径",
        "cover_resource_id": "已上传的封面图资源 ID",
        "is_required": "是否必修",
        "type_name": "小节类型标签",
        "min_duration_seconds": "最小学习时长（秒）",
        "max_duration_seconds": "学习时长统计上限（秒）",
        "show_course_creator_info": "是否展示课程创建者信息",
        "show_article_reading_speed": "是否展示阅读速度",
        "is_comment_time_visible": "是否允许学员查看发言提交时间",
        "enable_comment": "是否开启发言区",
        "tags": "标签列表",
        "sort_order": "排序序号",
    },
)
async def create_infographic_section(
    client: UMUClient,
    group_id: str,
    session_title: str,
    content_blocks: list[dict],
    cover_image_path: str | None = None,
    cover_resource_id: str | None = None,
    is_required: bool = True,
    type_name: str = "",
    min_duration_seconds: int = 0,
    max_duration_seconds: int = 0,
    show_course_creator_info: bool = True,
    show_article_reading_speed: bool = True,
    is_comment_time_visible: bool = True,
    enable_comment: bool = True,
    tags: list[str] | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    """在课程中创建图文类型小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        session_title: 小节标题。
        content_blocks: 图文内容块列表。
        cover_image_path: 封面图本地路径。
        cover_resource_id: 已上传的封面图资源 ID。
        is_required: 是否必修。
        type_name: 小节类型标签。
        min_duration_seconds: 最小学习时长。
        max_duration_seconds: 学习时长统计上限。
        show_course_creator_info: 是否展示课程创建者信息。
        show_article_reading_speed: 是否展示阅读速度。
        is_comment_time_visible: 是否允许学员查看发言提交时间。
        enable_comment: 是否开启发言区。
        tags: 标签列表。
        sort_order: 排序序号。

    Returns:
        包含 session_id 和绑定资源信息的字典。
    """
    builder = CourseBuilder(client)
    return builder.create_infographic_section(
        group_id=group_id,
        session_title=session_title,
        content_blocks=content_blocks,
        cover_image_path=cover_image_path or "",
        cover_resource_id=cover_resource_id or "",
        is_required=is_required,
        type_name=type_name,
        min_duration_seconds=min_duration_seconds,
        max_duration_seconds=max_duration_seconds,
        show_course_creator_info=show_course_creator_info,
        show_article_reading_speed=show_article_reading_speed,
        is_comment_time_visible=is_comment_time_visible,
        enable_comment=enable_comment,
        tags=tags,
        sort_order=sort_order,
    )


@umu_operation(
    name="create_document_section",
    description="在课程中创建文档类型小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "section_title": "小节标题",
        "document_resource_id": "已有文档资源 ID。与 document_file_path 二选一",
        "document_file_path": "本地文档文件路径。与 document_resource_id 二选一",
        "desc_plain": "纯文本文档说明",
        "desc_richtext": "富文本文档说明（HTML）",
        "is_required": "是否为必修小节",
        "allow_download": "是否允许学员下载文档",
        "min_duration_minutes": "最小学习时长（分钟）",
        "finish_condition": '完成条件: "open"=打开即完成, "last_page"=学完最后一页',
        "show_creator_info": "是否展示课程创建者信息",
        "enable_comment": "是否开启发言区",
        "show_comment_time": "是否允许查看发言提交时间",
        "tags": "标签文本列表",
        "section_cover_path": "小节封面图片路径",
    },
)
async def create_document_section(
    client: UMUClient,
    group_id: str,
    section_title: str,
    document_resource_id: str | None = None,
    document_file_path: str | None = None,
    desc_plain: str = "",
    desc_richtext: str = "",
    is_required: bool = True,
    allow_download: bool = True,
    min_duration_minutes: int = 0,
    finish_condition: str = "open",
    show_creator_info: bool = True,
    enable_comment: bool = True,
    show_comment_time: bool = True,
    tags: list[str] | None = None,
    section_cover_path: str | None = None,
) -> dict[str, Any]:
    """在课程中创建文档类型小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        section_title: 小节标题。
        document_resource_id: 已有文档资源 ID。
        document_file_path: 本地文档文件路径。
        desc_plain: 纯文本说明。
        desc_richtext: 富文本说明。
        is_required: 是否必修。
        allow_download: 是否允许下载。
        min_duration_minutes: 最小学习时长（分钟）。
        finish_condition: 完成条件。
        show_creator_info: 是否展示课程创建者信息。
        enable_comment: 是否开启发言区。
        show_comment_time: 是否显示发言提交时间。
        tags: 标签列表。
        section_cover_path: 小节封面路径。

    Returns:
        包含 session_id 等信息的字典。
    """
    if not document_resource_id and not document_file_path:
        raise ValueError(
            "必须提供 document_resource_id（已有文档）或 document_file_path（上传新文档）之一"
        )

    if desc_plain and desc_richtext:
        raise ValueError("desc_plain 和 desc_richtext 不能同时提供")

    if finish_condition not in ("open", "last_page"):
        raise ValueError(
            f"finish_condition 必须是 'open' 或 'last_page'，收到: {finish_condition}"
        )

    builder = CourseBuilder(client)

    actual_resource_id = await _upload_document_if_needed(
        client, document_file_path, document_resource_id, section_title
    )
    actual_resource_id = actual_resource_id or ""

    cover_resource_id, _ = _upload_image_if_needed(
        client, section_cover_path, media_type="picweike"
    )

    session = builder.create_document_session(
        group_id=group_id,
        session_title=section_title,
        resource_id=actual_resource_id,
        desc_plain=desc_plain,
        desc_richtext=desc_richtext,
        is_required=is_required,
        allow_download=allow_download,
        min_duration_seconds=min_duration_minutes * 60,
        finish_condition=finish_condition,
        show_creator_info=show_creator_info,
        enable_comment=enable_comment,
        show_comment_time=show_comment_time,
        tags=tags,
        cover_resource_id=cover_resource_id,
    )

    return {
        "session_id": session["session_id"],
        "group_id": group_id,
        "title": section_title,
        "resource_id": actual_resource_id,
        "cover_resource_id": cover_resource_id or None,
        "is_required": is_required,
        "allow_download": allow_download,
        "min_duration_minutes": min_duration_minutes,
        "finish_condition": finish_condition,
        "multimedia_type": session.get("multimedia_type", 0),
        "multimedia_id": session.get("multimedia_id", 0),
    }


@umu_operation(
    name="create_survey_section",
    description="在课程中创建问卷类型小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "session_title": "问卷小节标题",
        "questions_json": "题目列表的 JSON 字符串",
        "is_required": "是否必修",
        "jump_button": "提交成功后是否显示跳转按钮",
        "jump_url": "跳转按钮目标 URL",
        "jump_button_title": "跳转按钮文本",
        "show_user_result": "提交后是否展示问卷结果",
        "is_show_participate_on_screen": "大屏幕是否展示参与人数",
        "share_status": "问卷访问权限: 1=课程内公开, 2=企业内公开, 3=仅自己",
        "submit_permission": "提交权限: 3=不允许匿名, 4=允许匿名提交",
        "allow_modify": "是否允许提交后修改问卷",
        "submit_limit": '提交次数限制: "1"=最多1次, "n"=允许多次',
        "result_prompt": "提交成功提示语",
        "accept_submission_time": "开始提交时间（Unix时间戳）",
        "refuse_submission_time": "结束提交时间（Unix时间戳）",
        "random_option": "选项是否随机展示",
        "type_name": "小节类型标签",
        "tags": "标签文本列表",
        "sort_order": "排序序号",
    },
)
async def create_survey_section(
    client: UMUClient,
    group_id: str,
    session_title: str,
    questions_json: str,
    is_required: bool = True,
    jump_button: bool = False,
    jump_url: str = "",
    jump_button_title: str = "",
    show_user_result: bool = False,
    is_show_participate_on_screen: bool = True,
    share_status: int = 1,
    submit_permission: int = 4,
    allow_modify: bool = False,
    submit_limit: str = "1",
    result_prompt: str = "感谢您的参与！",
    accept_submission_time: int = 0,
    refuse_submission_time: int = 0,
    random_option: bool = False,
    type_name: str = "",
    tags: list[str] | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    """在课程中创建问卷类型小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        session_title: 小节标题。
        questions_json: 题目列表 JSON 字符串。
        is_required: 是否必修。
        jump_button: 是否显示跳转按钮。
        jump_url: 跳转 URL。
        jump_button_title: 跳转按钮文本。
        show_user_result: 是否展示结果。
        is_show_participate_on_screen: 大屏幕是否展示参与人数。
        share_status: 访问权限。
        submit_permission: 提交权限。
        allow_modify: 是否允许修改。
        submit_limit: 提交次数限制。
        result_prompt: 提交成功提示。
        accept_submission_time: 开始提交时间。
        refuse_submission_time: 结束提交时间。
        random_option: 是否随机选项。
        type_name: 小节类型标签。
        tags: 标签列表。
        sort_order: 排序序号。

    Returns:
        包含 session_id 等信息的字典。
    """
    questions = json.loads(questions_json)
    if not isinstance(questions, list):
        raise ValueError("questions_json 必须解析为列表")

    builder = CourseBuilder(client)
    return builder.create_survey_section(
        group_id=group_id,
        session_title=session_title,
        questions=questions,
        is_required=is_required,
        jump_button=jump_button,
        jump_url=jump_url,
        jump_button_title=jump_button_title,
        show_user_result=show_user_result,
        is_show_participate_on_screen=is_show_participate_on_screen,
        share_status=share_status,
        submit_permission=submit_permission,
        allow_modify=allow_modify,
        submit_limit=submit_limit,
        result_prompt=result_prompt,
        accept_submission_time=accept_submission_time,
        refuse_submission_time=refuse_submission_time,
        random_option=random_option,
        type_name=type_name,
        tags=tags,
        sort_order=sort_order,
    )


@umu_operation(
    name="create_exam_section",
    description="在课程中创建考试类型小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "session_title": "考试小节标题",
        "questions_json": "题目列表的 JSON 字符串",
        "description": "考试说明/描述",
        "exam_duration_minutes": "考试时长（分钟），0=不限时",
        "quiz_count_limit": "考试次数限制，0=不限次数",
        "quiz_pass_mark": "及格线（百分比 0-100）",
        "random_option": "是否随机展示选项顺序",
        "show_user_result": "是否向学员展示成绩",
        "submit_one_by_one": "是否逐题提交",
        "is_required": "是否必修",
        "type_name": "小节类型标签",
        "tags": "标签文本列表",
        "sort_order": "排序序号",
        "accept_submission_time": "开始接受提交时间（Unix时间戳）",
        "refuse_submission_time": "截止提交时间（Unix时间戳）",
        "question_show_mode": '展示样式: "0"=一页式, "1"=逐题式',
        "allow_answer_type": '开放式问题提交格式: "1"=文字+图片, "0"=仅文字',
        "exam_result_setting": '成绩设置: "0"=最后一次提交为准',
        "switch_window_limit": "防切屏次数",
        "quiz_completion_condition": '完成条件: "0"=不设置',
        "share_status": "访问权限: 1=课程内公开, 2=企业内公开, 3=仅自己",
        "submit_permission": "提交权限: 1=课程内学员",
        "show_answer_after_submit": "提交后展示正确答案",
        "allow_add_question_collection": "允许将题目加入考题本",
        "is_show_quiz_ranking": "提交后展示考试排行榜",
        "is_answer_paste": "回答开放式问题是否允许粘贴",
        "quiz_cover_tips_type": '封面提示类型: "1"=自动设置, "0"=手动设置',
        "quiz_cover_tips_content": "封面提示内容",
        "point_ratio": "小节基本积分倍率",
        "is_set_quiz_cover": "是否设置考试封面",
        "jump_button": "提交成功页是否显示跳转按钮",
        "jump_url": "跳转按钮目标 URL",
        "jump_button_title": "跳转按钮文本",
        "result_prompt": "提交成功提示语",
        "show_user_result_mode": '提交后展示内容模式。None 时使用 show_user_result',
        "display_score": "是否向学员展示考试分数",
    },
)
async def create_exam_section(
    client: UMUClient,
    group_id: str,
    session_title: str,
    questions_json: str,
    description: str = "",
    exam_duration_minutes: int = 0,
    quiz_count_limit: int = 0,
    quiz_pass_mark: int = 0,
    random_option: bool = False,
    show_user_result: bool = True,
    submit_one_by_one: bool = False,
    is_required: bool = True,
    type_name: str = "",
    tags: list[str] | None = None,
    sort_order: int = 0,
    accept_submission_time: int = 0,
    refuse_submission_time: int = 0,
    question_show_mode: str = "0",
    allow_answer_type: str = "1",
    exam_result_setting: str = "0",
    switch_window_limit: int = 0,
    quiz_completion_condition: str = "0",
    share_status: int = 1,
    submit_permission: int = 1,
    show_answer_after_submit: bool = False,
    allow_add_question_collection: bool = True,
    is_show_quiz_ranking: bool = True,
    is_answer_paste: bool = True,
    quiz_cover_tips_type: str = "1",
    quiz_cover_tips_content: str = "",
    point_ratio: int = 1,
    is_set_quiz_cover: bool = True,
    jump_button: bool = False,
    jump_url: str = "",
    jump_button_title: str = "",
    result_prompt: str = "",
    show_user_result_mode: str | None = None,
    display_score: bool = True,
) -> dict[str, Any]:
    """在课程中创建考试类型小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        session_title: 小节标题。
        questions_json: 题目列表 JSON 字符串。
        description: 考试说明。
        exam_duration_minutes: 考试时长（分钟）。
        quiz_count_limit: 考试次数限制。
        quiz_pass_mark: 及格线。
        random_option: 是否随机选项。
        show_user_result: 是否展示成绩。
        submit_one_by_one: 是否逐题提交。
        is_required: 是否必修。
        type_name: 小节类型标签。
        tags: 标签列表。
        sort_order: 排序序号。
        accept_submission_time: 开始接受提交时间。
        refuse_submission_time: 截止提交时间。
        question_show_mode: 展示样式。
        allow_answer_type: 开放题提交格式。
        exam_result_setting: 成绩设置。
        switch_window_limit: 防切屏次数。
        quiz_completion_condition: 完成条件。
        share_status: 访问权限。
        submit_permission: 提交权限。
        show_answer_after_submit: 提交后展示正确答案。
        allow_add_question_collection: 允许加入考题本。
        is_show_quiz_ranking: 展示排行榜。
        is_answer_paste: 允许粘贴。
        quiz_cover_tips_type: 封面提示类型。
        quiz_cover_tips_content: 封面提示内容。
        point_ratio: 积分倍率。
        is_set_quiz_cover: 是否设置封面。
        jump_button: 跳转按钮。
        jump_url: 跳转 URL。
        jump_button_title: 跳转按钮文本。
        result_prompt: 提交成功提示。
        show_user_result_mode: 展示内容模式。
        display_score: 是否展示分数。

    Returns:
        包含 session_id 等信息的字典。
    """
    questions = json.loads(questions_json)
    if not isinstance(questions, list):
        raise ValueError("questions_json 必须解析为列表")

    builder = CourseBuilder(client)
    return builder.create_exam_section(
        group_id=group_id,
        session_title=session_title,
        questions=questions,
        description=description,
        exam_duration_seconds=exam_duration_minutes * 60,
        quiz_count_limit=quiz_count_limit,
        quiz_pass_mark=quiz_pass_mark,
        random_option=random_option,
        show_user_result=show_user_result,
        submit_one_by_one=submit_one_by_one,
        accept_submission_time=accept_submission_time,
        refuse_submission_time=refuse_submission_time,
        is_required=is_required,
        type_name=type_name,
        tags=tags,
        sort_order=sort_order,
        question_show_mode=question_show_mode,
        allow_answer_type=allow_answer_type,
        exam_result_setting=exam_result_setting,
        switch_window_limit=switch_window_limit,
        quiz_completion_condition=quiz_completion_condition,
        share_status=share_status,
        submit_permission=submit_permission,
        show_answer_after_submit=show_answer_after_submit,
        allow_add_question_collection=allow_add_question_collection,
        is_show_quiz_ranking=is_show_quiz_ranking,
        is_answer_paste=is_answer_paste,
        quiz_cover_tips_type=quiz_cover_tips_type,
        quiz_cover_tips_content=quiz_cover_tips_content,
        point_ratio=point_ratio,
        is_set_quiz_cover=is_set_quiz_cover,
        jump_button=jump_button,
        jump_url=jump_url,
        jump_button_title=jump_button_title,
        result_prompt=result_prompt,
        show_user_result_mode=show_user_result_mode,
        display_score=display_score,
    )


@umu_operation(
    name="create_signin_section",
    description="在课程中创建签到类型小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "session_title": "签到小节标题",
        "signin_info_json": "签到信息（问题）列表的 JSON 字符串",
        "auto_check": "是否自动审核签到",
        "is_required": "是否必修",
        "point_ratio": "小节基本积分倍率",
        "is_anti_fraud": "是否开启防作弊",
        "mini_program_switch": "是否开启小程序",
        "share_status": "访问权限: 1=课程内公开, 2=企业内公开, 3=仅自己",
        "result_prompt": "签到成功提示语",
        "type_name": "小节类型标签",
        "desc_richtext": "富文本签到说明",
        "tags": "标签文本列表",
        "sort_order": "排序序号",
    },
)
async def create_signin_section(
    client: UMUClient,
    group_id: str,
    session_title: str,
    signin_info_json: str,
    auto_check: bool = True,
    is_required: bool = True,
    point_ratio: int = 1,
    is_anti_fraud: bool = False,
    mini_program_switch: bool = True,
    share_status: int = 1,
    result_prompt: str = "",
    type_name: str = "",
    desc_richtext: str = "",
    tags: list[str] | None = None,
    sort_order: int = 0,
) -> dict[str, Any]:
    """在课程中创建签到类型小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        session_title: 小节标题。
        signin_info_json: 签到信息 JSON 字符串。
        auto_check: 是否自动审核。
        is_required: 是否必修。
        point_ratio: 积分倍率。
        is_anti_fraud: 是否开启防作弊。
        mini_program_switch: 是否开启小程序。
        share_status: 访问权限。
        result_prompt: 成功提示。
        type_name: 小节类型标签。
        desc_richtext: 富文本说明。
        tags: 标签列表。
        sort_order: 排序序号。

    Returns:
        包含 session_id 等信息的字典。
    """
    signin_info_list = json.loads(signin_info_json)
    if not isinstance(signin_info_list, list):
        raise ValueError("signin_info_json 必须解析为列表")
    if not signin_info_list:
        raise ValueError("signin_info_list 不能为空，签到小节至少需要包含一个签到信息")

    builder = CourseBuilder(client)
    return builder.create_signin_section(
        group_id=group_id,
        session_title=session_title,
        signin_info_list=signin_info_list,
        auto_check=auto_check,
        is_required=is_required,
        point_ratio=point_ratio,
        is_anti_fraud=is_anti_fraud,
        mini_program_switch=mini_program_switch,
        share_status=share_status,
        result_prompt=result_prompt,
        type_name=type_name,
        desc_richtext=desc_richtext,
        tags=tags,
        sort_order=sort_order,
    )


@umu_operation(
    name="list_sections",
    description="列出课程中的所有小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "fuzzy_title": "小节标题模糊匹配关键词",
        "top_k": "模糊匹配时最多返回的候选数量",
        "similarity_threshold": "模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
    },
)
async def list_sections(
    client: UMUClient,
    group_id: str,
    fuzzy_title: str | None = None,
    top_k: int = 10,
    similarity_threshold: float = 0.3,
) -> dict[str, Any]:
    """列出课程中的所有小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        fuzzy_title: 模糊匹配关键词。
        top_k: 最多返回数量。
        similarity_threshold: 相似度阈值。

    Returns:
        包含 group_id、count、sections 的字典。
    """
    builder = CourseBuilder(client)
    sections = builder.list_sections(group_id)

    if fuzzy_title and fuzzy_title.strip():
        sections = fuzzy_filter_items(
            sections,
            fuzzy_title,
            key="title",
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )

    return {
        "group_id": group_id,
        "count": len(sections),
        "sections": sections,
    }


@umu_operation(
    name="get_section",
    description="获取单个小节的完整详情",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={"section_id": "小节 ID（即 savesession 返回的 session_id）"},
)
async def get_section(
    client: UMUClient,
    section_id: str,
) -> dict[str, Any]:
    """获取单个小节的完整详情.

    Args:
        client: 已登录的 UMUClient 实例。
        section_id: 小节 ID。

    Returns:
        包含 sessionInfo 和 sectionArr 的字典。
    """
    builder = CourseBuilder(client)
    return builder.get_section(section_id)


@umu_operation(
    name="delete_section",
    description="删除课程中的小节",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "section_id": "小节 ID（即 savesession 返回的 session_id）",
    },
)
async def delete_section(
    client: UMUClient,
    group_id: str,
    section_id: str,
) -> dict[str, Any]:
    """删除课程中的小节.

    Args:
        client: 已登录的 UMUClient 实例。
        group_id: 课程 ID。
        section_id: 小节 ID。

    Returns:
        删除结果字典。
    """
    builder = CourseBuilder(client)
    return builder.delete_session(
        group_id=group_id,
        session_id=section_id,
    )


@umu_operation(
    name="toggle_section_visibility",
    description="切换小节对学员的可见性（打开/关闭）",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "section_id": "小节 ID",
        "visible": "True=打开（学员可见）, False=关闭（学员不可见）",
    },
)
async def toggle_section_visibility(
    client: UMUClient,
    section_id: str,
    visible: bool,
) -> dict[str, Any]:
    """切换小节对学员的可见性.

    Args:
        client: 已登录的 UMUClient 实例。
        section_id: 小节 ID。
        visible: 是否可见。

    Returns:
        操作结果字典。
    """
    builder = CourseBuilder(client)
    return builder.toggle_session_visibility(
        session_id=section_id,
        visible=visible,
    )
