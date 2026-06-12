"""Tests for skills.server orchestrator tools."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from umu_sdk.skills import server as skills_server
from umu_sdk.skills.decorators import SkillContext, skill
from umu_sdk.skills.mcp_client import MCPClientManager, ToolCallResult
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


@pytest.fixture
def mock_registry() -> SkillRegistry:
    registry = SkillRegistry()

    @skill(name="test_double", description="Double a number", required_servers=["teacher"])
    async def test_double(ctx: SkillContext, value: int) -> dict[str, Any]:
        return {
            "success": True,
            "data": {"result": value * 2},
        }

    registry.register_function(test_double)
    return registry


@pytest.fixture
def mock_mcp() -> MockMCPClientManager:
    return MockMCPClientManager()


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


class TestSkillList:
    async def test_returns_skills(self, mock_registry: SkillRegistry) -> None:
        skills_server._skill_registry = mock_registry
        result = await skills_server.skill_list()
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert len(parsed["data"]) == 1
        assert parsed["data"][0]["name"] == "test_double"

    async def test_registry_not_ready(self) -> None:
        result = await skills_server.skill_list()
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "REGISTRY_NOT_READY"


class TestSkillDescribe:
    async def test_describe_existing(self, mock_registry: SkillRegistry) -> None:
        skills_server._skill_registry = mock_registry
        result = await skills_server.skill_describe(name="test_double")
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["name"] == "test_double"
        assert any(p["name"] == "value" for p in parsed["data"]["parameters"])

    async def test_describe_not_found(self, mock_registry: SkillRegistry) -> None:
        skills_server._skill_registry = mock_registry
        result = await skills_server.skill_describe(name="missing")
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "SKILL_NOT_FOUND"


class TestSkillRun:
    async def test_run_skill_success(
        self,
        mock_registry: SkillRegistry,
        mock_mcp: MockMCPClientManager,
    ) -> None:
        skills_server._skill_registry = mock_registry
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="test_double",
            arguments={"value": 21},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["result"] == 42

    async def test_run_skill_missing_server(
        self,
        mock_registry: SkillRegistry,
    ) -> None:
        skills_server._skill_registry = mock_registry
        skills_server._mcp_client = MockMCPClientManager()
        # 覆盖 list_servers 使其不包含 teacher
        skills_server._mcp_client.list_servers = lambda: ["student"]  # type: ignore[method-assign]

        result = await skills_server.skill_run(
            name="test_double",
            arguments={"value": 21},
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "SERVER_UNAVAILABLE"

    async def test_run_skill_not_found(
        self,
        mock_mcp: MockMCPClientManager,
    ) -> None:
        skills_server._skill_registry = SkillRegistry()
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(name="missing", arguments={})
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "SKILL_NOT_FOUND"

    async def test_run_skill_invalid_arguments(
        self,
        mock_registry: SkillRegistry,
        mock_mcp: MockMCPClientManager,
    ) -> None:
        skills_server._skill_registry = mock_registry
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="test_double",
            arguments={"wrong_param": 10},
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "INVALID_ARGUMENTS"

    async def test_run_builtin_create_course(self) -> None:
        responses = {
            ("teacher", "tch_create_course"): {
                "success": True,
                "data": {"group_id": "mock-group-id"},
            },
            ("teacher", "tch_create_scorm_section"): {
                "success": True,
                "data": {"section_id": "mock-section-id"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        registry = SkillRegistry()
        registry.load_builtin_skills()
        skills_server._skill_registry = registry
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="create_course_with_scorm",
            arguments={
                "title": "测试课程",
                "scorm_resource_id": "res-123",
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["group_id"] == "mock-group-id"


class TestSkillCallAtomicTool:
    async def test_call_atomic_tool_success(self, mock_mcp: MockMCPClientManager) -> None:
        skills_server._mcp_client = mock_mcp
        result = await skills_server.skill_call_atomic_tool(
            server="teacher",
            tool="tch_get_categories",
            arguments={},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"] == {"mock": True}
        assert mock_mcp.calls == [("teacher", "tch_get_categories", {})]

    async def test_call_atomic_tool_server_unavailable(
        self, mock_mcp: MockMCPClientManager
    ) -> None:
        mock_mcp.list_servers = lambda: ["student"]  # type: ignore[method-assign]
        skills_server._mcp_client = mock_mcp
        result = await skills_server.skill_call_atomic_tool(
            server="teacher",
            tool="tch_get_categories",
            arguments={},
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "SERVER_UNAVAILABLE"

    async def test_call_atomic_tool_failure(self, mock_mcp: MockMCPClientManager) -> None:
        responses = {
            ("teacher", "tch_delete_section"): {
                "success": False,
                "error_code": "SECTION_NOT_FOUND",
                "error_message": "小节不存在",
            },
        }
        mock_mcp_with_error = MockMCPClientManager(responses)
        skills_server._mcp_client = mock_mcp_with_error
        result = await skills_server.skill_call_atomic_tool(
            server="teacher",
            tool="tch_delete_section",
            arguments={"section_id": "123"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "SECTION_NOT_FOUND"
        assert parsed["error_message"] == "小节不存在"

    async def test_call_atomic_tool_server_not_ready(self) -> None:
        skills_server._mcp_client = None
        result = await skills_server.skill_call_atomic_tool(
            server="teacher",
            tool="tch_get_categories",
            arguments={},
        )
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "SERVER_NOT_READY"


class TestBuiltinSkillParameterFixes:
    async def test_enroll_course_uses_enroll_id(self) -> None:
        responses = {
            ("student", "stu_enroll_course"): {
                "success": True,
                "data": {"is_enrolled": True},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        registry = SkillRegistry()
        registry.load_builtin_skills()
        skills_server._skill_registry = registry
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="enroll_course",
            arguments={"enroll_id": "mock-enroll-id"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("student", "stu_enroll_course", {"enroll_id": "mock-enroll-id"}),
        ]

    async def test_batch_onboard_users_uses_correct_parameters(self) -> None:
        responses = {
            ("admin", "adm_create_account"): {
                "success": True,
                "data": {"user_id": "mock-user-id"},
            },
            ("student", "stu_enroll_course"): {
                "success": True,
                "data": {"is_enrolled": True},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        registry = SkillRegistry()
        registry.load_builtin_skills()
        skills_server._skill_registry = registry
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="batch_onboard_users",
            arguments={
                "users": [
                    {
                        "user_name": "张三",
                        "accounts": "zhangsan@example.com",
                        "role_type": 1,
                    },
                ],
                "enroll_id": "mock-enroll-id",
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["created"] == 1
        assert parsed["data"]["enrolled"] == 1

        # 验证 adm_create_account 使用了正确的参数名
        admin_call = mock_mcp.calls[0]
        assert admin_call[0] == "admin"
        assert admin_call[1] == "adm_create_account"
        assert admin_call[2] == {
            "user_name": "张三",
            "accounts": "zhangsan@example.com",
            "role_type": 1,
        }

        # 验证 stu_enroll_course 使用了 enroll_id
        student_call = mock_mcp.calls[1]
        assert student_call[0] == "student"
        assert student_call[1] == "stu_enroll_course"
        assert student_call[2] == {"enroll_id": "mock-enroll-id"}

    async def test_batch_onboard_users_compatible_with_old_format(self) -> None:
        responses = {
            ("admin", "adm_create_account"): {
                "success": True,
                "data": {"user_id": "mock-user-id"},
            },
            ("student", "stu_enroll_course"): {
                "success": True,
                "data": {"is_enrolled": True},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        registry = SkillRegistry()
        registry.load_builtin_skills()
        skills_server._skill_registry = registry
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="batch_onboard_users",
            arguments={
                "users": [
                    {
                        "name": "李四",
                        "email": "lisi@example.com",
                        "role": "student",
                    },
                ],
                "enroll_id": "mock-enroll-id",
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True

        admin_call = mock_mcp.calls[0]
        assert admin_call[2] == {
            "user_name": "李四",
            "accounts": "lisi@example.com",
            "role_type": 1,
        }


@pytest.mark.asyncio
async def test_skill_double_direct() -> None:
    """直接验证 @skill 装饰后的函数可被调用."""

    @skill(name="direct", description="Direct")
    async def direct(ctx: SkillContext, x: int) -> dict[str, Any]:
        return {"result": x + 1}

    manager = AsyncMock(spec=MCPClientManager)
    ctx = SkillContext(mcp=manager, skill_name="direct")
    result = await direct.func(ctx, x=5)
    assert result == {"result": 6}
