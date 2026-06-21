"""Orchestrator 对 Admin 已删除工具的向后兼容重定向测试."""

from __future__ import annotations

import json
from typing import Any

import pytest

from umu_sdk.skills import server as skills_server
from umu_sdk.skills.mcp_client import ToolCallResult


class MockMCPClientManager:
    """不启动真实子进程的 MCPClientManager 模拟."""

    def __init__(self, responses: dict[tuple[str, str], dict[str, Any]] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def list_servers(self) -> list[str]:
        return ["teacher", "student", "admin"]

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> ToolCallResult:
        self.calls.append((server, tool, arguments))
        key = (server, tool)
        response = self.responses.get(key, {"success": True, "data": {"mock": True}})
        return ToolCallResult(
            success=response.get("success", True),
            data=response.get("data"),
            error_code=response.get("error_code", ""),
            error_message=response.get("error_message", ""),
        )


@pytest.fixture(autouse=True)
def reset_globals():
    """每个测试前重置 server 全局变量."""
    original_mcp = skills_server._mcp_client
    skills_server._mcp_client = None
    yield
    skills_server._mcp_client = original_mcp


class TestAdminToTeacherToolRedirect:
    """验证每个遗留 adm_* 工具都被正确转发到 tch_* 并附加弃用提示."""

    @pytest.mark.parametrize(
        "legacy_tool,expected_server,expected_tool",
        [
            (legacy, target, tool)
            for legacy, (target, tool) in skills_server._ADMIN_TO_TEACHER_TOOL_MAP.items()
        ],
    )
    async def test_redirect_maps_to_teacher_tool(
        self,
        legacy_tool: str,
        expected_server: str,
        expected_tool: str,
    ) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_call_atomic_tool(
            server="admin",
            tool=legacy_tool,
            arguments={"group_id": "123"},
        )
        parsed = json.loads(result)

        assert parsed["success"] is True
        assert mock_mcp.calls == [(expected_server, expected_tool, {"group_id": "123"})]
        assert parsed.get("deprecated") is True
        assert expected_tool in (parsed.get("migration_note") or "")

    async def test_non_legacy_admin_tool_no_redirect(self) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_call_atomic_tool(
            server="admin",
            tool="adm_list_accounts",
            arguments={},
        )
        parsed = json.loads(result)

        assert parsed["success"] is True
        assert mock_mcp.calls == [("admin", "adm_list_accounts", {})]
        assert "deprecated" not in parsed

    async def test_redirect_target_unavailable_returns_error(self) -> None:
        class AdminOnlyMCP(MockMCPClientManager):
            def list_servers(self) -> list[str]:
                return ["admin"]

        mock_mcp = AdminOnlyMCP()
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_call_atomic_tool(
            server="admin",
            tool="adm_set_course_access_permission",
            arguments={"group_id": "123", "access_permission": 2},
        )
        parsed = json.loads(result)

        assert parsed["success"] is False
        assert parsed["error_code"] == "SERVER_UNAVAILABLE"
        assert "teacher" in parsed["error_message"]
        assert mock_mcp.calls == []

    async def test_redirect_preserves_error_response(self) -> None:
        mock_mcp = MockMCPClientManager(
            responses={
                ("teacher", "tch_set_course_access_permission"): {
                    "success": False,
                    "error_code": "AUTH_FAILED",
                    "error_message": "未登录",
                },
            }
        )
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_call_atomic_tool(
            server="admin",
            tool="adm_set_course_access_permission",
            arguments={"group_id": "123", "access_permission": 2},
        )
        parsed = json.loads(result)

        assert parsed["success"] is False
        assert parsed["error_code"] == "AUTH_FAILED"
        assert parsed.get("deprecated") is True
        assert mock_mcp.calls == [
            ("teacher", "tch_set_course_access_permission", {"group_id": "123", "access_permission": 2}),
        ]
