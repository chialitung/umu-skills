"""Teacher 学习项目 Skill 测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from umu_sdk.skills.builtin.teacher_learning_programs import (
    create_learning_program,
    list_program_learning_tasks,
    list_program_participants,
    update_learning_program,
)


@pytest.fixture
def ctx():
    context = MagicMock()
    context.call_tool = AsyncMock()
    context.logger = MagicMock()
    return context


class TestCreateLearningProgram:
    async def test_success_with_explicit_modules(self, ctx):
        ctx.call_tool.side_effect = [
            {
                "success": True,
                "data": {"program_id": "359929"},
                "error_code": "",
                "error_message": "",
            },
            {
                "success": True,
                "data": {"added": [{"course_id": "1"}], "failed": []},
                "error_code": "",
                "error_message": "",
            },
        ]
        result = await create_learning_program(
            ctx,
            title="新项目",
            modules=[{"module_title": "阶段一", "course_ids": ["1"]}],
        )
        assert result["success"] is True
        assert result["data"]["program_id"] == "359929"

    async def test_success_with_flat_course_ids(self, ctx):
        ctx.call_tool.side_effect = [
            {
                "success": True,
                "data": {"program_id": "359929"},
                "error_code": "",
                "error_message": "",
            },
            {
                "success": True,
                "data": {"added": [{"course_id": "1"}], "failed": []},
                "error_code": "",
                "error_message": "",
            },
        ]
        result = await create_learning_program(ctx, title="新项目", course_ids=["1"])
        assert result["success"] is True
        _, add_call = ctx.call_tool.call_args_list
        assert add_call.kwargs["arguments"]["modules"][0]["module_title"] == "必修课程"

    async def test_create_failure(self, ctx):
        ctx.call_tool.return_value = {
            "success": False,
            "data": None,
            "error_code": "FAILED",
            "error_message": "登录已过期",
        }
        result = await create_learning_program(ctx, title="新项目")
        assert result["success"] is False
        assert result["error_code"] == "FAILED"


class TestUpdateLearningProgram:
    async def test_update_basic_success(self, ctx):
        ctx.call_tool.return_value = {
            "success": True,
            "data": {"program_id": "359929"},
            "error_code": "",
            "error_message": "",
        }
        result = await update_learning_program(ctx, program_id="359929", title="新标题")
        assert result["success"] is True
        assert result["data"]["program_id"] == "359929"
        _, kwargs = ctx.call_tool.call_args
        assert kwargs["tool"] == "tch_update_learning_program"
        assert kwargs["arguments"]["title"] == "新标题"

    async def test_update_with_modules(self, ctx):
        ctx.call_tool.side_effect = [
            {
                "success": True,
                "data": {"removed": ["1791558"], "failed": []},
                "error_code": "",
                "error_message": "",
            },
            {
                "success": True,
                "data": {"program_id": "359929"},
                "error_code": "",
                "error_message": "",
            },
            {
                "success": True,
                "data": {"program_id": "359929"},
                "error_code": "",
                "error_message": "",
            },
        ]
        result = await update_learning_program(
            ctx,
            program_id="359929",
            title="新标题",
            modules=[{"module_id": "197797", "module_title": "阶段一改名"}],
            remove_module_group_ids=["1791558"],
        )
        assert result["success"] is True
        assert len(ctx.call_tool.call_args_list) == 3

    async def test_update_failure(self, ctx):
        ctx.call_tool.return_value = {
            "success": False,
            "data": None,
            "error_code": "FAILED",
            "error_message": "项目不存在",
        }
        result = await update_learning_program(ctx, program_id="359929", title="新标题")
        assert result["success"] is False
        assert result["error_code"] == "FAILED"


class TestListProgramParticipants:
    async def test_success(self, ctx):
        ctx.call_tool.return_value = {
            "success": True,
            "data": {
                "summary": {"total": 1, "completed": 1, "uncompleted": 0, "completion_rate": 1.0},
                "students": [{"umu_id": "1", "user_name": "Alice"}],
                "pagination": {"total": 1, "total_pages": 1, "current_page": 1, "page_size": 20},
            },
            "error_code": "",
            "error_message": "",
        }
        result = await list_program_participants(ctx, program_id="358416", status_filter="completed")
        assert result["success"] is True
        assert result["data"]["students"][0]["user_name"] == "Alice"
        _, kwargs = ctx.call_tool.call_args
        assert kwargs["tool"] == "tch_list_program_participants"
        assert kwargs["arguments"]["status_filter"] == "completed"

    async def test_failure(self, ctx):
        ctx.call_tool.return_value = {
            "success": False,
            "data": None,
            "error_code": "FAILED",
            "error_message": "项目不存在",
        }
        result = await list_program_participants(ctx, program_id="358416")
        assert result["success"] is False
        assert result["error_code"] == "FAILED"


class TestListProgramLearningTasks:
    async def test_success(self, ctx):
        ctx.call_tool.return_value = {
            "success": True,
            "data": {
                "summary": {
                    "total": 1,
                    "completed": 1,
                    "uncompleted": 0,
                    "completion_rate": 1.0,
                    "has_learning_task": True,
                },
                "students": [{"umu_id": "1", "user_name": "Alice"}],
                "pagination": {"total": 1, "total_pages": 1, "current_page": 1, "page_size": 20},
            },
            "error_code": "",
            "error_message": "",
        }
        result = await list_program_learning_tasks(ctx, program_id="358416", include_disabled=False)
        assert result["success"] is True
        assert result["data"]["summary"]["has_learning_task"] is True
        _, kwargs = ctx.call_tool.call_args
        assert kwargs["tool"] == "tch_list_program_learning_tasks"
        assert kwargs["arguments"]["include_disabled"] is False

    async def test_failure(self, ctx):
        ctx.call_tool.return_value = {
            "success": False,
            "data": None,
            "error_code": "FAILED",
            "error_message": "无权限",
        }
        result = await list_program_learning_tasks(ctx, program_id="358416")
        assert result["success"] is False
        assert result["error_code"] == "FAILED"
