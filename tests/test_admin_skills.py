"""Tests for Admin builtin skills."""

from __future__ import annotations

import json
from typing import Any

import pytest

from umu_sdk.skills import server as skills_server
from umu_sdk.skills.mcp_client import ToolCallResult
from umu_sdk.skills.registry import SkillRegistry


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
    original_registry = skills_server._skill_registry
    skills_server._mcp_client = None
    skills_server._skill_registry = None
    yield
    skills_server._mcp_client = original_mcp
    skills_server._skill_registry = original_registry


@pytest.fixture
def registry_with_admin_skills() -> SkillRegistry:
    registry = SkillRegistry()
    registry.load_builtin_skills()
    return registry


class TestAdminOrganization:
    async def test_list_departments(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(name="list_departments", arguments={})
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("admin", "adm_list_departments", {})]

    async def test_list_groups(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_groups",
            arguments={"page": 2, "page_size": 50},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("admin", "adm_list_groups", {"page": 2, "page_size": 50})]

    async def test_list_classes(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_classes",
            arguments={"page": 1, "page_size": 20, "fetch_all": True},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("admin", "adm_list_classes", {"page": 1, "page_size": 20, "fetch_all": True}),
        ]


class TestAdminAccounts:
    async def test_list_accounts(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_accounts",
            arguments={"keywords": "张三", "role_type": 1, "fetch_all": True},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_list_accounts",
                {"keywords": "张三", "role_type": 1, "page": 1, "page_size": 500, "fetch_all": True},
            ),
        ]

    async def test_disable_account_by_umu_id(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="disable_account",
            arguments={"umu_id": "u-123", "effective_time": "immediate"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("admin", "adm_disable_account", {"umu_id": "u-123", "effective_time": "immediate"}),
        ]

    async def test_disable_account_missing_identifier(
        self, registry_with_admin_skills: SkillRegistry
    ) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(name="disable_account", arguments={})
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "MISSING_IDENTIFIER"
        assert mock_mcp.calls == []

    async def test_enable_account_by_email(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="enable_account",
            arguments={"email": "test@umu.cn"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("admin", "adm_enable_account", {"email": "test@umu.cn"})]

    async def test_enable_account_missing_identifier(
        self, registry_with_admin_skills: SkillRegistry
    ) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(name="enable_account", arguments={})
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "MISSING_IDENTIFIER"
        assert mock_mcp.calls == []


class TestAdminData:
    async def test_get_learning_records(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="get_learning_records",
            arguments={
                "start_day": "2026-06-01",
                "end_day": "2026-06-12",
                "student_keywords": "张三",
                "course_title": "入职培训",
                "group_ids": "1,2",
                "fetch_all": True,
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_list_learning_records",
                {
                    "start_day": "2026-06-01",
                    "end_day": "2026-06-12",
                    "student_keywords": "张三",
                    "course_title": "入职培训",
                    "group_ids": "1,2",
                    "page": 1,
                    "page_size": 20,
                    "fetch_all": True,
                },
            ),
        ]

    async def test_get_learning_records_minimal(
        self, registry_with_admin_skills: SkillRegistry
    ) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(name="get_learning_records", arguments={})
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("admin", "adm_list_learning_records", {"page": 1, "page_size": 20, "fetch_all": False}),
        ]
