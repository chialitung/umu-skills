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


class TestCollaborationToolsPresent:
    async def test_collaboration_tools_registered(self) -> None:
        from umu_sdk.adapters.mcp.teacher import mcp

        tools = await mcp.list_tools()
        tool_names = {tool.name for tool in tools}
        expected = {
            "tch_list_course_collaborators",
            "tch_search_collaborator_accounts",
            "tch_invite_course_collaborator",
            "tch_update_collaborator_role",
            "tch_remove_course_collaborator",
            "tch_transfer_course_owner",
        }
        assert expected.issubset(tool_names), f"缺少协同工具: {expected - tool_names}"


class TestTchListCourseLearningTasks:
    async def test_single_page_maps_params_and_returns_students(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_course_learning_tasks

        api_response = {
            "status": True,
            "errno": 0,
            "error_code": 0,
            "error": "success",
            "data": {
                "data_count": {
                    "total_num": 2,
                    "complete_num": 1,
                    "uncomplete_num": 1,
                    "complete_rate": 0.5,
                    "exist_learning_task": 1,
                },
                "table_body": {
                    "page_info": {
                        "list_total_num": 2,
                        "total_page_num": 1,
                        "current_page": 1,
                        "size": 20,
                    },
                    "list": [
                        {
                            "student_id": "s1",
                            "umu_id": "u1",
                            "user_name": "Alice",
                            "avatar": "https://example.com/a.jpg",
                            "task_count": 2,
                            "task_done_count": 2,
                            "complete_num": 2,
                            "complete_rate": 1,
                            "is_assign": 1,
                            "assign_time": 0,
                            "last_assign_time": 1000,
                            "complete_time": 2000,
                            "first_learning_time": 500,
                            "last_learning_time": 2000,
                            "due_time": 3000,
                        },
                    ],
                },
            },
        }

        with _patch_teacher_auth(require_auth=True) as client:
            client.get.return_value = api_response
            result = json.loads(
                await tch_list_course_learning_tasks(
                    group_id="g123",
                    status_filter="completed",
                    include_disabled=False,
                    page=1,
                    page_size=20,
                )
            )

        assert result["success"] is True
        assert result["data"]["summary"]["completed"] == 1
        assert len(result["data"]["students"]) == 1
        assert result["data"]["students"][0]["user_name"] == "Alice"
        # 验证参数映射
        call_args = client.get.call_args
        params = call_args.kwargs["params"]
        assert params["group_id"] == "g123"
        assert params["type"] == "1"
        assert params["filter_disabled_user"] == "1"

    async def test_fetch_all_merges_pages_and_reports_progress(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_course_learning_tasks

        def _student(sid: str, name: str) -> dict[str, Any]:
            return {
                "student_id": sid,
                "umu_id": f"u_{sid}",
                "user_name": name,
                "avatar": "",
                "task_count": 2,
                "task_done_count": 2,
                "complete_num": 2,
                "complete_rate": 1,
                "is_assign": 1,
                "assign_time": 0,
                "last_assign_time": 1000,
                "complete_time": 2000,
                "first_learning_time": 500,
                "last_learning_time": 2000,
                "due_time": 3000,
            }

        page1 = {
            "status": True,
            "error_code": 0,
            "data": {
                "data_count": {"total_num": 2, "complete_num": 2, "uncomplete_num": 0, "complete_rate": 1, "exist_learning_task": 1},
                "table_body": {
                    "page_info": {"list_total_num": 2, "total_page_num": 2, "current_page": 1, "size": 50},
                    "list": [_student("s1", "Alice")],
                },
            },
        }
        page2 = {
            "status": True,
            "error_code": 0,
            "data": {
                "data_count": {"total_num": 2, "complete_num": 2, "uncomplete_num": 0, "complete_rate": 1, "exist_learning_task": 1},
                "table_body": {
                    "page_info": {"list_total_num": 2, "total_page_num": 2, "current_page": 2, "size": 50},
                    "list": [_student("s2", "Bob")],
                },
            },
        }

        with _patch_teacher_auth(require_auth=True) as client:
            client.get.side_effect = [page1, page2]
            result = json.loads(await tch_list_course_learning_tasks(group_id="g1", fetch_all=True))

        assert result["success"] is True
        assert len(result["data"]["students"]) == 2
        output = capsys.readouterr().err
        assert "[tch_list_course_learning_tasks]" in output
        assert "获取完成" in output
        assert result["data"]["pagination"]["total_all"] == 2
        assert result["data"]["pagination"]["page_size"] == 50

    async def test_not_authenticated_returns_retry(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_course_learning_tasks

        with _patch_teacher_auth(require_auth=False) as client:
            with patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value="未登录"):
                result = json.loads(await tch_list_course_learning_tasks(group_id="g1"))

        assert result["success"] is False
        assert result["error_code"] == "NOT_AUTHENTICATED"
        assert result["next_action"] == "retry"
        client.get.assert_not_called()

    async def test_api_error_returns_error(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_course_learning_tasks

        with _patch_teacher_auth(require_auth=True) as client:
            client.get.return_value = {"status": False, "error": "课程不存在"}
            result = json.loads(await tch_list_course_learning_tasks(group_id="g1"))

        assert result["success"] is False
        assert result["error_code"] == "LIST_COURSE_LEARNING_TASKS_ERROR"
        assert "课程不存在" in result["error_message"]

    async def test_invalid_status_filter_returns_user_input(self) -> None:
        from umu_sdk.adapters.mcp.teacher import tch_list_course_learning_tasks

        with _patch_teacher_auth(require_auth=True) as client:
            result = json.loads(await tch_list_course_learning_tasks(group_id="g1", status_filter="done"))

        assert result["success"] is False
        assert result["error_code"] == "INVALID_STATUS_FILTER"
        assert result["next_action"] == "needs_user_input"
        client.get.assert_not_called()
