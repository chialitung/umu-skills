"""Teacher 学习项目新增工具测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.core.errors import UMUError
from umu_sdk.adapters.mcp.teacher import (
    mcp as teacher_mcp,
    tch_list_program_learning_tasks,
    tch_list_program_participants,
)
from umu_sdk.tools.operations.programs import (
    add_courses_to_learning_program,
    configure_program_certificate,
    create_learning_program,
    delete_learning_program,
    get_learning_program,
    list_learning_programs,
    remove_courses_from_learning_program,
    search_courses_for_program,
    set_program_points_status,
    update_learning_program,
    update_learning_program_modules,
)


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    client.base_url = "https://www.umu.cn"
    return client


def _auth_patch():
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=MagicMock()))
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value=None))
    return stack


class TestCreateLearningProgram:
    async def test_create_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.create_program.return_value = {"program_id": "359929"}
            result = await create_learning_program(mock_client, title="新项目")
            assert result["program_id"] == "359929"
            instance.create_program.assert_called_once()

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_create_learning_program" in tools
        assert tools["tch_create_learning_program"].fn.__name__ == "tch_create_learning_program"


class TestAddCourses:
    async def test_add_courses_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.add_courses.return_value = {
                "added": [{"course_id": "1", "module_id": "197797"}],
                "failed": [],
            }
            result = await add_courses_to_learning_program(
                mock_client,
                program_id="359929",
                modules=[{"module_title": "阶段一", "course_ids": ["1"]}],
            )
            assert len(result["added"]) == 1

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_add_courses_to_learning_program" in tools


class TestConfigureCertificate:
    async def test_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.configure_certificate.return_value = {"status": 1}
            result = await configure_program_certificate(mock_client, "359929")
            assert result["status"] == 1

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_configure_program_certificate" in tools


class TestSetProgramPointsStatus:
    async def test_enable(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.set_points_status.return_value = {"status": 1}
            result = await set_program_points_status(mock_client, "359929", True)
            assert result["status"] == 1

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_set_program_points_status" in tools


class TestSearchCoursesForProgram:
    async def test_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.search_courses.return_value = ([{"obj_id": "1"}], 1)
            result = await search_courses_for_program(mock_client, "359929")
            assert result["total"] == 1
            assert result["courses"][0]["obj_id"] == "1"

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_search_courses_for_program" in tools


class TestGetLearningProgram:
    async def test_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.get_program.return_value = {"program_info": {"program_id": "359929"}}
            result = await get_learning_program(mock_client, "359929")
            assert result["program_info"]["program_id"] == "359929"

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_get_learning_program" in tools


class TestUpdateLearningProgram:
    async def test_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.update_program.return_value = {"program_id": "359929"}
            result = await update_learning_program(mock_client, "359929", title="新标题")
            assert result["program_id"] == "359929"
            instance.update_program.assert_called_once()

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_update_learning_program" in tools


class TestUpdateLearningProgramModules:
    async def test_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.update_modules.return_value = {"program_id": "359929"}
            result = await update_learning_program_modules(
                mock_client,
                "359929",
                modules=[{"module_id": "197797", "module_title": "新标题"}],
            )
            assert result["program_id"] == "359929"

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_update_learning_program_modules" in tools


class TestRemoveCoursesFromLearningProgram:
    async def test_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramBuilder") as MockBuilder:
            instance = MockBuilder.return_value
            instance.remove_courses.return_value = {"removed": ["1"], "failed": []}
            result = await remove_courses_from_learning_program(
                mock_client, "359929", ["1"]
            )
            assert result["removed"] == ["1"]

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_remove_courses_from_learning_program" in tools


class TestDeleteLearningProgramOperation:
    async def test_success(self, mock_client):
        mock_client.post.return_value = {"status": True, "error_code": 0}
        result = await delete_learning_program(mock_client, "360141")
        assert result["deleted"] is True
        mock_client.post.assert_called_once()
        call = mock_client.post.call_args
        assert "/api/program/deleteprogram" in call.args[0]
        assert call.kwargs["data"]["program_id"] == "360141"

    async def test_failure(self, mock_client):
        mock_client.post.return_value = {"status": False, "error": "无权限删除该项目"}
        with pytest.raises(RuntimeError, match="无权限删除该项目"):
            await delete_learning_program(mock_client, "360141")


class TestListLearningPrograms:
    async def test_success(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "data": {
                "list": [
                    {
                        "program_id": 1,
                        "program_title": "项目",
                        "setup": {},
                        "creator": {"umu_id": "11", "user_name": "Alice"},
                    }
                ],
                "page_info": {"list_total_num": 1},
            },
        }
        result = await list_learning_programs(
            mock_client, scope="owned", page=1, page_size=20
        )
        assert result["total"] == 1
        assert result["programs"][0]["program_id"] == "1"

    async def test_invalid_scope(self, mock_client):
        with pytest.raises(UMUError, match="不支持的 scope"):
            await list_learning_programs(mock_client, scope="invalid")

    async def test_tool_registered(self):
        tools = teacher_mcp._tool_manager._tools
        assert "tch_list_learning_programs" in tools


class TestTchListProgramParticipants:
    async def test_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramStudentManager") as MockManager:
            instance = MockManager.return_value
            instance.list_participants.return_value = {
                "summary": {"total": 1, "completed": 1, "uncompleted": 0, "completion_rate": 1.0},
                "students": [{"umu_id": "1", "user_name": "Alice"}],
                "pagination": {"total": 1, "total_pages": 1, "current_page": 1, "page_size": 20},
            }
            with _auth_patch():
                result = json.loads(await tch_list_program_participants("358416"))
            assert result["success"] is True
            assert result["data"]["students"][0]["user_name"] == "Alice"


class TestTchListProgramLearningTasks:
    async def test_success(self, mock_client):
        with patch("umu_sdk.tools.operations.programs.ProgramStudentManager") as MockManager:
            instance = MockManager.return_value
            instance.list_learning_tasks.return_value = {
                "summary": {
                    "total": 1,
                    "completed": 1,
                    "uncompleted": 0,
                    "completion_rate": 1.0,
                    "has_learning_task": True,
                },
                "students": [{"umu_id": "1", "user_name": "Alice"}],
                "pagination": {"total": 1, "total_pages": 1, "current_page": 1, "page_size": 20},
            }
            with _auth_patch():
                result = json.loads(await tch_list_program_learning_tasks("358416"))
            assert result["success"] is True
            assert result["data"]["summary"]["has_learning_task"] is True
