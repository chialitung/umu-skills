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

    async def test_get_department_tree(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="get_department_tree",
            arguments={"fetch_all": True},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("admin", "adm_get_department_tree", {"fetch_all": True})]

    async def test_get_department(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="get_department",
            arguments={"department_id": "251103"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("admin", "adm_get_department", {"department_id": "251103"})]

    async def test_get_child_departments(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="get_child_departments",
            arguments={"department_id": "251103"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("admin", "adm_get_child_departments", {"department_id": "251103"}),
        ]

    async def test_list_department_members(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_department_members",
            arguments={"department_id": "251103", "fetch_all": True},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_list_department_members",
                {
                    "department_id": "251103",
                    "page": 1,
                    "page_size": 15,
                    "fetch_all": True,
                },
            ),
        ]

    async def test_search_department_members(
        self, registry_with_admin_skills: SkillRegistry
    ) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="search_department_members",
            arguments={"department_id": "251103", "keywords": "张三"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_search_department_members",
                {
                    "department_id": "251103",
                    "keywords": "张三",
                    "page": 1,
                    "page_size": 20,
                },
            ),
        ]

    async def test_create_department(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="create_department",
            arguments={"department_name": "华东区", "parent_department_id": "251103"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_create_department",
                {"department_name": "华东区", "parent_department_id": "251103"},
            ),
        ]

    async def test_update_department(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="update_department",
            arguments={
                "department_id": "251103",
                "department_name": "新名称",
                "manager_umu_ids": "20458616",
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_update_department",
                {
                    "department_id": "251103",
                    "department_name": "新名称",
                    "manager_umu_ids": "20458616",
                },
            ),
        ]

    async def test_sort_departments(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        order = '[{"department_id":"251103","index":1},{"department_id":"251104","index":2}]'
        result = await skills_server.skill_run(
            name="sort_departments",
            arguments={"department_orders": order},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("admin", "adm_sort_departments", {"department_orders": order})]

    async def test_add_department_members(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="add_department_members",
            arguments={"department_id": "251103", "umu_ids": "20439812,20439813"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_add_department_members",
                {"department_id": "251103", "umu_ids": "20439812,20439813"},
            ),
        ]

    async def test_move_department_members(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="move_department_members",
            arguments={"umu_ids": "20439812", "department_ids": "251104"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_move_department_members",
                {"umu_ids": "20439812", "department_ids": "251104"},
            ),
        ]

    async def test_remove_department_members(
        self, registry_with_admin_skills: SkillRegistry
    ) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="remove_department_members",
            arguments={"member_ids": "327926038"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("admin", "adm_remove_department_members", {"member_ids": "327926038"}),
        ]

    async def test_delete_departments(self, registry_with_admin_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_admin_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="delete_departments",
            arguments={"department_ids": "251105"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("admin", "adm_delete_departments", {"department_ids": "251105"}),
        ]

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
