"""Teacher 学习项目 Skill 测试."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from umu_sdk.skills.builtin.teacher_learning_programs import (
    list_owned_learning_programs,
    set_program_access_permission,
)


@pytest.fixture
def ctx():
    mock = AsyncMock()
    mock.call_tool.return_value = {
        "success": True,
        "data": {"programs": [{"program_id": "359923"}]},
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }
    return mock


class TestTeacherLearningProgramSkills:
    async def test_list_owned_learning_programs(self, ctx):
        result = await list_owned_learning_programs(ctx)
        assert result["success"] is True
        ctx.call_tool.assert_awaited_once()
        call = ctx.call_tool.call_args
        assert call.kwargs["server"] == "teacher"
        assert call.kwargs["tool"] == "tch_list_learning_programs"
        assert call.kwargs["arguments"]["scope"] == "owned"

    async def test_set_program_access_permission(self, ctx):
        ctx.call_tool.return_value = {
            "success": True,
            "data": {"program_id": "359923", "access_permission": 2},
            "error_code": "",
            "error_message": "",
            "suggested_action": "",
            "next_action": "proceed",
        }
        result = await set_program_access_permission(ctx, "359923", 2)
        assert result["success"] is True
        call = ctx.call_tool.call_args
        assert call.kwargs["tool"] == "tch_set_program_access_permission"
        assert call.kwargs["arguments"]["access_permission"] == 2
