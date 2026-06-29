"""Unified course permission Skill tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from umu_sdk.skills.builtin.course_permissions import (
    add_course_access_accounts,
    cancel_course_access_permissions,
    cancel_course_auto_close,
    get_course_access_list,
    get_course_access_permission,
    get_course_auto_close,
    remove_course_access_accounts,
    search_course_access_accounts,
    set_course_access_permission,
    set_course_auto_close,
)


@pytest.fixture
def ctx():
    mock = AsyncMock()
    default_response = {
        "success": True,
        "data": {"ok": True},
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }
    mock.call_capability_tool.return_value = default_response
    mock.call_role_tool.return_value = default_response
    return mock


class TestCoursePermissionsSkills:
    async def test_set_course_access_permission(self, ctx):
        result = await set_course_access_permission(ctx, "g1", 2)
        assert result["success"] is True
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="set_course_access_permission",
            arguments={"group_id": "g1", "access_permission": 2},
        )

    async def test_search_course_access_accounts(self, ctx):
        await search_course_access_accounts(ctx, "g1", "key")
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="search_access_accounts",
            arguments={"group_id": "g1", "keyword": "key"},
        )

    async def test_add_course_access_accounts(self, ctx):
        accounts = [{"account": "a@umu.cn", "account_type": "user", "id": "1"}]
        await add_course_access_accounts(ctx, "g1", accounts)
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="add_course_access_accounts",
            arguments={"group_id": "g1", "accounts": accounts},
        )

    async def test_remove_course_access_accounts(self, ctx):
        accounts = [{"account": "a@umu.cn", "account_type": "user", "id": "1"}]
        await remove_course_access_accounts(ctx, "g1", accounts)
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="remove_course_access_accounts",
            arguments={"group_id": "g1", "accounts": accounts},
        )

    async def test_cancel_course_access_permissions(self, ctx):
        await cancel_course_access_permissions(ctx, "g1")
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="cancel_all_assigned_permissions",
            arguments={"group_id": "g1"},
        )

    async def test_get_course_access_permission(self, ctx):
        await get_course_access_permission(ctx, "g1")
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="get_course_access_permission",
            arguments={"group_id": "g1"},
        )

    async def test_get_course_access_list(self, ctx):
        await get_course_access_list(ctx, "g1")
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="get_course_access_list",
            arguments={"group_id": "g1", "page": 1, "size": 20},
        )

    async def test_get_course_auto_close(self, ctx):
        result = await get_course_auto_close(ctx, "g1")
        assert result["success"] is True
        ctx.call_role_tool.assert_awaited_once_with(
            role="teacher",
            operation="get_course_auto_close",
            arguments={"group_id": "g1"},
        )

    async def test_set_course_auto_close(self, ctx):
        result = await set_course_auto_close(ctx, "g1", "2026-06-30 10:00")
        assert result["success"] is True
        calls = ctx.call_role_tool.call_args_list
        assert calls[0].kwargs == {
            "role": "teacher",
            "operation": "get_course_auto_close",
            "arguments": {"group_id": "g1"},
        }
        assert calls[1].kwargs == {
            "role": "teacher",
            "operation": "set_course_auto_close",
            "arguments": {"group_id": "g1", "close_time": "2026-06-30 10:00"},
        }

    async def test_cancel_course_auto_close(self, ctx):
        result = await cancel_course_auto_close(ctx, "g1")
        assert result["success"] is True
        calls = ctx.call_role_tool.call_args_list
        assert calls[0].kwargs == {
            "role": "teacher",
            "operation": "get_course_auto_close",
            "arguments": {"group_id": "g1"},
        }
        assert calls[1].kwargs == {
            "role": "teacher",
            "operation": "cancel_course_auto_close",
            "arguments": {"group_id": "g1", "clear_tips": True},
        }
