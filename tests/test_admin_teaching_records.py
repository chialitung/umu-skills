"""Admin 授课记录查询测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import adm_list_teaching_records
from umu_sdk.core.admin_models import (
    TeachingRecord,
    TeachingRecordAuditStatus,
    TeachingRecordListData,
    TeachingRecordListPagination,
    TeachingRecordListResponse,
    TeachingRecordRaw,
    TeachingRecordTeacher,
    TeachingRecordTeacherRaw,
)
from umu_sdk.skills import server as skills_server
from umu_sdk.skills.mcp_client import ToolCallResult
from umu_sdk.skills.registry import SkillRegistry


@pytest.fixture(autouse=True)
def reset_skills_server_globals():
    """每个测试后恢复 skills_server 全局变量."""
    original_mcp = skills_server._mcp_client
    original_registry = skills_server._skill_registry
    yield
    skills_server._mcp_client = original_mcp
    skills_server._skill_registry = original_registry


@pytest.fixture
def sample_raw_teaching_record():
    """单个原始授课记录样本."""
    return {
        "id": 855,
        "course_id": 7160899,
        "start_time": 1761015600,
        "end_time": 1761018600,
        "location": "广州捷泰广场20楼",
        "import_type": 2,
        "participate_num": 4,
        "submit_num": 1,
        "submit_ts": 1762156728,
        "apply_desc": "",
        "audit_status": 2,
        "total_lecturing_duration": 50,
        "teacher_info": [
            {
                "umu_id": 12735726,
                "user_name": "Yoyo ZENG",
                "lecturing_duration": 50,
                "on_job_status": 1,
                "profile_url": "https://m.umu.cn/profile/bdd065f1ed85f7932126aa2b",
                "manage_permission": 1,
            }
        ],
        "group_title": "“小祖宗”投保速成攻略",
        "group_access_code": "bkz401",
        "group_share_url": "https://m.umu.cn/course/?groupId=7160899&sKey=bcaa",
        "session_count": 2,
    }


class TestTeachingRecordModels:
    """测试授课记录模型."""

    def test_audit_status_constants(self):
        """状态常量应映射正确."""
        assert TeachingRecordAuditStatus.PENDING == 2
        assert TeachingRecordAuditStatus.PASSED == 3
        assert TeachingRecordAuditStatus.REJECTED == 4

    def test_teacher_raw_parse(self):
        """原始讲师信息子模型解析."""
        raw = TeachingRecordTeacherRaw(umu_id=12735726, user_name="Yoyo ZENG")
        assert raw.umu_id == 12735726
        assert raw.user_name == "Yoyo ZENG"

    def test_teacher_standardize(self):
        """标准化讲师信息应包含可读文本."""
        raw = TeachingRecordTeacherRaw(
            umu_id=12735726,
            user_name="Yoyo ZENG",
            lecturing_duration=50,
            on_job_status=1,
            profile_url="https://m.umu.cn/profile/xxx",
            manage_permission=1,
        )
        teacher = TeachingRecordTeacher.from_raw(raw)
        assert teacher.umu_id == "12735726"
        assert teacher.user_name == "Yoyo ZENG"
        assert teacher.on_job_status == 1
        assert teacher.on_job_status_text == "在职"

    def test_teaching_record_raw_parse(self, sample_raw_teaching_record):
        """原始授课记录解析."""
        raw = TeachingRecordRaw(**sample_raw_teaching_record)
        assert raw.id == 855
        assert raw.audit_status == 2
        assert raw.group_access_code == "bkz401"
        assert len(raw.teacher_info) == 1

    def test_teaching_record_standardize(self, sample_raw_teaching_record):
        """标准化授课记录应含可读字段."""
        raw = TeachingRecordRaw(**sample_raw_teaching_record)
        record = TeachingRecord.from_raw(raw)
        assert record.id == 855
        assert record.audit_status == 2
        assert record.audit_status_text == "待审核"
        assert record.start_time_readable == "2025-10-21 11:00:00"
        assert record.end_time_readable == "2025-10-21 11:50:00"
        assert record.submit_time_readable == "2025-11-03 15:58:48"
        assert record.teachers[0].user_name == "Yoyo ZENG"
        assert record.group_access_code == "bkz401"

    def test_status_text_map(self):
        """审核状态码应正确映射人读文本."""
        assert (
            TeachingRecord.from_raw(TeachingRecordRaw(audit_status=2)).audit_status_text == "待审核"
        )
        assert (
            TeachingRecord.from_raw(TeachingRecordRaw(audit_status=3)).audit_status_text == "已通过"
        )
        assert (
            TeachingRecord.from_raw(TeachingRecordRaw(audit_status=4)).audit_status_text == "已拒绝"
        )
        assert (
            TeachingRecord.from_raw(TeachingRecordRaw(audit_status=9)).audit_status_text
            == "未知状态(9)"
        )

    def test_pagination_and_response(self):
        """分页与响应模型解析."""
        record = TeachingRecord.from_raw(TeachingRecordRaw(id=1, audit_status=2))
        data = TeachingRecordListData(
            records=[record],
            total=1,
            pagination=TeachingRecordListPagination(total_all=1, current_page=1, page_size=20),
        )
        response = TeachingRecordListResponse(success=True, data=data)
        assert response.data.total == 1
        assert response.data.pagination.total_all == 1


@pytest.fixture
def mock_client():
    """创建模拟的已认证 UMUClient."""
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


@pytest.fixture
def single_page_response(sample_raw_teaching_record):
    """单页响应样本."""
    return {
        "error_code": 0,
        "error_message": "",
        "data": {
            "page_info": {
                "list_total_num": 1,
                "total_page_num": 1,
                "current_page": 1,
                "size": 20,
            },
            "list": [sample_raw_teaching_record],
        },
    }


def _auth_patch(mock_client):
    """Patch _get_client and _require_auth for adm_list_teaching_records."""
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._require_auth", return_value=None))
    return stack


class TestAdmListTeachingRecords:
    """测试 adm_list_teaching_records 工具."""

    @pytest.mark.asyncio
    async def test_status_mapping(self, mock_client, single_page_response):
        """状态参数应正确映射为接口状态码."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_teaching_records(audit_status="pending")
        parsed = json.loads(result)
        assert parsed["success"] is True
        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        assert params["audit_status"] == "2"

    @pytest.mark.asyncio
    async def test_single_page_query(self, mock_client, single_page_response):
        """单页查询返回正确结果."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_teaching_records(audit_status="已通过", page=1, page_size=10)
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 1
        assert parsed["data"]["pagination"]["current_page"] == 1
        assert parsed["data"]["pagination"]["page_size"] == 10
        assert parsed["data"]["records"][0]["audit_status_text"] == "待审核"

    @pytest.mark.asyncio
    async def test_teacher_umu_ids_passed(self, mock_client, single_page_response):
        """显式传入 teacher_umu_ids 时应透传为 uids."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            await adm_list_teaching_records(
                audit_status="passed", teacher_umu_ids="12737766,12735726"
            )
        params = mock_client.get.call_args.kwargs.get("params", {})
        assert params["uids"] == "12737766,12735726"

    @pytest.mark.asyncio
    async def test_teacher_keywords_resolved(self, mock_client, single_page_response):
        """teacher_keywords 应解析为 umu_id 后查询."""
        mock_client.get.return_value = single_page_response
        with (
            _auth_patch(mock_client),
            patch(
                "umu_sdk.adapters.mcp.admin._resolve_user_keywords",
                return_value=["12737766"],
            ) as resolve_mock,
        ):
            await adm_list_teaching_records(audit_status="rejected", teacher_keywords="Jerry")
        resolve_mock.assert_awaited_once()
        params = mock_client.get.call_args.kwargs.get("params", {})
        assert params["uids"] == "12737766"

    @pytest.mark.asyncio
    async def test_course_keywords_as_search_keyword(self, mock_client, single_page_response):
        """course_keywords 应作为 search_keyword 提交."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            await adm_list_teaching_records(audit_status="pending", course_keywords="保全")
        params = mock_client.get.call_args.kwargs.get("params", {})
        assert params["search_keyword"] == "保全"

    @pytest.mark.asyncio
    async def test_access_code_local_filter(self, mock_client, single_page_response):
        """course_keywords 与 access_code 同时传入时，本地按访问码过滤."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_teaching_records(
                audit_status="pending", course_keywords="投保", access_code="bkz401"
            )
        parsed = json.loads(result)
        assert parsed["data"]["total"] == 1
        # 当 access_code 不匹配时，结果应为空
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_teaching_records(
                audit_status="pending", course_keywords="投保", access_code="not-match"
            )
        parsed = json.loads(result)
        assert parsed["data"]["total"] == 0

    @pytest.mark.asyncio
    async def test_invalid_status(self, mock_client):
        """非法审核状态应返回错误."""
        with _auth_patch(mock_client):
            result = await adm_list_teaching_records(audit_status="unknown")
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "INVALID_AUDIT_STATUS" in parsed["error_code"]


class TestGetTeachingRecordsSkill:
    """测试 get_teaching_records Skill."""

    @pytest.mark.asyncio
    async def test_skill_passes_arguments(self):
        """Skill 应正确透传参数到 admin 工具."""

        class MockMCPClientManager:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, Any]]] = []

            def list_servers(self) -> list[str]:
                return ["admin"]

            async def call_tool(
                self,
                server: str,
                tool: str,
                arguments: dict[str, Any] | None = None,
                read_timeout_seconds: float | None = None,
            ) -> ToolCallResult:
                self.calls.append((server, tool, arguments))
                return ToolCallResult(
                    success=True,
                    data={
                        "records": [],
                        "total": 0,
                        "pagination": {
                            "total_all": 0,
                            "current_page": 1,
                            "page_size": 20,
                        },
                    },
                    error_code="",
                    error_message="",
                )

        registry = SkillRegistry()
        registry.load_builtin_skills()
        mock_mcp = MockMCPClientManager()
        skills_server._skill_registry = registry
        skills_server._mcp_client = mock_mcp

        result = await skills_server.skill_run(
            name="get_teaching_records",
            arguments={
                "audit_status": "pending",
                "teacher_keywords": "Jerry",
                "course_keywords": "保全",
                "access_code": "um7927",
                "page": 1,
                "page_size": 20,
                "fetch_all": False,
            },
        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert mock_mcp.calls == [
            (
                "admin",
                "adm_list_teaching_records",
                {
                    "audit_status": "pending",
                    "teacher_keywords": "Jerry",
                    "course_keywords": "保全",
                    "access_code": "um7927",
                    "page": 1,
                    "page_size": 20,
                    "fetch_all": False,
                },
            ),
        ]
