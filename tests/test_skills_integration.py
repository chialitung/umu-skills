"""Integration tests for cross-role Skill orchestration."""

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
def registry_with_all_skills() -> SkillRegistry:
    registry = SkillRegistry()
    registry.load_builtin_skills()
    return registry


class TestCrossRoleOnboardAndLearn:
    @pytest.mark.skip(reason="batch_onboard_users skill 已临时禁用")
    async def test_admin_onboard_then_student_enroll_and_complete(
        self, registry_with_all_skills: SkillRegistry
    ) -> None:
        responses = {
            ("admin", "adm_create_account"): {
                "success": True,
                "data": {"umu_id": "u-001", "user_name": "张三"},
            },
            ("student", "stu_enroll_course"): {
                "success": True,
                "data": {"is_enrolled": True},
            },
            ("student", "stu_resolve_course_url"): {
                "success": True,
                "data": {"group_id": "g-001", "s_key": "sk-001"},
            },
            ("student", "stu_complete_course"): {
                "success": True,
                "data": {"completed_lessons": ["elem-1"], "failed_lessons": []},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_all_skills
        skills_server._mcp_client = mock_mcp

        onboard_result = await skills_server.skill_run(
            name="batch_onboard_users",
            arguments={
                "users": [
                    {
                        "user_name": "张三",
                        "accounts": "zhangsan@umu.cn",
                        "role_type": 1,
                    }
                ],
                "enroll_id": "enr-001",
            },
        )
        parsed_onboard = json.loads(onboard_result)
        assert parsed_onboard["success"] is True

        resolve_result = await skills_server.skill_run(
            name="resolve_course_identifier",
            arguments={"course_identifier": "aet504"},
        )
        parsed_resolve = json.loads(resolve_result)
        assert parsed_resolve["success"] is True
        assert parsed_resolve["data"]["group_id"] == "g-001"

        complete_result = await skills_server.skill_run(
            name="complete_entire_course",
            arguments={"course_identifier": "aet504"},
        )
        parsed_complete = json.loads(complete_result)
        assert parsed_complete["success"] is True

        assert mock_mcp.calls[0] == (
            "admin",
            "adm_create_account",
            {
                "user_name": "张三",
                "accounts": "zhangsan@umu.cn",
                "role_type": 1,
            },
        )
        assert mock_mcp.calls[1] == (
            "student",
            "stu_enroll_course",
            {"enroll_id": "enr-001"},
        )
        assert mock_mcp.calls[2] == (
            "student",
            "stu_resolve_course_url",
            {"course_identifier": "aet504"},
        )
        assert mock_mcp.calls[3] == (
            "student",
            "stu_complete_course",
            {
                "course_identifier": "aet504",
                "skip_exam": True,
                "skip_questionnaire": True,
            },
        )


class TestTeacherCreateAndStudentLearn:
    async def test_teacher_creates_course_and_student_completes_lesson(
        self, registry_with_all_skills: SkillRegistry
    ) -> None:
        responses = {
            ("teacher", "tch_create_course"): {
                "success": True,
                "data": {"group_id": "g-course", "course_url": "https://www.umu.cn/..."},
            },
            ("teacher", "tch_create_scorm_section"): {
                "success": True,
                "data": {"section_id": "sec-scorm"},
            },
            ("teacher", "tch_create_video_section"): {
                "success": True,
                "data": {"section_id": "sec-1"},
            },
            ("student", "stu_browse_lesson"): {
                "success": True,
                "data": {"element_id": "sec-1", "action": "browse_completed"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_all_skills
        skills_server._mcp_client = mock_mcp

        course_result = await skills_server.skill_run(
            name="create_course_with_scorm",
            arguments={
                "title": "新课程",
                "scorm_resource_id": "res-scorm",
            },
        )
        parsed_course = json.loads(course_result)
        assert parsed_course["success"] is True

        section_result = await skills_server.skill_run(
            name="add_video_section",
            arguments={
                "group_id": "g-course",
                "session_title": "视频小节",
                "video_resource_id": "vid-1",
            },
        )
        parsed_section = json.loads(section_result)
        assert parsed_section["success"] is True

        browse_result = await skills_server.skill_run(
            name="complete_browse_lesson",
            arguments={"element_id": "sec-1", "duration_seconds": 60},
        )
        parsed_browse = json.loads(browse_result)
        assert parsed_browse["success"] is True

        assert mock_mcp.calls == [
            ("teacher", "tch_create_course", {"title": "新课程"}),
            (
                "teacher",
                "tch_create_scorm_section",
                {
                    "group_id": "g-course",
                    "section_title": "SCORM 学习",
                    "scorm_resource_id": "res-scorm",
                },
            ),
            ("teacher", "tch_get_course", {"group_id": "g-course"}),
            (
                "teacher",
                "tch_create_video_section",
                {
                    "group_id": "g-course",
                    "session_title": "视频小节",
                    "video_resource_id": "vid-1",
                },
            ),
            ("student", "stu_browse_lesson", {"element_id": "sec-1", "duration_seconds": 60}),
        ]


class TestSkillCallAtomicToolFallback:
    async def test_passthrough_reaches_uncovered_atomic_tool(
        self, registry_with_all_skills: SkillRegistry
    ) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_all_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_call_atomic_tool(
            server="teacher",
            tool="tch_get_course_stats",
            arguments={"group_id": "g-001"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("teacher", "tch_get_course_stats", {"group_id": "g-001"}),
        ]
