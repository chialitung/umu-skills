"""Teacher 学习项目 Skill 测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from umu_sdk.skills.builtin.teacher_learning_programs import create_learning_program


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
