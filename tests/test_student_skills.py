"""Tests for Student builtin skills."""

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
def registry_with_student_skills() -> SkillRegistry:
    registry = SkillRegistry()
    registry.load_builtin_skills()
    return registry


class TestStudentLearning:
    async def test_resolve_course_identifier(self, registry_with_student_skills: SkillRegistry) -> None:
        responses = {
            ("student", "stu_resolve_course_url"): {
                "success": True,
                "data": {"group_id": "g-123", "s_key": "sk-123"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="resolve_course_identifier",
            arguments={"course_identifier": "aet504"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["group_id"] == "g-123"
        assert mock_mcp.calls == [
            ("student", "stu_resolve_course_url", {"course_identifier": "aet504"}),
        ]

    async def test_list_my_courses_student(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_my_courses_student",
            arguments={"page": 1, "page_size": 10},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "student",
                "stu_get_my_courses",
                {"page": 1, "page_size": 10, "fetch_all": False},
            ),
        ]

    async def test_complete_browse_lesson(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="complete_browse_lesson",
            arguments={"element_id": "elem-123", "duration_seconds": 30},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("student", "stu_browse_lesson", {"element_id": "elem-123", "duration_seconds": 30}),
        ]

    async def test_complete_checkin(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="complete_checkin",
            arguments={"element_id": "elem-checkin"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("student", "stu_check_in", {"element_id": "elem-checkin"})]

    async def test_complete_rating_checkin(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="complete_rating_checkin",
            arguments={"element_id": "elem-rate", "rating": 5, "comment": "很好"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "student",
                "stu_check_in_with_rating",
                {"element_id": "elem-rate", "rating": 5, "comment": "很好"},
            ),
        ]

    async def test_check_lesson_completion(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="check_lesson_completion",
            arguments={"element_id": "elem-123", "group_id": "g-123"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("student", "stu_get_lesson_status", {"element_id": "elem-123", "group_id": "g-123"}),
        ]

    async def test_complete_scorm_section(self, registry_with_student_skills: SkillRegistry) -> None:
        responses = {
            ("student", "stu_complete_scorm_section"): {
                "success": True,
                "data": {"is_completed": True, "element_id": "e-scorm"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="complete_scorm_section",
            arguments={
                "element_id": "e-scorm",
                "group_id": "g1",
                "status": "passed",
                "score": 90,
                "duration_seconds": 120,
                "scorm_launch_url": "https://vfua3ytp5.m.umu.cn/scorm/1/launch/2/course/3/element/abc?sesskey=s",
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["is_completed"] is True

        assert len(mock_mcp.calls) == 1
        server, tool, args = mock_mcp.calls[0]
        assert server == "student"
        assert tool == "stu_complete_scorm_section"
        assert args["element_id"] == "e-scorm"
        assert args["group_id"] == "g1"
        assert args["score"] == 90
        assert args["duration_seconds"] == 120
        assert "scorm_launch_url" in args


class TestStudentAssessment:
    async def test_get_questionnaire(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="get_questionnaire",
            arguments={"element_id": "elem-q"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("student", "stu_get_questionnaire_questions", {"element_id": "elem-q"}),
        ]

    async def test_submit_questionnaire(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="submit_questionnaire",
            arguments={"element_id": "elem-q", "answers_json": "[]"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("student", "stu_submit_questionnaire", {"element_id": "elem-q", "answers_json": "[]"}),
        ]

    async def test_submit_questionnaire_simple(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="submit_questionnaire_simple",
            arguments={"element_id": "elem-q", "answers_config": "A;B"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "student",
                "stu_submit_questionnaire_with_config",
                {"element_id": "elem-q", "answers_config": "A;B"},
            ),
        ]

    async def test_start_exam(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="start_exam",
            arguments={"element_id": "elem-exam"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("student", "stu_start_exam", {"element_id": "elem-exam"})]

    async def test_submit_exam(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="submit_exam",
            arguments={"element_id": "elem-exam", "exam_submit_id": "sub-123", "answers_json": "{}"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "student",
                "stu_submit_exam",
                {"element_id": "elem-exam", "exam_submit_id": "sub-123", "answers_json": "{}"},
            ),
        ]

    async def test_submit_exam_simple(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="submit_exam_simple",
            arguments={"element_id": "elem-exam", "answers_config": "A;B"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "student",
                "stu_submit_exam_with_config",
                {"element_id": "elem-exam", "answers_config": "A;B"},
            ),
        ]


class TestStudentCourseCompletion:
    async def test_complete_entire_course(self, registry_with_student_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_student_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="complete_entire_course",
            arguments={"course_identifier": "aet504"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "student",
                "stu_complete_course",
                {"course_identifier": "aet504", "skip_exam": True, "skip_questionnaire": True},
            ),
        ]
