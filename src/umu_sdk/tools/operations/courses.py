# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""课程相关共享业务操作.

目前包含学员/讲师共用的"已参与课程列表"查询，两端调用同一 UMU 端点族，
仅返回结构保持一致。
"""

from __future__ import annotations

import time
from typing import Any

from ...adapters.mcp.utils import fuzzy_filter_items
from ...core.client import UMUClient
from ...core.errors import UMUError
from ..decorators import umu_operation
from ..shared.progress import report_pagination_progress


_STATUS_MAP: dict[int, str] = {0: "all", 1: "pending", 2: "learning", 3: "completed"}


def _format_participated_course(item: dict[str, Any]) -> dict[str, Any]:
    """统一格式化已参与课程列表条目."""
    learn_status = item.get("learn_status", 0)
    finish_ratio = item.get("finish_ratio", 0)
    return {
        "group_id": str(item.get("group_id", item.get("id", ""))),
        "title": item.get("group_title", item.get("title", "")),
        "cover_url": item.get("show_pic", item.get("cover_url", item.get("cover", ""))),
        "learn_status": learn_status,
        "learn_status_label": _STATUS_MAP.get(learn_status, "unknown"),
        "status": learn_status,
        "is_finished": learn_status == 3,
        "finish_ratio": finish_ratio,
        "complete_rate": finish_ratio,
        "access_code": item.get("access_code", ""),
        "group_url": item.get("group_url", ""),
        "share_pc_url": item.get("share_pc_url", ""),
        "session_num": item.get("session_num", 0),
        "participant_time": item.get("participant_time", ""),
    }


def _is_success_response(resp: dict[str, Any]) -> bool:
    """判断 UMU 响应是否表示成功."""
    return resp.get("status") in (True, "true") or resp.get("error_code") == 0


def _build_endpoint_urls(
    client: UMUClient,
    learn_status: int,
    p: int,
    sz: int,
) -> list[str]:
    """构造按优先级排列的课程列表端点 URL."""
    timestamp = int(time.time() * 1000)
    return [
        client.desktop_url(
            f"/api/group/getmyparticipatedgrouplist?t={timestamp}"
            f"&learn_status={learn_status}&page={p}&size={sz}"
        ),
        client.desktop_url(f"/uapi/v1/course/list-my-course?page={p}&size={sz}"),
        client.desktop_url(f"/uapi/v1/course/my-courses?page={p}&size={sz}"),
    ]


def _fetch_page_with_fallback(
    client: UMUClient,
    p: int,
    sz: int,
    learn_status: int,
    preferred_endpoint: str | None = None,
) -> tuple[list[dict[str, Any]], int, str | None]:
    """获取单页课程列表，支持端点降级.

    返回 (courses, total_all, successful_endpoint)。
    如果提供了 preferred_endpoint，则只尝试该端点；否则按优先级依次尝试。
    """
    if preferred_endpoint:
        endpoints_to_try = [preferred_endpoint]
    else:
        endpoints_to_try = _build_endpoint_urls(client, learn_status, p, sz)

    last_error = ""
    for url in endpoints_to_try:
        try:
            resp = client.get(url)
        except Exception as e:
            last_error = str(e)
            continue

        if not _is_success_response(resp):
            last_error = resp.get("message", resp.get("error", "未知错误"))
            continue

        data = resp.get("data", {}) if isinstance(resp.get("data"), dict) else {}
        items = data.get("list", []) if isinstance(data, dict) else []
        courses = [_format_participated_course(c) for c in items]
        page_info = data.get("page_info", {}) if isinstance(data, dict) else {}
        total_all = int(page_info.get("list_total_num", 0) or 0)
        return courses, total_all, url

    raise RuntimeError(f"无法获取课程列表: {last_error}")


@umu_operation(
    name="list_participated_courses",
    description="获取当前用户已参与学习的课程列表",
    roles=["student", "teacher", "admin"],
    capabilities=["learning"],
    parameter_docs={
        "learn_status": "学习状态筛选：0=所有, 1=已学习, 2=学习中, 3=待学习",
        "page": "页码，从 1 开始",
        "page_size": "每页数量，默认 20，最大 100",
        "fetch_all": "是否自动获取全量数据",
        "fuzzy_title": "可选的课程标题模糊匹配关键词",
        "top_k": "模糊匹配时最多返回的候选数量",
        "similarity_threshold": "模糊匹配的最小相似度阈值（0.0 ~ 1.0）",
    },
)
async def list_participated_courses(
    client: UMUClient,
    learn_status: int = 0,
    page: int = 1,
    page_size: int = 20,
    fetch_all: bool = False,
    fuzzy_title: str | None = None,
    top_k: int = 10,
    similarity_threshold: float = 0.3,
) -> dict[str, Any]:
    """查询当前用户已参与学习的课程列表.

    Teacher 与 Student 共用同一底层实现，仅通过 @umu_operation 注册到不同角色。
    内部按优先级尝试多个端点，首次成功后复用该端点完成后续分页。
    """
    if learn_status not in _STATUS_MAP:
        raise UMUError(f"不支持的 learn_status: {learn_status}", code="INVALID_LEARN_STATUS")

    effective_fetch_all = fetch_all or bool(fuzzy_title and fuzzy_title.strip())

    if effective_fetch_all:
        batch_size = 50
        all_items: list[dict[str, Any]] = []
        total_all = 0
        current_page = 1
        successful_endpoint: str | None = None

        while True:
            page_items, total_all, successful_endpoint = _fetch_page_with_fallback(
                client,
                current_page,
                batch_size,
                learn_status,
                preferred_endpoint=successful_endpoint,
            )
            all_items.extend(page_items)

            report_pagination_progress(
                "list_participated_courses",
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
                    "list_participated_courses",
                    current_page,
                    len(all_items),
                    total_all,
                    batch_size,
                    is_safety_limit=True,
                )
                break

            current_page += 1

        result_items = all_items
        if fuzzy_title and fuzzy_title.strip():
            result_items = fuzzy_filter_items(
                all_items,
                fuzzy_title,
                key="title",
                top_k=top_k,
                similarity_threshold=similarity_threshold,
            )

        return {
            "courses": result_items,
            "filter": {
                "learn_status": learn_status,
                "learn_status_label": _STATUS_MAP.get(learn_status, "unknown"),
            },
            "pagination": {
                "total_all": total_all,
                "current_page": current_page,
                "page_size": batch_size,
            },
        }

    courses, total_all, _ = _fetch_page_with_fallback(
        client, page, page_size, learn_status
    )
    return {
        "courses": courses,
        "filter": {
            "learn_status": learn_status,
            "learn_status_label": _STATUS_MAP.get(learn_status, "unknown"),
        },
        "pagination": {
            "total": total_all,
            "total_pages": 0,
            "current_page": page,
            "page_size": page_size,
        },
    }
