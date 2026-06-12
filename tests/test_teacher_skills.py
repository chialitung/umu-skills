"""Tests for Teacher builtin skills."""

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
def registry_with_teacher_skills() -> SkillRegistry:
    registry = SkillRegistry()
    registry.load_builtin_skills()
    return registry


class TestTeacherResources:
    async def test_upload_scorm_resource(self, registry_with_teacher_skills: SkillRegistry) -> None:
        responses = {
            ("teacher", "tch_upload_scorm"): {
                "success": True,
                "data": {"resource_id": "res-123"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="upload_scorm_resource",
            arguments={"file_path": "/path/to/course.zip", "title": "测试 SCORM"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["resource_id"] == "res-123"
        assert mock_mcp.calls == [
            ("teacher", "tch_upload_scorm", {"file_path": "/path/to/course.zip", "name": "测试 SCORM"}),
        ]

    async def test_upload_document_resource(self, registry_with_teacher_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="upload_document_resource",
            arguments={"file_path": "/path/to/doc.pdf"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("teacher", "tch_upload_document", {"file_path": "/path/to/doc.pdf"}),
        ]

    async def test_upload_video_resource(self, registry_with_teacher_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="upload_video_resource",
            arguments={"file_path": "/path/to/video.mp4"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("teacher", "tch_upload_audio_video", {"file_path": "/path/to/video.mp4"}),
        ]

    async def test_list_scorm_resources(self, registry_with_teacher_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_scorm_resources",
            arguments={"page": 1, "page_size": 10},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "teacher",
                "tch_list_resources",
                {"page": 1, "page_size": 10, "media_type": "videoweike", "ext_type": "scorm"},
            ),
        ]

    async def test_list_document_resources(self, registry_with_teacher_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_document_resources",
            arguments={"search_keyword": "test"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("teacher", "tch_list_documents", {"page": 1, "page_size": 20, "search_keyword": "test"}),
        ]

    async def test_list_video_resources(self, registry_with_teacher_skills: SkillRegistry) -> None:
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_video_resources",
            arguments={"page": 2},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("teacher", "tch_list_audio_videos", {"page": 2, "page_size": 20}),
        ]


class TestTeacherSections:
    async def test_add_video_section(self, registry_with_teacher_skills: SkillRegistry) -> None:
        responses = {
            ("teacher", "tch_create_video_section"): {
                "success": True,
                "data": {"section_id": "sec-123"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="add_video_section",
            arguments={
                "group_id": "g-123",
                "session_title": "视频小节",
                "video_resource_id": "res-123",
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["section_id"] == "sec-123"
        assert mock_mcp.calls == [
            (
                "teacher",
                "tch_create_video_section",
                {
                    "group_id": "g-123",
                    "session_title": "视频小节",
                    "video_resource_id": "res-123",
                },
            ),
        ]

    async def test_add_article_section(self, registry_with_teacher_skills: SkillRegistry) -> None:
        responses = {
            ("teacher", "tch_create_article_section"): {
                "success": True,
                "data": {"section_id": "sec-article"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="add_article_section",
            arguments={
                "group_id": "g-123",
                "session_title": "文章小节",
                "article_content": "<p>内容</p>",
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls[0][2]["article_content"] == "<p>内容</p>"

    async def test_add_document_section(self, registry_with_teacher_skills: SkillRegistry) -> None:
        responses = {
            ("teacher", "tch_create_document_section"): {
                "success": True,
                "data": {"section_id": "sec-doc"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="add_document_section",
            arguments={
                "group_id": "g-123",
                "section_title": "文档小节",
                "document_resource_id": "doc-123",
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls[0][2] == {
            "group_id": "g-123",
            "section_title": "文档小节",
            "document_resource_id": "doc-123",
        }

    async def test_list_course_sections(self, registry_with_teacher_skills: SkillRegistry) -> None:
        responses = {
            ("teacher", "tch_list_sections"): {
                "success": True,
                "data": {"sections": [{"id": "s1"}]},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_course_sections",
            arguments={"group_id": "g-123"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("teacher", "tch_list_sections", {"group_id": "g-123"})]


class TestTeacherCourses:
    async def test_get_course_categories(self, registry_with_teacher_skills: SkillRegistry) -> None:
        responses = {
            ("teacher", "tch_get_categories"): {
                "success": True,
                "data": {"categories": []},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="get_course_categories",
            arguments={},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [("teacher", "tch_get_categories", {})]

    async def test_get_course_info(self, registry_with_teacher_skills: SkillRegistry) -> None:
        responses = {
            ("teacher", "tch_get_course"): {
                "success": True,
                "data": {"group_id": "g-123", "title": "测试课程"},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="get_course_info",
            arguments={"group_id": "g-123"},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("teacher", "tch_get_course", {"group_id": "g-123", "include_fulltext": False}),
        ]

    async def test_list_my_courses(self, registry_with_teacher_skills: SkillRegistry) -> None:
        responses = {
            ("teacher", "tch_list_created_courses"): {
                "success": True,
                "data": {"courses": []},
            },
        }
        mock_mcp = MockMCPClientManager(responses)
        skills_server._skill_registry = registry_with_teacher_skills
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="list_my_courses",
            arguments={"page": 1, "page_size": 10},
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            ("teacher", "tch_list_created_courses", {"page": 1, "page_size": 10, "order": "update_time"}),
        ]
