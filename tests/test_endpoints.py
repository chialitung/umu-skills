"""Endpoints 测试."""

from __future__ import annotations

import sys
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.endpoints.courses import CourseEndpoint, ListCoursesParams
from umu_sdk.core.models import PaginatedResponse


class TestCourseEndpointIterateAll:
    def test_iterate_all_reports_progress(self) -> None:
        endpoint = CourseEndpoint(http=MagicMock())
        endpoint.list = MagicMock(
            side_effect=[
                PaginatedResponse(
                    data=[
                        {
                            "id": "1",
                            "title": "Course 1",
                            "createdAt": "2024-01-01T00:00:00Z",
                            "updatedAt": "2024-01-01T00:00:00Z",
                        }
                    ],
                    total=2,
                    page=1,
                    page_size=1,
                    total_pages=2,
                ),
                PaginatedResponse(
                    data=[
                        {
                            "id": "2",
                            "title": "Course 2",
                            "createdAt": "2024-01-02T00:00:00Z",
                            "updatedAt": "2024-01-02T00:00:00Z",
                        }
                    ],
                    total=2,
                    page=2,
                    page_size=1,
                    total_pages=2,
                ),
            ]
        )

        stderr = StringIO()
        with patch.object(sys, "stderr", stderr):
            result = endpoint.iterate_all(page_size=1)

        assert len(result) == 2
        output = stderr.getvalue()
        assert "[CourseEndpoint.iterate_all]" in output
        assert "共 2 条" in output
        assert "预计 2 页" in output
        assert "已获取第 1 页" in output
        assert "已获取第 2 页" in output
        assert "获取完成" in output

    def test_iterate_all_respects_50_page_safety_limit(self) -> None:
        endpoint = CourseEndpoint(http=MagicMock())
        endpoint.list = MagicMock(
            side_effect=[
                PaginatedResponse(
                    data=[
                        {
                            "id": str(i),
                            "title": f"Course {i}",
                            "createdAt": "2024-01-01T00:00:00Z",
                            "updatedAt": "2024-01-01T00:00:00Z",
                        }
                    ],
                    total=100,
                    page=i,
                    page_size=1,
                    total_pages=100,
                )
                for i in range(1, 52)
            ]
        )

        stderr = StringIO()
        with patch.object(sys, "stderr", stderr):
            result = endpoint.iterate_all(page_size=1)

        assert len(result) == 50
        output = stderr.getvalue()
        assert "50 页安全上限" in output

    def test_iterate_all_with_params(self) -> None:
        endpoint = CourseEndpoint(http=MagicMock())
        endpoint.list = MagicMock(
            side_effect=[
                PaginatedResponse(
                    data=[
                        {
                            "id": "1",
                            "title": "Course 1",
                            "createdAt": "2024-01-01T00:00:00Z",
                            "updatedAt": "2024-01-01T00:00:00Z",
                        }
                    ],
                    total=1,
                    page=1,
                    page_size=100,
                    total_pages=1,
                ),
            ]
        )

        stderr = StringIO()
        params = ListCoursesParams(search="test")
        with patch.object(sys, "stderr", stderr):
            result = endpoint.iterate_all(params=params)

        assert len(result) == 1
        # 调用 list 时应覆盖 page/page_size
        call_args = endpoint.list.call_args[0][0]
        assert call_args.page == 1
        assert call_args.page_size == 100
        assert call_args.search == "test"
