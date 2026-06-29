"""Unified program permission Skill tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from umu_sdk.skills.builtin.program_permissions import (
    add_program_access_accounts,
    cancel_program_access_permissions,
    get_program_access_list,
    get_program_access_permission,
    remove_program_access_accounts,
    search_program_access_accounts,
    set_program_access_permission,
)


@pytest.fixture
def ctx():
    mock = AsyncMock()
    mock.call_capability_tool.return_value = {
        "success": True,
        "data": {"ok": True},
        "error_code": "",
        "error_message": "",
        "suggested_action": "",
        "next_action": "proceed",
    }
    return mock


class TestProgramPermissionsSkills:
    async def test_set_program_access_permission(self, ctx):
        result = await set_program_access_permission(ctx, "p1", 2)
        assert result["success"] is True
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="set_program_access_permission",
            arguments={"program_id": "p1", "access_permission": 2},
        )

    async def test_get_program_access_permission(self, ctx):
        await get_program_access_permission(ctx, "p1")
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="get_program_access_permission",
            arguments={"program_id": "p1"},
        )

    async def test_get_program_access_list(self, ctx):
        await get_program_access_list(ctx, "p1")
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="get_program_access_list",
            arguments={"program_id": "p1", "page": 1, "size": 20},
        )

    async def test_search_program_access_accounts(self, ctx):
        await search_program_access_accounts(ctx, "p1", "key")
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="search_program_access_accounts",
            arguments={"program_id": "p1", "keyword": "key"},
        )

    async def test_add_program_access_accounts(self, ctx):
        accounts = [{"account": "a@umu.cn", "account_type": "user", "id": "1"}]
        await add_program_access_accounts(ctx, "p1", accounts)
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="add_program_access_accounts",
            arguments={"program_id": "p1", "accounts": accounts},
        )

    async def test_remove_program_access_accounts(self, ctx):
        accounts = [{"account": "a@umu.cn", "account_type": "user", "id": "1"}]
        await remove_program_access_accounts(ctx, "p1", accounts)
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="remove_program_access_accounts",
            arguments={"program_id": "p1", "accounts": accounts},
        )

    async def test_cancel_program_access_permissions(self, ctx):
        await cancel_program_access_permissions(ctx, "p1")
        ctx.call_capability_tool.assert_awaited_once_with(
            capability="permission_management",
            operation="cancel_all_program_permissions",
            arguments={"program_id": "p1"},
        )
