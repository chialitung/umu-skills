"""Student MCP 列表工具 fetch_all 分页测试."""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@contextmanager
def _patch_student_auth(require_auth: bool = False):
    """Patch student.py 的客户端与认证依赖."""
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    client.mobile_url.side_effect = lambda path: f"https://m.umu.cn{path}"

    stack = [
        patch("umu_sdk.adapters.mcp.student._umu_client", client),
    ]
    if require_auth:
        stack.append(patch("umu_sdk.adapters.mcp.student._require_auth", return_value=None))

    with ExitStack() as exit_stack:
        for cm in stack:
            exit_stack.enter_context(cm)
        yield client


def _participated_course_page(
    page: int,
    size: int,
    total: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造 stu_list_participated_courses 的分页响应."""
    return {
        "status": True,
        "error_code": 0,
        "data": {
            "page_info": {
                "list_total_num": total,
                "total_page_num": max(1, (total + size - 1) // size),
                "current_page": page,
                "size": size,
            },
            "list": items,
        },
    }


def _fallback_course_page(
    page: int,
    size: int,
    total: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造备用端点的分页响应（无 status 字段，仅 error_code=0）."""
    return {
        "error_code": 0,
        "data": {
            "page_info": {
                "list_total_num": total,
                "total_page_num": max(1, (total + size - 1) // size),
                "current_page": page,
                "size": size,
            },
            "list": items,
        },
    }


class TestStuListParticipatedCourses:
    async def test_fetch_all_two_pages_reuses_successful_endpoint(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from umu_sdk.adapters.mcp.student import stu_list_participated_courses

        page1 = _participated_course_page(1, 50, 2, [{"group_id": "g1", "group_title": "Course 1"}])
        page2 = _participated_course_page(2, 50, 2, [{"group_id": "g2", "group_title": "Course 2"}])

        with _patch_student_auth(require_auth=True) as client:
            # 第一个端点成功，后续复用同一端点
            client.get.side_effect = [page1, page2]
            result = json.loads(await stu_list_participated_courses(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["courses"]) == 2
        output = capsys.readouterr().err
        assert "[list_participated_courses]" in output
        assert "共 2 条" in output
        assert "获取完成" in output
        # 确认后续请求复用了第一次成功的 endpoint
        assert client.get.call_count == 2

    async def test_fetch_all_uses_fallback_endpoint(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from umu_sdk.adapters.mcp.student import stu_list_participated_courses

        page = _fallback_course_page(1, 50, 1, [{"group_id": "g1", "title": "Course 1"}])

        with _patch_student_auth(require_auth=True) as client:
            client.get.side_effect = [
                {"error_code": 1, "message": "fail"},
                {"error_code": 1, "message": "fail"},
                page,
            ]
            result = json.loads(await stu_list_participated_courses(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["courses"]) == 1
        assert client.get.call_count == 3

    async def test_single_page_no_progress(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.student import stu_list_participated_courses

        resp = _participated_course_page(1, 20, 1, [{"group_id": "g1", "group_title": "Course 1"}])

        with _patch_student_auth(require_auth=True) as client:
            client.get.return_value = resp
            result = json.loads(await stu_list_participated_courses())

        assert result["success"] is True
        assert "[list_participated_courses]" not in capsys.readouterr().err

    async def test_safety_limit_stops_at_50_pages(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.student import stu_list_participated_courses

        pages = [
            _participated_course_page(
                i,
                50,
                5000,
                [{"group_id": str(i), "group_title": f"Course {i}"}],
            )
            for i in range(1, 52)
        ]

        with _patch_student_auth(require_auth=True) as client:
            client.get.side_effect = pages
            result = json.loads(await stu_list_participated_courses(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["courses"]) == 50
        assert "50 页安全上限" in capsys.readouterr().err
