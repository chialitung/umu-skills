"""Teacher 学习项目新增工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.teacher import (
    tch_add_courses_to_learning_program,
    tch_configure_program_certificate,
    tch_create_learning_program,
    tch_search_courses_for_program,
    tch_set_program_points_status,
)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    client.base_url = "https://www.umu.cn"
    return client


def _auth_patch(mock_client):
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value=None))
    return stack


class TestTchCreateLearningProgram:
    async def test_create_success(self, mock_client):
        with patch("umu_sdk.adapters.mcp.teacher.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.create_program.return_value = {"program_id": "359929"}
            with _auth_patch(mock_client):
                result = json.loads(await tch_create_learning_program(title="新项目"))
            assert result["success"] is True
            assert result["data"]["program_id"] == "359929"
            instance.create_program.assert_called_once()


class TestTchAddCourses:
    async def test_add_courses_success(self, mock_client):
        with patch("umu_sdk.adapters.mcp.teacher.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.add_courses.return_value = {
                "added": [{"course_id": "1", "module_id": "197797"}],
                "failed": [],
            }
            with _auth_patch(mock_client):
                result = json.loads(
                    await tch_add_courses_to_learning_program(
                        program_id="359929",
                        modules=[{"module_title": "阶段一", "course_ids": ["1"]}],
                    )
                )
            assert result["success"] is True
            assert len(result["data"]["added"]) == 1


class TestTchConfigureCertificate:
    async def test_success(self, mock_client):
        with patch("umu_sdk.adapters.mcp.teacher.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.configure_certificate.return_value = {"status": 1}
            with _auth_patch(mock_client):
                result = json.loads(await tch_configure_program_certificate("359929"))
            assert result["success"] is True


class TestTchSetProgramPointsStatus:
    async def test_enable(self, mock_client):
        with patch("umu_sdk.adapters.mcp.teacher.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.set_points_status.return_value = {"status": 1}
            with _auth_patch(mock_client):
                result = json.loads(await tch_set_program_points_status("359929", True))
            assert result["success"] is True


class TestTchSearchCoursesForProgram:
    async def test_success(self, mock_client):
        with patch("umu_sdk.adapters.mcp.teacher.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.search_courses.return_value = ([{"obj_id": "1"}], 1)
            with _auth_patch(mock_client):
                result = json.loads(await tch_search_courses_for_program("359929"))
            assert result["success"] is True
