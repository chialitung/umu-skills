# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程管理跨角色共享业务操作.

Admin 与 Teacher 在课程管理（列表、详情、分类、审核、访问权限、自动关闭等）上
调用的 UMU API 高度相同，本模块将公共逻辑下沉为无状态业务函数，通过
@umu_operation 注册到对应角色的 MCP server。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from ...adapters.mcp.course_builder import CourseBuilder
from ...adapters.mcp.utils import (
    _format_auto_close_tips,
    _parse_auto_close_time,
    fuzzy_filter_items_multi_key,
)
from ...core.client import UMUClient
from ...core.errors import UMUError
from ..decorators import umu_operation
from ..shared.progress import report_pagination_progress


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 课程列表与查询
# ---------------------------------------------------------------------------
@umu_operation(
    name="list_created_courses",
    description="获取当前讲师已创建的课程列表",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 10，最大 100",
        "order": "排序方式：update_time=按更新时间, create_time=按创建时间",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
    },
)
async def list_created_courses(
    client: UMUClient,
    page: int = 1,
    page_size: int = 10,
    order: str = "update_time",
    fetch_all: bool = False,
) -> dict[str, Any]:
    """获取当前讲师已创建的课程列表."""

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        resp = client.get(
            client.desktop_url("/api/group/getgrouplist"),
            params={
                "t": str(int(time.time() * 1000)),
                "from_type": "web",
                "order": order,
                "page": str(p),
                "size": str(sz),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取已创建课程列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        course_list = data.get("list", [])

        formatted_list = []
        for item in course_list:
            info = item.get("groupInfo", {})
            formatted_list.append({
                "group_id": info.get("id", ""),
                "title": info.get("title", ""),
                "teacher_name": info.get("teacher_name", ""),
                "teacher_id": info.get("teacher_id", ""),
                "access_code": info.get("access_code", ""),
                "cover_url": info.get("head_img", ""),
                "bg_url": info.get("bg_img", ""),
                "share_url": info.get("sharePcUrl", ""),
                "lesson_type": info.get("lesson_type", ""),
                "release_status": info.get("release_status", ""),
                "create_time": info.get("creat_time", ""),
                "update_time": info.get("update_time", ""),
                "stime": info.get("stime", ""),
                "etime": info.get("etime", ""),
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
                "list_created_courses",
                current_page,
                len(all_items),
                total_all,
                batch_size,
                is_complete=not page_items or len(all_items) >= total_all,
            )

            if not page_items or len(all_items) >= total_all:
                break

            if current_page >= 50:
                report_pagination_progress(
                    "list_created_courses",
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
            "courses": all_items,
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    formatted_list, _ = _fetch_page(page, page_size)
    return {
        "courses": formatted_list,
        "pagination": {
            "total": 0,
            "total_pages": 0,
            "current_page": page,
            "page_size": page_size,
        },
    }


@umu_operation(
    name="list_cooperated_courses",
    description="获取别人协同给当前讲师的课程列表",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 10，最大 100",
        "order": "排序方式：update_time=按更新时间, create_time=按创建时间",
        "fetch_all": "是否自动获取全量数据。设为 True 时忽略 page/page_size，自动遍历所有分页并合并结果。",
    },
)
async def list_cooperated_courses(
    client: UMUClient,
    page: int = 1,
    page_size: int = 10,
    order: str = "update_time",
    fetch_all: bool = False,
) -> dict[str, Any]:
    """获取别人协同给当前讲师的课程列表."""

    def _fetch_page(p: int, sz: int) -> tuple[list[dict[str, Any]], int]:
        resp = client.get(
            client.desktop_url("/api/group/getcooperategrouplist"),
            params={
                "t": str(int(time.time() * 1000)),
                "from_type": "web",
                "order": order,
                "page": str(p),
                "size": str(sz),
            },
        )

        if resp.get("status") not in (True, "true") and resp.get("error_code") != 0:
            raise RuntimeError(resp.get("error", "获取协同课程列表失败"))

        data = resp.get("data", {})
        page_info = data.get("page_info", {})
        course_list = data.get("list", [])

        formatted_list = []
        for item in course_list:
            info = item.get("groupInfo", {})
            formatted_list.append({
                "group_id": info.get("id", ""),
                "title": info.get("title", ""),
                "teacher_name": info.get("teacher_name", ""),
                "teacher_id": info.get("teacher_id", ""),
                "access_code": info.get("access_code", ""),
                "cover_url": info.get("head_img", ""),
                "bg_url": info.get("bg_img", ""),
                "share_url": info.get("sharePcUrl", ""),
                "lesson_type": info.get("lesson_type", ""),
                "release_status": info.get("release_status", ""),
                "create_time": info.get("creat_time", ""),
                "update_time": info.get("update_time", ""),
                "stime": info.get("stime", ""),
                "etime": info.get("etime", ""),
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
                "list_cooperated_courses",
                current_page,
                len(all_items),
                total_all,
                batch_size,
                is_complete=not page_items or len(all_items) >= total_all,
            )

            if not page_items or len(all_items) >= total_all:
                break

            if current_page >= 50:
                report_pagination_progress(
                    "list_cooperated_courses",
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
            "courses": all_items,
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    formatted_list, _ = _fetch_page(page, page_size)
    return {
        "courses": formatted_list,
        "pagination": {
            "total": 0,
            "total_pages": 0,
            "current_page": page,
            "page_size": page_size,
        },
    }


@umu_operation(
    name="get_categories",
    description="获取当前账号的课程分类树",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "fuzzy_name": "可选的分类名称模糊匹配关键词。提供时会从全部分类中筛选最匹配的候选，并返回相似度分数。",
        "top_k": "模糊匹配时最多返回的候选数量",
        "similarity_threshold": "模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
    },
)
async def get_categories(
    client: UMUClient,
    fuzzy_name: str | None = None,
    top_k: int = 10,
    similarity_threshold: float = 0.3,
) -> dict[str, Any]:
    """获取当前账号的课程分类树.

    返回分类树 JSON，包含 tree（嵌套结构）和 flat（扁平列表）两种形式。
    """
    builder = CourseBuilder(client)
    tree = builder.get_category_tree()

    flat_list: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], path: list[str]) -> None:
        node_id = str(node.get("id", ""))
        node_name = str(node.get("name", ""))
        current_path = path + [node_name]
        flat_list.append({
            "id": node_id,
            "name": node_name,
            "parent_id": str(node.get("parent_id", "")),
            "path": " > ".join(current_path),
        })
        for sub in node.get("sub_category", []):
            walk(sub, current_path)

    for root in tree:
        walk(root, [])

    if fuzzy_name and fuzzy_name.strip():
        flat_list = fuzzy_filter_items_multi_key(
            flat_list,
            fuzzy_name,
            keys=["name", "path"],
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )

    return {
        "tree": tree,
        "flat": flat_list,
        "total_count": len(flat_list),
    }


@umu_operation(
    name="get_course",
    description="获取课程的完整可修改信息",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "include_fulltext": "是否同时获取富文本 HTML 内容",
    },
)
async def get_course(
    client: UMUClient,
    group_id: str,
    include_fulltext: bool = False,
) -> dict[str, Any]:
    """获取课程的完整可修改信息."""
    builder = CourseBuilder(client)
    return builder.get_course(group_id, include_fulltext=include_fulltext)


@umu_operation(
    name="get_course_detail",
    description="获取课程的完整详情，包含小节列表和绑定资源删除状态检测",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "include_fulltext": "是否同时获取富文本 HTML 内容",
        "check_resource_status": "是否检测每个小节绑定资源的删除状态。开启后会检查 resource_info.is_recycle 字段，标记被删除到回收站的资源。",
    },
)
async def get_course_detail(
    client: UMUClient,
    group_id: str,
    include_fulltext: bool = False,
    check_resource_status: bool = True,
) -> dict[str, Any]:
    """获取课程的完整详情，包含小节列表和绑定资源删除状态检测."""
    builder = CourseBuilder(client)
    return builder.get_course_detail(
        group_id,
        include_fulltext=include_fulltext,
        check_resource_status=check_resource_status,
    )


@umu_operation(
    name="submit_course_for_audit",
    description="将课程提交至企业知识库进行审核",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={"group_id": "课程 ID，要提交审核的课程"},
)
async def submit_course_for_audit(
    client: UMUClient,
    group_id: str,
) -> dict[str, Any]:
    """将课程提交至企业知识库进行审核.

    提交后课程状态会变为待审核（audit_status=0, release_status=2）。
    """
    builder = CourseBuilder(client)
    result = builder.submit_course_for_audit(group_id=group_id)

    return {
        "group_id": group_id,
        "release_status": result.get("data", {}).get("release_status"),
        "audit_status": result.get("data", {}).get("audit_status"),
    }


# ---------------------------------------------------------------------------
# 课程自动关闭时间
# ---------------------------------------------------------------------------
@umu_operation(
    name="get_course_auto_close",
    description="查询课程自动关闭时间设置",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={"group_id": "课程 ID"},
)
async def get_course_auto_close(
    client: UMUClient,
    group_id: str,
) -> dict[str, Any]:
    """查询课程自动关闭时间设置.

    返回当前课程的 open_time、close_time、open_access_permission。
    close_time 为 0 表示未设置自动关闭。
    """
    resp = client.get(
        client.desktop_url("/uapi/v1/course/get-timing-switch"),
        params={"course_id": str(group_id)},
    )
    if not isinstance(resp, dict) or resp.get("error_code") != 0:
        raise UMUError(
            resp.get("error_message") or "查询课程自动关闭时间失败",
            code="GET_COURSE_AUTO_CLOSE_FAILED",
        )

    data = resp.get("data", {})
    return {
        "group_id": str(group_id),
        "open_time": data.get("open_time", 0),
        "close_time": data.get("close_time", 0),
        "open_access_permission": data.get("open_access_permission", 0),
    }


@umu_operation(
    name="set_course_auto_close",
    description="设置课程自动关闭时间",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "close_time": "自动关闭时间，支持格式如 2026-06-30 10:00、2026-06-30T10:00:00、2026年6月30日10点",
        "custom_tips": "自定义提示文本；若提供则直接作为 accessPermissionTips，忽略 close_time 的默认格式化",
    },
)
async def set_course_auto_close(
    client: UMUClient,
    group_id: str,
    close_time: str,
    custom_tips: str | None = None,
) -> dict[str, Any]:
    """设置课程自动关闭时间.

    会先调用 /uapi/v1/course/set-timing-switch 设置真实的关闭时间戳，
    再保存 group_setup.accessPermissionTips 作为前端展示文案。
    """
    parsed = _parse_auto_close_time(close_time)
    close_timestamp = int(parsed.timestamp())

    resp = client.post(
        client.desktop_url("/uapi/v1/course/set-timing-switch"),
        data={
            "course_id": str(group_id),
            "open_time": "0",
            "close_time": str(close_timestamp),
        },
    )
    if not isinstance(resp, dict) or resp.get("error_code") != 0:
        raise UMUError(
            resp.get("error_message") or "设置课程自动关闭时间失败",
            code="SET_COURSE_AUTO_CLOSE_FAILED",
        )

    tips = custom_tips.strip() if custom_tips else _format_auto_close_tips(close_time)
    group_setup = {
        "accessPermissionTips": tips,
        "access_permission_tips": tips,
        "enable_mini_program": 0,
        "learn_within_mini_program": 0,
    }
    setup_resp = client.post(
        client.desktop_url("/api/group/savesetup"),
        data={
            "group_id": str(group_id),
            "group_setup": json.dumps(group_setup, ensure_ascii=False),
        },
    )
    if not isinstance(setup_resp, dict):
        raise UMUError("保存自动关闭提示文案失败", code="SET_COURSE_AUTO_CLOSE_FAILED")
    if setup_resp.get("status") not in (True, "true") and setup_resp.get("error_code") != 0:
        raise UMUError(
            setup_resp.get("error")
            or setup_resp.get("error_message")
            or "保存自动关闭提示文案失败",
            code="SET_COURSE_AUTO_CLOSE_FAILED",
        )

    return {
        "group_id": str(group_id),
        "close_time": close_timestamp,
        "access_permission_tips": tips,
    }


@umu_operation(
    name="cancel_course_auto_close",
    description="取消课程自动关闭时间",
    roles=["teacher", "admin"],
    capabilities=["course_management"],
    parameter_docs={
        "group_id": "课程 ID",
        "clear_tips": "是否同时清空自动关闭提示文案",
    },
)
async def cancel_course_auto_close(
    client: UMUClient,
    group_id: str,
    clear_tips: bool = True,
) -> dict[str, Any]:
    """取消课程自动关闭时间.

    调用 /uapi/v1/course/set-timing-switch 将 close_time 置为 0，
    默认同时清空 accessPermissionTips 提示文案。
    """
    resp = client.post(
        client.desktop_url("/uapi/v1/course/set-timing-switch"),
        data={
            "course_id": str(group_id),
            "open_time": "0",
            "close_time": "0",
        },
    )
    if not isinstance(resp, dict) or resp.get("error_code") != 0:
        raise UMUError(
            resp.get("error_message") or "取消课程自动关闭时间失败",
            code="CANCEL_COURSE_AUTO_CLOSE_FAILED",
        )

    if clear_tips:
        group_setup = {
            "accessPermissionTips": "",
            "access_permission_tips": "",
            "enable_mini_program": 0,
            "learn_within_mini_program": 0,
        }
        setup_resp = client.post(
            client.desktop_url("/api/group/savesetup"),
            data={
                "group_id": str(group_id),
                "group_setup": json.dumps(group_setup, ensure_ascii=False),
            },
        )
        if not isinstance(setup_resp, dict):
            raise UMUError("清空自动关闭提示文案失败", code="CANCEL_COURSE_AUTO_CLOSE_FAILED")
        if setup_resp.get("status") not in (True, "true") and setup_resp.get("error_code") != 0:
            raise UMUError(
                setup_resp.get("error")
                or setup_resp.get("error_message")
                or "清空自动关闭提示文案失败",
                code="CANCEL_COURSE_AUTO_CLOSE_FAILED",
            )

    return {
        "group_id": str(group_id),
        "close_time": 0,
        "cleared_tips": clear_tips,
    }
