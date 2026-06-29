# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""资源管理相关共享业务操作.

将讲师/管理员的资源库操作（SCORM、文档、音视频）下沉为无状态业务函数，
供 teacher/admin MCP server 复用。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from ...adapters.mcp.cos_upload import (
    ScormUploader,
    UploadResult,
    validate_file_path,
)
from ...adapters.mcp.document_upload import (
    DocumentUploader,
    validate_document_path,
)
from ...adapters.mcp.video_upload import (
    VIDEO_MEDIA_TYPE,
    VideoUploader,
    validate_video_path,
)
from ...core.client import UMUClient
from ...core.errors import UMUError
from ..decorators import umu_operation
from ..shared.progress import report_pagination_progress

logger = logging.getLogger(__name__)


def _format_size(size_bytes: int) -> str:
    """格式化文件大小为人类可读字符串."""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    else:
        return f"{size_bytes} B"


def _find_document_by_name_size(
    client: UMUClient, file_name: str, file_size: int
) -> str | None:
    """根据文件名和大小查找已存在的文档资源 ID（用于幂等性检查）.

    Returns:
        已存在的 resource_id，或 None（未找到）
    """
    try:
        resp = client.get(
            client.desktop_url("/ajax/resource/getresourcelist"),
            params={
                "page": "1",
                "is_recycle": "0",
                "search_keyword": file_name,
                "page_rows": "20",
                "order_by": "create_time",
                "is_desc": "1",
                "media_type": "docweike",
                "status_str": "in_use,transcoding,wait_transcoding",
            },
        )
        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            return None

        for item in resp.get("data", {}).get("list", []):
            existing_name = item.get("file_name", "")
            existing_size = int(item.get("file_size", 0) or 0)
            if existing_name == file_name and existing_size == file_size:
                return str(item.get("id", ""))
        return None
    except Exception:
        return None


def _verify_resource_registered(
    client: UMUClient, resource_id: str, max_attempts: int = 3
) -> bool:
    """防御性验证：确认资源已成功注册到资源列表.

    Args:
        client: UMUClient 实例
        resource_id: 要验证的资源 ID
        max_attempts: 最大重试次数

    Returns:
        True 如果确认注册成功，False 如果无法确认
    """
    for attempt in range(max_attempts):
        try:
            info_resp = client.get(
                client.desktop_url("/ajax/resource/getresourceinfo"),
                params={"resource_id": resource_id, "media_type": "docweike"},
            )
            if info_resp.get("status") is True or info_resp.get("error_code") == 0:
                info = info_resp.get("data", {}).get("info")
                if info:
                    return True

            list_resp = client.get(
                client.desktop_url("/ajax/resource/getresourcelist"),
                params={
                    "page": "1",
                    "is_recycle": "0",
                    "search_keyword": "",
                    "page_rows": "50",
                    "order_by": "create_time",
                    "is_desc": "1",
                    "media_type": "docweike",
                },
            )
            for item in list_resp.get("data", {}).get("list", []):
                if item.get("id") == resource_id:
                    return True

        except Exception as e:
            logger.warning("资源注册验证失败 (attempt %d/%d): %s", attempt + 1, max_attempts, e)

        if attempt < max_attempts - 1:
            time.sleep(0.5)

    return False


@umu_operation(
    name="upload_scorm",
    description="上传 SCORM 格式的课程数据包（.zip）到 UMU 资源库",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "file_path": "本地 SCORM zip 文件的绝对路径，如 /path/to/course.zip",
        "name": "上传后在 UMU 资源库中显示的名称。如果不提供，默认使用原文件名（不含 .zip 后缀）",
        "auto_rename": "上传成功后是否自动重命名。如果为 true 且提供了 name，会在上传后自动重命名资源",
    },
)
async def upload_scorm(
    client: UMUClient,
    file_path: str,
    name: str | None = None,
    auto_rename: bool = False,
) -> dict[str, Any]:
    """上传 SCORM 包到 UMU 资源库."""
    try:
        validate_file_path(file_path)
    except FileNotFoundError as e:
        raise UMUError(str(e), code="FILE_NOT_FOUND") from e
    except ValueError as e:
        raise UMUError(str(e), code="INVALID_FILE") from e

    uploader = ScormUploader(client, client.base_url)
    result: UploadResult = await uploader.run(file_path, name)

    rename_ok = False
    if auto_rename and name and result.resource_id:
        rename_resp = client.post(
            client.desktop_url("/ajax/resource/renameresource"),
            data={
                "resource_id": result.resource_id,
                "file_name": name,
                "media_type": "videoweike",
            },
        )
        if rename_resp.get("status") is True or rename_resp.get("error_code") == 0:
            rename_ok = True
            logger.info("自动重命名成功: %s", name)
        else:
            logger.warning("自动重命名失败: %s", rename_resp.get("error", ""))

    return {
        "resource_id": result.resource_id,
        "file_url": result.file_url,
        "scorm_url": result.scorm_url,
        "task_id": result.task_id,
        "status": result.status,
        "name": result.name,
        "file_size": result.file_size,
        "task_result": result.task_result,
        "progress": {
            "stage": result.progress.stage,
            "current_part": result.progress.current_part,
            "total_parts": result.progress.total_parts,
            "bytes_uploaded": result.progress.bytes_uploaded,
            "bytes_total": result.progress.bytes_total,
            "percent": result.progress.percent,
            "estimated_seconds_remaining": result.progress.estimated_seconds_remaining,
        },
        "rename_status": "success" if rename_ok else "skipped",
    }


@umu_operation(
    name="upload_document",
    description="上传文档（Excel/Word/PPT/PDF/TXT）到\"我的文档\"资源库",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "file_path": "本地文档文件的绝对路径，支持 .xlsx/.xls, .docx/.doc, .pptx/.ppt, .pdf, .txt",
        "name": "上传后在 UMU 文档库中显示的名称。如果不提供，默认使用原文件名",
        "skip_existing": "如果为 True，上传前检查是否已有同名同大小的文档，存在则跳过上传并返回已有资源 ID",
    },
)
async def upload_document(
    client: UMUClient,
    file_path: str,
    name: str | None = None,
    skip_existing: bool = False,
) -> dict[str, Any]:
    """上传文档到 UMU 文档库."""
    try:
        validate_document_path(file_path)
    except FileNotFoundError as e:
        raise ValueError(str(e)) from e
    except ValueError:
        raise

    file_size = os.path.getsize(file_path)
    display_name = name or os.path.basename(file_path)

    if skip_existing:
        existing_id = _find_document_by_name_size(client, display_name, file_size)
        if existing_id:
            logger.info("文档已存在，跳过上传: %s (resource_id=%s)", display_name, existing_id)
            return {
                "resource_id": existing_id,
                "name": display_name,
                "file_size": file_size,
                "status": "skipped",
                "is_duplicate": True,
            }

    uploader = DocumentUploader(client, client.base_url)
    result: UploadResult = await uploader.run(file_path, name)

    is_verified = False
    if result.resource_id:
        is_verified = _verify_resource_registered(client, result.resource_id)
        if not is_verified:
            logger.warning(
                "文档上传成功但资源注册验证未通过: resource_id=%s",
                result.resource_id,
            )

    next_actions: list[str] = []
    if result.resource_id:
        next_actions.append(
            f"tch_rename_document(resource_id='{result.resource_id}', file_name='新名称')"
        )
        next_actions.append(
            f"tch_delete_document(resource_id='{result.resource_id}')"
        )

    return {
        "resource_id": result.resource_id,
        "file_url": result.file_url,
        "name": result.name,
        "file_size": result.file_size,
        "status": result.status,
        "is_verified": is_verified if result.resource_id else False,
        "progress": {
            "stage": result.progress.stage,
            "current_part": result.progress.current_part,
            "total_parts": result.progress.total_parts,
            "bytes_uploaded": result.progress.bytes_uploaded,
            "bytes_total": result.progress.bytes_total,
            "percent": result.progress.percent,
            "estimated_seconds_remaining": result.progress.estimated_seconds_remaining,
        },
        "next_actions": next_actions,
    }


@umu_operation(
    name="upload_audio_video",
    description="上传音视频文件到\"我的音视频\"资源库",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "file_path": "本地音视频文件的绝对路径，支持 mp4, mov, avi, mkv, mp3, wav, flac 等 36 种格式",
        "name": "上传后在 UMU 音视频库中显示的名称。如果不提供，默认使用原文件名",
    },
)
async def upload_audio_video(
    client: UMUClient,
    file_path: str,
    name: str | None = None,
) -> dict[str, Any]:
    """上传音视频到 UMU 音视频库."""
    try:
        validate_video_path(file_path)
    except FileNotFoundError as e:
        raise ValueError(str(e)) from e
    except ValueError:
        raise

    uploader = VideoUploader(client, client.base_url)
    result: UploadResult = await uploader.run(file_path, name)

    next_actions: list[str] = []
    if result.resource_id:
        next_actions.append(
            f"tch_rename_audio_video(resource_id='{result.resource_id}', file_name='新名称')"
        )
        next_actions.append(
            f"tch_delete_audio_video(resource_id='{result.resource_id}')"
        )

    return {
        "resource_id": result.resource_id,
        "file_url": result.file_url,
        "name": result.name,
        "file_size": result.file_size,
        "status": result.status,
        "progress": {
            "stage": result.progress.stage,
            "current_part": result.progress.current_part,
            "total_parts": result.progress.total_parts,
            "bytes_uploaded": result.progress.bytes_uploaded,
            "bytes_total": result.progress.bytes_total,
            "percent": result.progress.percent,
            "estimated_seconds_remaining": result.progress.estimated_seconds_remaining,
        },
        "next_actions": next_actions,
    }


@umu_operation(
    name="list_resources",
    description="查询讲师的音视频/SCORM 资源列表",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 15",
        "search_keyword": "搜索关键词，按文件名模糊匹配",
        "media_type": "媒体类型筛选，默认 videoweike（音视频/SCORM）",
        "ext_type": "扩展类型筛选，如 'scorm' 只显示 SCORM 资源",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果",
    },
)
async def list_resources(
    client: UMUClient,
    page: int = 1,
    page_size: int = 15,
    search_keyword: str | None = None,
    media_type: str = "videoweike",
    ext_type: str | None = None,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询音视频/SCORM 资源列表."""

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, str] = {
            "page": str(p),
            "is_recycle": "0",
            "search_keyword": search_keyword or "",
            "page_rows": str(sz),
            "order_by": "create_time",
            "is_desc": "1",
            "media_type": media_type,
            "status_str": "in_use,transcoding,wait_transcoding",
        }
        if ext_type:
            params["ext_type"] = ext_type

        resp = client.get(
            client.desktop_url("/ajax/resource/getresourcelist"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise UMUError(resp.get("error", "获取资源列表失败"), code="LIST_RESOURCES_FAILED")

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        resource_list = data.get("list", [])

        formatted_list = []
        for item in resource_list:
            formatted_list.append({
                "id": item.get("id", ""),
                "name": item.get("file_name", ""),
                "size": int(item.get("file_size", 0) or 0),
                "url": item.get("url", ""),
                "ext": item.get("ext", ""),
                "media_type": item.get("media_type", ""),
                "transcoding_url": item.get("transcoding_url", ""),
                "transcoding_ext": item.get("transcoding_ext", ""),
                "create_time": item.get("create_time", ""),
                "status": item.get("status", ""),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    if fetch_all:
        batch_size = 50
        all_items: list[dict[str, Any]] = []
        total_all = 0
        current_page = 1

        while True:
            page_items, total_all = _fetch_page(current_page, batch_size)
            all_items.extend(page_items)

            report_pagination_progress(
                "list_resources",
                current_page,
                len(all_items),
                total_all,
                batch_size,
            )

            if not page_items or len(all_items) >= total_all:
                report_pagination_progress(
                    "list_resources",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_complete=True,
                )
                break

            if current_page >= 50:
                report_pagination_progress(
                    "list_resources",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_safety_limit=True,
                )
                logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                break

            current_page += 1

        return {
            "resources": all_items,
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    formatted_list, total_all = _fetch_page(page, page_size)
    return {
        "resources": formatted_list,
        "pagination": {
            "total": total_all,
            "total_pages": 0,
            "current_page": page,
            "page_size": page_size,
        },
    }


@umu_operation(
    name="list_documents",
    description="查询讲师\"我的文档\"中的文档列表",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 15",
        "search_keyword": "搜索关键词，按文件名模糊匹配",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果",
    },
)
async def list_documents(
    client: UMUClient,
    page: int = 1,
    page_size: int = 15,
    search_keyword: str | None = None,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询文档资源列表."""

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, str] = {
            "page": str(p),
            "is_recycle": "0",
            "search_keyword": search_keyword or "",
            "page_rows": str(sz),
            "order_by": "create_time",
            "is_desc": "1",
            "media_type": "docweike",
            "status_str": "in_use,transcoding,wait_transcoding",
        }

        resp = client.get(
            client.desktop_url("/ajax/resource/getresourcelist"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取文档列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        resource_list = data.get("list", [])

        formatted_list = []
        for item in resource_list:
            file_size = int(item.get("file_size", 0) or 0)
            status = item.get("status", "")
            size_note = None
            if file_size == 0 and status != "wait_transcoding":
                size_note = "size_unknown"
            formatted_list.append({
                "id": item.get("id", ""),
                "name": item.get("file_name", ""),
                "size": file_size,
                "size_formatted": _format_size(file_size),
                "size_note": size_note,
                "url": item.get("url", ""),
                "ext": item.get("ext", ""),
                "media_type": item.get("media_type", ""),
                "create_time": item.get("create_time", ""),
                "status": status,
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    if fetch_all:
        batch_size = 50
        all_items: list[dict[str, Any]] = []
        total_all = 0
        current_page = 1

        while True:
            page_items, total_all = _fetch_page(current_page, batch_size)
            all_items.extend(page_items)

            report_pagination_progress(
                "list_documents",
                current_page,
                len(all_items),
                total_all,
                batch_size,
            )

            if not page_items or len(all_items) >= total_all:
                report_pagination_progress(
                    "list_documents",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_complete=True,
                )
                break

            if current_page >= 50:
                report_pagination_progress(
                    "list_documents",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_safety_limit=True,
                )
                logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                break

            current_page += 1

        return {
            "documents": all_items,
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    formatted_list, _ = _fetch_page(page, page_size)
    return {
        "documents": formatted_list,
        "pagination": {
            "total": 0,
            "total_pages": 0,
            "current_page": page,
            "page_size": page_size,
        },
    }


@umu_operation(
    name="list_audio_videos",
    description="查询讲师\"我的音视频\"中的音视频列表",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 15",
        "search_keyword": "搜索关键词，按文件名模糊匹配",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果",
    },
)
async def list_audio_videos(
    client: UMUClient,
    page: int = 1,
    page_size: int = 15,
    search_keyword: str | None = None,
    fetch_all: bool = False,
) -> dict[str, Any]:
    """查询音视频资源列表."""

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        params: dict[str, str] = {
            "page": str(p),
            "is_recycle": "0",
            "search_keyword": search_keyword or "",
            "page_rows": str(sz),
            "order_by": "create_time",
            "is_desc": "1",
            "media_type": VIDEO_MEDIA_TYPE,
            "status_str": "in_use,transcoding,wait_transcoding",
        }

        resp = client.get(
            client.desktop_url("/ajax/resource/getresourcelist"),
            params=params,
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取音视频列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        resource_list = data.get("list", [])

        formatted_list = []
        for item in resource_list:
            file_size = int(item.get("file_size", 0) or 0)
            formatted_list.append({
                "id": item.get("id", ""),
                "name": item.get("file_name", ""),
                "size": file_size,
                "size_formatted": _format_size(file_size),
                "url": item.get("url", ""),
                "ext": item.get("ext", ""),
                "media_type": item.get("media_type", ""),
                "create_time": item.get("create_time", ""),
                "status": item.get("status", ""),
            })

        total_all = int(page_info.get("list_total_num", 0) or 0)
        return formatted_list, total_all

    if fetch_all:
        batch_size = 50
        all_items: list[dict[str, Any]] = []
        total_all = 0
        current_page = 1

        while True:
            page_items, total_all = _fetch_page(current_page, batch_size)
            all_items.extend(page_items)

            report_pagination_progress(
                "list_audio_videos",
                current_page,
                len(all_items),
                total_all,
                batch_size,
            )

            if not page_items or len(all_items) >= total_all:
                report_pagination_progress(
                    "list_audio_videos",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_complete=True,
                )
                break

            if current_page >= 50:
                report_pagination_progress(
                    "list_audio_videos",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_safety_limit=True,
                )
                logger.warning("fetch_all 达到安全上限 50 页，停止获取")
                break

            current_page += 1

        return {
            "videos": all_items,
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    formatted_list, _ = _fetch_page(page, page_size)
    return {
        "videos": formatted_list,
        "pagination": {
            "total": 0,
            "total_pages": 0,
            "current_page": page,
            "page_size": page_size,
        },
    }


@umu_operation(
    name="rename_resource",
    description="重命名资源库中的已有资源",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "resource_id": "资源 ID，可从 list_resources 或 upload_scorm 返回结果中获取",
        "file_name": "新的文件名（不需要包含 .zip 后缀，系统会自动保留）",
        "media_type": "媒体类型，默认 videoweike",
    },
)
async def rename_resource(
    client: UMUClient,
    resource_id: str,
    file_name: str,
    media_type: str = "videoweike",
) -> dict[str, Any]:
    """重命名资源库中的已有资源."""
    resp = client.post(
        client.desktop_url("/ajax/resource/renameresource"),
        data={
            "resource_id": resource_id,
            "file_name": file_name,
            "media_type": media_type,
        },
    )

    if resp.get("status") is True or resp.get("error_code") == 0:
        return {
            "resource_id": resource_id,
            "new_name": file_name,
            "result": resp.get("data", {}).get("result", 1),
        }

    raise UMUError(resp.get("error", "重命名失败"), code="RENAME_FAILED")


@umu_operation(
    name="delete_resource",
    description="删除资源库中的资源（移到回收站）",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "resource_id": "资源 ID，可从 list_resources 获取",
        "media_type": "媒体类型，默认 videoweike",
    },
)
async def delete_resource(
    client: UMUClient,
    resource_id: str,
    media_type: str = "videoweike",
) -> dict[str, Any]:
    """删除资源库中的资源（移到回收站）."""
    resp = client.post(
        client.desktop_url("/ajax/resource/deleteresource"),
        data={
            "resource_id": resource_id,
            "media_type": media_type,
        },
    )

    if resp.get("status") is True or resp.get("error_code") == 0:
        return {"resource_id": resource_id, "deleted": True}

    raise UMUError(resp.get("error", "删除失败"), code="DELETE_FAILED")


@umu_operation(
    name="rename_document",
    description="重命名\"我的文档\"中的已有文档",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "resource_id": "文档资源 ID，可从 list_documents 获取",
        "file_name": "新的文件名（不需要包含扩展名，系统会自动保留）",
    },
)
async def rename_document(
    client: UMUClient,
    resource_id: str,
    file_name: str,
) -> dict[str, Any]:
    """重命名文档资源."""
    resp = client.post(
        client.desktop_url("/ajax/resource/renameresource"),
        data={
            "resource_id": resource_id,
            "file_name": file_name,
            "media_type": "docweike",
        },
    )

    if resp.get("status") is True or resp.get("error_code") == 0:
        return {
            "resource_id": resource_id,
            "new_name": file_name,
            "result": resp.get("data", {}).get("result", 1),
        }

    raise RuntimeError(resp.get("error", "重命名失败"))


@umu_operation(
    name="delete_document",
    description="删除\"我的文档\"中的文档（移到回收站）",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "resource_id": "文档资源 ID，可从 list_documents 获取",
    },
)
async def delete_document(
    client: UMUClient,
    resource_id: str,
) -> dict[str, Any]:
    """删除文档资源（移到回收站）.

    删除前会检查文档是否被课程小节引用，若被引用则在返回中附带警告。
    """
    try:
        refer_resp = client.get(
            client.desktop_url("/ajax/resource/isreferredbysession"),
            params={
                "resource_id": resource_id,
                "media_type": "docweike",
            },
        )
        is_referred = refer_resp.get("data", {}).get("is_referred", False)
        if is_referred:
            logger.warning("文档 %s 被课程小节引用，删除可能影响课程内容", resource_id)
    except Exception as e:
        logger.warning("检查文档引用状态失败（非致命）: %s", e)
        is_referred = False

    resp = client.post(
        client.desktop_url("/ajax/resource/deleteresource"),
        data={
            "resource_id": resource_id,
            "delete_mode": "1",
            "media_type": "docweike",
        },
    )

    if resp.get("status") is True or resp.get("error_code") == 0:
        return {
            "resource_id": resource_id,
            "deleted": True,
            "was_referred": is_referred,
        }

    raise RuntimeError(resp.get("error", "删除失败"))


@umu_operation(
    name="rename_audio_video",
    description="重命名\"我的音视频\"中的已有音视频",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "resource_id": "音视频资源 ID，可从 list_audio_videos 获取",
        "file_name": "新的文件名（不需要包含扩展名，系统会自动保留）",
    },
)
async def rename_audio_video(
    client: UMUClient,
    resource_id: str,
    file_name: str,
) -> dict[str, Any]:
    """重命名音视频资源."""
    resp = client.post(
        client.desktop_url("/ajax/resource/renameresource"),
        data={
            "resource_id": resource_id,
            "file_name": file_name,
            "media_type": VIDEO_MEDIA_TYPE,
        },
    )

    if resp.get("status") is True or resp.get("error_code") == 0:
        return {
            "resource_id": resource_id,
            "new_name": file_name,
            "result": resp.get("data", {}).get("result", 1),
        }

    raise RuntimeError(resp.get("error", "重命名失败"))


@umu_operation(
    name="delete_audio_video",
    description="删除\"我的音视频\"中的音视频（移到回收站）",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "resource_id": "音视频资源 ID，可从 list_audio_videos 获取",
    },
)
async def delete_audio_video(
    client: UMUClient,
    resource_id: str,
) -> dict[str, Any]:
    """删除音视频资源（移到回收站）.

    删除前会检查音视频是否被课程小节引用，若被引用则在返回中附带警告。
    """
    try:
        refer_resp = client.get(
            client.desktop_url("/ajax/resource/isreferredbysession"),
            params={
                "resource_id": resource_id,
                "media_type": VIDEO_MEDIA_TYPE,
            },
        )
        is_referred = refer_resp.get("data", {}).get("is_referred", False)
        if is_referred:
            logger.warning("音视频 %s 被课程小节引用，删除可能影响课程内容", resource_id)
    except Exception as e:
        logger.warning("检查音视频引用状态失败（非致命）: %s", e)
        is_referred = False

    resp = client.post(
        client.desktop_url("/ajax/resource/deleteresource"),
        data={
            "resource_id": resource_id,
            "delete_mode": "1",
            "media_type": VIDEO_MEDIA_TYPE,
        },
    )

    if resp.get("status") is True or resp.get("error_code") == 0:
        return {
            "resource_id": resource_id,
            "deleted": True,
            "was_referred": is_referred,
        }

    raise RuntimeError(resp.get("error", "删除失败"))


@umu_operation(
    name="upload_documents_batch",
    description="批量上传文档到\"我的文档\"资源库",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "file_paths": "本地文档文件的绝对路径列表，支持 .xlsx/.xls, .docx/.doc, .pptx/.ppt, .pdf, .txt",
        "skip_existing": "如果为 True，每个文件上传前检查是否已有同名同大小的文档，存在则跳过",
    },
)
async def upload_documents_batch(
    client: UMUClient,
    file_paths: list[str],
    skip_existing: bool = False,
) -> dict[str, Any]:
    """批量上传文档到 UMU 文档库."""
    if not file_paths:
        raise ValueError("file_paths 不能为空列表")

    invalid_paths: list[dict[str, Any]] = []
    valid_paths: list[str] = []
    for fp in file_paths:
        if not os.path.isfile(fp):
            suggestions: list[str] = []
            dir_path = os.path.dirname(fp) or "."
            base_name = os.path.basename(fp)
            try:
                for fname in os.listdir(dir_path):
                    if fname.replace(" ", "") == base_name.replace(" ", ""):
                        suggestions.append(os.path.join(dir_path, fname))
            except OSError:
                pass
            invalid_paths.append({
                "path": fp,
                "reason": "文件不存在",
                "suggestions": suggestions,
            })
        else:
            try:
                validate_document_path(fp)
                valid_paths.append(fp)
            except (FileNotFoundError, ValueError) as e:
                invalid_paths.append({
                    "path": fp,
                    "reason": str(e),
                    "suggestions": [],
                })

    if not valid_paths:
        raise ValueError(
            f"所有文件路径均无效。{invalid_paths[0]['reason'] if invalid_paths else ''}"
        )

    success_results: list[dict[str, Any]] = []
    skipped_results: list[dict[str, Any]] = []
    failed_results: list[dict[str, Any]] = []

    for i, fp in enumerate(valid_paths, 1):
        file_size = os.path.getsize(fp)
        display_name = os.path.basename(fp)
        logger.info("[%d/%d] 上传文档: %s", i, len(valid_paths), display_name)

        if skip_existing:
            existing_id = _find_document_by_name_size(client, display_name, file_size)
            if existing_id:
                logger.info("  跳过（已存在）: %s", display_name)
                skipped_results.append({
                    "path": fp,
                    "name": display_name,
                    "resource_id": existing_id,
                })
                continue

        try:
            uploader = DocumentUploader(client, client.base_url)
            result: UploadResult = await uploader.run(fp)

            is_verified = False
            if result.resource_id:
                is_verified = _verify_resource_registered(client, result.resource_id)

            success_results.append({
                "path": fp,
                "resource_id": result.resource_id,
                "name": result.name,
                "file_size": result.file_size,
                "is_verified": is_verified,
            })
        except Exception as e:
            failed_results.append({"path": fp, "error": str(e)})

    return {
        "summary": {
            "total": len(file_paths),
            "valid": len(valid_paths),
            "invalid": len(invalid_paths),
            "success": len(success_results),
            "skipped": len(skipped_results),
            "failed": len(failed_results),
        },
        "invalid_paths": invalid_paths,
        "success_results": success_results,
        "skipped_results": skipped_results,
        "failed_results": failed_results,
    }


@umu_operation(
    name="delete_documents_batch",
    description="批量删除\"我的文档\"中的文档（移到回收站）",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "resource_ids": "要删除的文档资源 ID 列表，可从 list_documents 获取",
    },
)
async def delete_documents_batch(
    client: UMUClient,
    resource_ids: list[str],
) -> dict[str, Any]:
    """批量删除文档资源（移到回收站）."""
    if not resource_ids:
        raise ValueError("resource_ids 不能为空列表")

    success_ids: list[str] = []
    failed_ids: list[dict[str, Any]] = []
    warned_ids: list[dict[str, Any]] = []

    for i, rid in enumerate(resource_ids, 1):
        logger.info("[%d/%d] 删除文档 %s", i, len(resource_ids), rid)
        try:
            try:
                refer_resp = client.get(
                    client.desktop_url("/ajax/resource/isreferredbysession"),
                    params={"resource_id": rid, "media_type": "docweike"},
                )
                is_referred = refer_resp.get("data", {}).get("is_referred", False)
            except Exception:
                is_referred = False

            resp = client.post(
                client.desktop_url("/ajax/resource/deleteresource"),
                data={
                    "resource_id": rid,
                    "delete_mode": "1",
                    "media_type": "docweike",
                },
            )

            if resp.get("status") is True or resp.get("error_code") == 0:
                if is_referred:
                    warned_ids.append({"resource_id": rid, "was_referred": True})
                else:
                    success_ids.append(rid)
            else:
                failed_ids.append({
                    "resource_id": rid,
                    "error": resp.get("error", "删除失败"),
                })
        except Exception as e:
            failed_ids.append({"resource_id": rid, "error": str(e)})

    return {
        "summary": {
            "total": len(resource_ids),
            "success": len(success_ids),
            "warned": len(warned_ids),
            "failed": len(failed_ids),
        },
        "success_ids": success_ids,
        "warned_ids": warned_ids,
        "failed_ids": failed_ids,
    }
