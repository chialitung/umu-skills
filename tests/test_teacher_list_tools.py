"""Teacher MCP 列表工具 fetch_all 分页测试."""

from __future__ import annotations

import json
from contextlib import ExitStack, contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


@contextmanager
def _patch_teacher_auth(require_auth: bool = False):
    """Patch teacher.py 的客户端与认证依赖."""
    client = MagicMock()
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"

    stack = [
        patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=client),
    ]
    if require_auth:
        stack.append(patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value=None))

    with ExitStack() as exit_stack:
        for cm in stack:
            exit_stack.enter_context(cm)
        yield client


def _resource_page(
    page: int,
    size: int,
    total: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造资源类列表接口（tch_list_resources/documents/audio_videos）的分页响应."""
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


def _course_page(
    page: int,
    size: int,
    total: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """构造课程类列表接口的分页响应."""
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
            "list": [{"groupInfo": item} for item in items],
        },
    }


class TestTchListResources:
    async def test_fetch_all_two_pages_reports_progress(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_resources

        page1 = _resource_page(
            1,
            50,
            2,
            [{"id": "1", "file_name": "a.zip", "file_size": 100, "media_type": "videoweike"}],
        )
        page2 = _resource_page(
            2,
            50,
            2,
            [{"id": "2", "file_name": "b.zip", "file_size": 200, "media_type": "videoweike"}],
        )

        with _patch_teacher_auth() as client:
            client.get.side_effect = [page1, page2]
            result = json.loads(await tch_list_resources(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["resources"]) == 2
        output = capsys.readouterr().err
        assert "[tch_list_resources]" in output
        assert "共 2 条" in output
        assert "已获取第 1 页" in output
        assert "已获取第 2 页" in output
        assert "获取完成" in output

    async def test_single_page_no_progress(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_resources

        resp = _resource_page(
            1,
            15,
            1,
            [{"id": "1", "file_name": "a.zip", "file_size": 100, "media_type": "videoweike"}],
        )

        with _patch_teacher_auth() as client:
            client.get.return_value = resp
            result = json.loads(await tch_list_resources())

        assert result["success"] is True
        assert "[tch_list_resources]" not in capsys.readouterr().err


class TestTchListDocuments:
    async def test_fetch_all_two_pages(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_documents

        page1 = _resource_page(1, 50, 2, [{"id": "1", "file_name": "a.pdf", "file_size": 100}])
        page2 = _resource_page(2, 50, 2, [{"id": "2", "file_name": "b.pdf", "file_size": 200}])

        with _patch_teacher_auth() as client:
            client.get.side_effect = [page1, page2]
            result = json.loads(await tch_list_documents(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["documents"]) == 2
        output = capsys.readouterr().err
        assert "[tch_list_documents]" in output
        assert "获取完成" in output


class TestTchListAudioVideos:
    async def test_fetch_all_two_pages(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_audio_videos

        page1 = _resource_page(1, 50, 2, [{"id": "1", "file_name": "a.mp4", "file_size": 100}])
        page2 = _resource_page(2, 50, 2, [{"id": "2", "file_name": "b.mp4", "file_size": 200}])

        with _patch_teacher_auth() as client:
            client.get.side_effect = [page1, page2]
            result = json.loads(await tch_list_audio_videos(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["videos"]) == 2
        output = capsys.readouterr().err
        assert "[tch_list_audio_videos]" in output
        assert "获取完成" in output


class TestTchListCreatedCourses:
    async def test_fetch_all_two_pages(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_created_courses

        page1 = _course_page(1, 50, 2, [{"id": "g1", "title": "Course 1"}])
        page2 = _course_page(2, 50, 2, [{"id": "g2", "title": "Course 2"}])

        with _patch_teacher_auth(require_auth=True) as client:
            client.get.side_effect = [page1, page2]
            result = json.loads(await tch_list_created_courses(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["courses"]) == 2
        output = capsys.readouterr().err
        assert "[tch_list_created_courses]" in output
        assert "获取完成" in output


class TestTchListCooperatedCourses:
    async def test_fetch_all_two_pages(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_cooperated_courses

        page1 = _course_page(1, 50, 2, [{"id": "g1", "title": "Course 1"}])
        page2 = _course_page(2, 50, 2, [{"id": "g2", "title": "Course 2"}])

        with _patch_teacher_auth(require_auth=True) as client:
            client.get.side_effect = [page1, page2]
            result = json.loads(await tch_list_cooperated_courses(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["courses"]) == 2
        output = capsys.readouterr().err
        assert "[tch_list_cooperated_courses]" in output
        assert "获取完成" in output


class TestTchListParticipatedCourses:
    async def test_fetch_all_two_pages(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_participated_courses

        page1 = _course_page(1, 50, 2, [{"id": "g1", "title": "Course 1"}])
        page2 = _course_page(2, 50, 2, [{"id": "g2", "title": "Course 2"}])

        with _patch_teacher_auth(require_auth=True) as client:
            client.get.side_effect = [page1, page2]
            result = json.loads(await tch_list_participated_courses(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["courses"]) == 2
        output = capsys.readouterr().err
        assert "[tch_list_participated_courses]" in output
        assert "获取完成" in output


class TestTchFetchAllSafetyLimit:
    async def test_resources_stops_at_50_pages(self, capsys: pytest.CaptureFixture[str]) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_resources

        pages = [
            _resource_page(
                i,
                50,
                5000,
                [{"id": str(i), "file_name": f"f{i}.zip", "file_size": i}],
            )
            for i in range(1, 52)
        ]

        with _patch_teacher_auth() as client:
            client.get.side_effect = pages
            result = json.loads(await tch_list_resources(fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["resources"]) == 50
        assert "50 页安全上限" in capsys.readouterr().err
