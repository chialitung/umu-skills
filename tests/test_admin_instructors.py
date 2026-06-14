"""Admin 讲师列表查询测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.admin import (
    adm_list_instructors,
    _build_instructor_search_condition,
    _resolve_instructor_group_names,
    _resolve_instructor_tag_names,
)
from umu_sdk.core.admin_models import (
    Instructor,
    InstructorListData,
    InstructorListPagination,
    InstructorListResponse,
    InstructorRaw,
)


@pytest.fixture
def mock_client():
    """创建模拟的已认证 UMUClient."""
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


@pytest.fixture
def sample_raw_instructor():
    """单个原始讲师样本."""
    return {
        "role_type": "2",
        "affected_student_count": 8,
        "affected_student_times": 9,
        "lecturing_duration": 3600,
        "lecturing_participate_student_times": 10,
        "certification_status": 1,
        "certification_expire_time": 4102415999,
        "certification_start_time": 1743091200,
        "tags": [
            {"tag_id": 393, "tag_name": "金牌讲师"},
            {"tag_id": 394, "tag_name": "外部讲师"},
        ],
        "id": "12731630",
        "enterprise_id": "11018",
        "create_time": "2022-11-07 10:20:33",
        "update_time": "2023-05-11 14:04:02",
        "user_enterprise_id": "11018",
        "umu_id": "12733916",
        "student_id": "31492212",
        "teacher_id": "12731630",
        "has_actived": "1",
        "user_type": "1",
        "register_from": "1",
        "user_name": "Cecilia Wang",
        "email": "cecilia-j.wang@aia.com",
        "number": "9009732",
        "on_job_status": 1,
        "phone": "13800138000",
        "login_name": "cecilia.wang",
        "avatar": "https://statics-umu-cn.umucdn.cn/image/a/a/1phNq/2944423900.jpg",
        "enterprise_groups": ["TalentDevelopment"],
        "enterprise_departments": ["总公司-首席运营官办公室-运营部-运营平台管理组"],
        "class": None,
        "is_signout_free": 0,
    }


@pytest.fixture
def single_page_response(sample_raw_instructor):
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
            "list": [sample_raw_instructor],
        },
    }


def _auth_patch(mock_client):
    """Patch _get_client and _require_auth for adm_list_instructors."""
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.admin._require_auth", return_value=None))
    return stack


class TestInstructorRaw:
    """测试 InstructorRaw 原始模型."""

    def test_parse_full(self, sample_raw_instructor):
        """完整字段应正确解析."""
        raw = InstructorRaw(**sample_raw_instructor)
        assert raw.teacher_id == "12731630"
        assert raw.umu_id == "12733916"
        assert raw.user_name == "Cecilia Wang"
        assert raw.certification_status == 1
        assert len(raw.tags) == 2
        assert raw.tags[0]["tag_id"] == 393
        assert raw.class_names is None

    def test_parse_minimal(self):
        """最小字段应使用默认值."""
        raw = InstructorRaw(id="1", umu_id="1", teacher_id="1", user_name="Test")
        assert raw.certification_status == 0
        assert raw.tags == []
        assert raw.class_names is None


class TestInstructor:
    """测试 Instructor 标准化模型."""

    def test_from_raw_full(self, sample_raw_instructor):
        """完整原始对象应正确标准化."""
        raw = InstructorRaw(**sample_raw_instructor)
        instructor = Instructor.from_raw(raw)

        assert instructor.teacher_id == "12731630"
        assert instructor.umu_id == "12733916"
        assert instructor.student_id == "31492212"
        assert instructor.user_name == "Cecilia Wang"
        assert instructor.email == "cecilia-j.wang@aia.com"
        assert instructor.number == "9009732"
        assert instructor.phone == "13800138000"
        assert instructor.login_name == "cecilia.wang"
        assert instructor.avatar == "https://statics-umu-cn.umucdn.cn/image/a/a/1phNq/2944423900.jpg"
        assert instructor.role_type == 2
        assert instructor.role_name == "讲师"
        assert instructor.certification_status == 1
        assert instructor.certification_status_text == "已认证"
        assert instructor.certification_start_time == 1743091200
        assert instructor.certification_start_time_readable == "2025-03-28 00:00:00"
        assert instructor.certification_expire_time == 4102415999
        assert instructor.certification_expire_time_readable == "2099-12-31 23:59:59"
        assert instructor.tag_ids == [393, 394]
        assert instructor.tag_names == ["金牌讲师", "外部讲师"]
        assert instructor.enterprise_groups == ["TalentDevelopment"]
        assert instructor.enterprise_departments == ["总公司-首席运营官办公室-运营部-运营平台管理组"]
        assert instructor.affected_student_count == 8
        assert instructor.affected_student_times == 9
        assert instructor.lecturing_duration == 3600
        assert instructor.lecturing_participate_student_times == 10
        assert instructor.on_job_status == 1
        assert instructor.on_job_status_text == "在职"
        assert instructor.has_actived is True
        assert instructor.is_signout_free == 0

    def test_certification_status_map(self):
        """认证状态码应正确映射."""
        assert Instructor.from_raw(InstructorRaw(certification_status=1)).certification_status_text == "已认证"
        assert Instructor.from_raw(InstructorRaw(certification_status=0)).certification_status_text == "未认证"
        assert Instructor.from_raw(InstructorRaw(certification_status=9)).certification_status_text == "未知(9)"

    def test_on_job_status_map(self):
        """在职状态码应正确映射."""
        assert Instructor.from_raw(InstructorRaw(on_job_status=1)).on_job_status_text == "在职"
        assert Instructor.from_raw(InstructorRaw(on_job_status=0)).on_job_status_text == "离职"
        assert Instructor.from_raw(InstructorRaw(on_job_status=9)).on_job_status_text == "未知(9)"

    def test_role_type_map(self):
        """角色类型码应正确映射."""
        assert Instructor.from_raw(InstructorRaw(role_type="2")).role_name == "讲师"
        assert Instructor.from_raw(InstructorRaw(role_type="99")).role_name == "未知角色(99)"

    def test_has_actived_map(self):
        """活跃状态应正确映射."""
        assert Instructor.from_raw(InstructorRaw(has_actived="1")).has_actived is True
        assert Instructor.from_raw(InstructorRaw(has_actived="0")).has_actived is False

    def test_model_dump(self, sample_raw_instructor):
        """model_dump 应包含所有字段."""
        raw = InstructorRaw(**sample_raw_instructor)
        instructor = Instructor.from_raw(raw)
        data = instructor.model_dump()
        assert "teacher_id" in data
        assert "certification_status_text" in data
        assert "tag_names" in data


class TestInstructorPagination:
    """测试分页模型."""

    def test_pagination(self):
        """标准化分页信息应正确解析."""
        pagination = InstructorListPagination(
            total_all=100,
            current_page=1,
            page_size=20,
        )
        assert pagination.total_all == 100
        assert pagination.page_size == 20


class TestInstructorListData:
    """测试列表数据模型."""

    def test_with_instructors(self, sample_raw_instructor):
        """包含讲师列表时应正确解析."""
        raw = InstructorRaw(**sample_raw_instructor)
        instructor = Instructor.from_raw(raw)
        data = InstructorListData(
            instructors=[instructor],
            total=1,
            pagination=InstructorListPagination(
                total_all=1,
                current_page=1,
                page_size=20,
            ),
        )
        assert len(data.instructors) == 1
        assert data.total == 1
        assert data.pagination.total_all == 1


class TestInstructorListResponse:
    """测试响应包装模型."""

    def test_success_response(self, sample_raw_instructor):
        """成功响应应正确解析."""
        raw = InstructorRaw(**sample_raw_instructor)
        instructor = Instructor.from_raw(raw)
        response = InstructorListResponse(
            success=True,
            data=InstructorListData(
                instructors=[instructor],
                total=1,
                pagination=InstructorListPagination(
                    total_all=1,
                    current_page=1,
                    page_size=20,
                ),
            ),
        )
        assert response.success is True
        assert response.error_code == ""
        assert response.next_action == "proceed"
        assert len(response.data.instructors) == 1

    def test_error_response(self):
        """错误响应应正确解析."""
        response = InstructorListResponse(
            success=False,
            data=InstructorListData(
                instructors=[],
                total=0,
                pagination=InstructorListPagination(
                    total_all=0,
                    current_page=1,
                    page_size=20,
                ),
            ),
            error_code="LIST_INSTRUCTORS_ERROR",
            error_message="获取失败",
            suggested_action="重试",
            next_action="needs_user_input",
        )
        assert response.success is False
        assert response.error_code == "LIST_INSTRUCTORS_ERROR"
        assert response.next_action == "needs_user_input"


class TestBuildInstructorSearchCondition:
    """测试 _build_instructor_search_condition 辅助函数."""

    def test_empty_returns_empty_dict(self):
        """无参数时返回空字典."""
        condition = _build_instructor_search_condition()
        assert condition == {}

    def test_certification_status(self):
        """认证状态应直接传入."""
        condition = _build_instructor_search_condition(certification_status=1)
        assert condition["certification_status"] == 1

    def test_tag_ids(self):
        """标签 ID 应使用 tag_ids 键并转为整数列表."""
        condition = _build_instructor_search_condition(tag_ids=["354", "355"])
        assert condition["tag_ids"] == [354, 355]

    def test_department_ids(self):
        """部门 ID 应使用字符串列表."""
        condition = _build_instructor_search_condition(department_ids=["82064", "82065"])
        assert condition["department_ids"] == ["82064", "82065"]

    def test_group_ids(self):
        """分组 ID 应使用 enterprise_group_ids 键并转为整数列表."""
        condition = _build_instructor_search_condition(group_ids=["136804", "127992"])
        assert condition["enterprise_group_ids"] == [136804, 127992]

    def test_account_keyword(self):
        """账号关键词应使用 account_keyword 键."""
        condition = _build_instructor_search_condition(account_keyword="jiali")
        assert condition["account_keyword"] == "jiali"

    def test_full_condition(self):
        """完整条件应包含所有字段."""
        condition = _build_instructor_search_condition(
            certification_status=0,
            tag_ids=["354"],
            department_ids=["82064"],
            group_ids=["136804"],
            account_keyword="test",
        )
        assert condition["certification_status"] == 0
        assert condition["tag_ids"] == [354]
        assert condition["department_ids"] == ["82064"]
        assert condition["enterprise_group_ids"] == [136804]
        assert condition["account_keyword"] == "test"


class TestResolveInstructorTagNames:
    """测试 _resolve_instructor_tag_names 辅助函数."""

    @pytest.mark.asyncio
    async def test_resolves_single_match(self, mock_client):
        """单个匹配时返回标签 ID 列表."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {"list_total_num": 2, "total_page_num": 1},
                "list": [
                    {"tag_id": 354, "tag_name": "专职讲师", "is_default": "1"},
                    {"tag_id": 355, "tag_name": "内训师", "is_default": "1"},
                ],
            },
        }
        ids = await _resolve_instructor_tag_names(mock_client, "专职")
        assert ids == ["354"]
        call_url = mock_client.get.call_args[0][0]
        assert "/uapi/v1/teacher-manage/tag-list" in call_url

    @pytest.mark.asyncio
    async def test_resolves_multiple_keywords(self, mock_client):
        """多个关键词匹配时返回所有匹配的 ID."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {"list_total_num": 2, "total_page_num": 1},
                "list": [
                    {"tag_id": 354, "tag_name": "专职讲师", "is_default": "1"},
                    {"tag_id": 355, "tag_name": "内训师", "is_default": "1"},
                ],
            },
        }
        ids = await _resolve_instructor_tag_names(mock_client, "专职, 内训")
        assert "354" in ids
        assert "355" in ids

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_client):
        """无匹配时返回 None."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {"page_info": {"list_total_num": 0}, "list": []},
        }
        ids = await _resolve_instructor_tag_names(mock_client, "不存在")
        assert ids is None

    @pytest.mark.asyncio
    async def test_empty_input_returns_none(self, mock_client):
        """空输入时返回 None."""
        ids = await _resolve_instructor_tag_names(mock_client, "  ,  ")
        assert ids is None

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_client):
        """接口失败时抛出 RuntimeError."""
        mock_client.get.return_value = {
            "error_code": 500,
            "error_message": "服务器错误",
        }
        with pytest.raises(RuntimeError, match="查询讲师标签列表失败"):
            await _resolve_instructor_tag_names(mock_client, "专职")


class TestResolveInstructorGroupNames:
    """测试 _resolve_instructor_group_names 辅助函数."""

    @pytest.mark.asyncio
    async def test_resolves_single_match(self, mock_client):
        """单个匹配时返回分组 ID 列表."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {"list_total_num": 1, "total_page_num": 1},
                "list": [
                    {"id": "136804", "group_name": "TalentDevelopment"},
                ],
            },
        }
        ids = await _resolve_instructor_group_names(mock_client, "Talent")
        assert ids == ["136804"]
        call_url = mock_client.get.call_args[0][0]
        assert "/uapi/v1/enterprise/enterprise-group-list" in call_url

    @pytest.mark.asyncio
    async def test_pagination_fetches_all(self, mock_client):
        """分页拉取全部数据."""
        responses = [
            {
                "error_code": 0,
                "data": {
                    "page_info": {"list_total_num": 2, "total_page_num": 2},
                    "list": [
                        {"id": "136804", "group_name": "分组A"},
                    ],
                },
            },
            {
                "error_code": 0,
                "data": {
                    "page_info": {"list_total_num": 2, "total_page_num": 2},
                    "list": [
                        {"id": "127992", "group_name": "分组B"},
                    ],
                },
            },
        ]
        mock_client.get.side_effect = responses
        ids = await _resolve_instructor_group_names(mock_client, "分组")
        assert "136804" in ids
        assert "127992" in ids
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self, mock_client):
        """无匹配时返回 None."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {"page_info": {"list_total_num": 0}, "list": []},
        }
        ids = await _resolve_instructor_group_names(mock_client, "不存在")
        assert ids is None

    @pytest.mark.asyncio
    async def test_api_error_raises(self, mock_client):
        """接口失败时抛出 RuntimeError."""
        mock_client.get.return_value = {
            "error_code": 500,
            "error_message": "服务器错误",
        }
        with pytest.raises(RuntimeError, match="查询分组列表失败"):
            await _resolve_instructor_group_names(mock_client, "分组")


class TestAdmListInstructors:
    """测试 adm_list_instructors 工具."""

    @pytest.mark.asyncio
    async def test_not_authenticated(self, mock_client):
        """未认证时返回错误."""
        with patch("umu_sdk.adapters.mcp.admin._get_client", return_value=mock_client):
            with patch(
                "umu_sdk.adapters.mcp.admin._require_auth",
                return_value="当前未登录",
            ):
                result = await adm_list_instructors()
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "NOT_AUTHENTICATED"

    @pytest.mark.asyncio
    async def test_single_page_query(self, mock_client, single_page_response):
        """单页查询返回正确结果."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_instructors(page=1, page_size=10)
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 1
        assert parsed["data"]["pagination"]["current_page"] == 1
        assert parsed["data"]["pagination"]["page_size"] == 10
        assert parsed["data"]["instructors"][0]["user_name"] == "Cecilia Wang"

    @pytest.mark.asyncio
    async def test_fetch_all_pagination(self, mock_client, sample_raw_instructor):
        """fetch_all=True 时自动翻页."""
        mock_client.get.side_effect = [
            {
                "error_code": 0,
                "data": {
                    "page_info": {
                        "list_total_num": 2,
                        "total_page_num": 2,
                        "current_page": 1,
                        "size": 20,
                    },
                    "list": [sample_raw_instructor],
                },
            },
            {
                "error_code": 0,
                "data": {
                    "page_info": {
                        "list_total_num": 2,
                        "total_page_num": 2,
                        "current_page": 2,
                        "size": 20,
                    },
                    "list": [{**sample_raw_instructor, "teacher_id": "12731631"}],
                },
            },
        ]
        with _auth_patch(mock_client):
            result = await adm_list_instructors(fetch_all=True)
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 2
        assert parsed["data"]["pagination"]["total_all"] == 2
        assert mock_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_with_filters(self, mock_client, single_page_response):
        """带筛选条件时正确构建 search_condition."""
        mock_client.get.return_value = single_page_response
        with _auth_patch(mock_client):
            result = await adm_list_instructors(
                certification_status="certified",
                tag_ids="354",
                department_ids="82064",
                group_ids="136804",
                account_keyword="jiali",
            )
        parsed = json.loads(result)
        assert parsed["success"] is True
        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        condition = json.loads(params["search_condition"])
        assert condition["certification_status"] == 1
        assert condition["tag_ids"] == [354]
        assert condition["department_ids"] == ["82064"]
        assert condition["enterprise_group_ids"] == [136804]
        assert condition["account_keyword"] == "jiali"

    @pytest.mark.asyncio
    async def test_merge_explicit_and_resolved_ids(self, mock_client, single_page_response):
        """显式 ID 和解析 ID 应合并去重."""
        with _auth_patch(mock_client):
            with patch(
                "umu_sdk.adapters.mcp.admin._resolve_instructor_tag_names",
                return_value=["355"],
            ):
                with patch(
                    "umu_sdk.adapters.mcp.admin._resolve_department_names",
                    return_value=["82065"],
                ):
                    with patch(
                        "umu_sdk.adapters.mcp.admin._resolve_instructor_group_names",
                        return_value=["127992"],
                    ):
                        mock_client.get.return_value = single_page_response
                        result = await adm_list_instructors(
                            tag_ids="354,355",
                            tag_names="内训师",
                            department_ids="82064",
                            department_names="技术部",
                            group_ids="136804",
                            group_names="分组B",
                        )
        parsed = json.loads(result)
        assert parsed["success"] is True
        call_args = mock_client.get.call_args
        params = call_args.kwargs.get("params", call_args[1].get("params", {}))
        condition = json.loads(params["search_condition"])
        assert condition["tag_ids"] == [354, 355]
        assert condition["department_ids"] == ["82064", "82065"]
        assert condition["enterprise_group_ids"] == [136804, 127992]

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_client):
        """空结果应正确返回."""
        mock_client.get.return_value = {
            "error_code": 0,
            "data": {
                "page_info": {
                    "list_total_num": 0,
                    "total_page_num": 0,
                    "current_page": 1,
                    "size": 20,
                },
                "list": [],
            },
        }
        with _auth_patch(mock_client):
            result = await adm_list_instructors()
        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["total"] == 0
        assert parsed["data"]["instructors"] == []

    @pytest.mark.asyncio
    async def test_error_response(self, mock_client):
        """API 错误时返回标准错误结构."""
        mock_client.get.return_value = {"error_code": 500, "error_message": "服务器错误"}
        with _auth_patch(mock_client):
            result = await adm_list_instructors()
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["error_code"] == "LIST_INSTRUCTORS_ERROR"
        assert "服务器错误" in parsed["error_message"]
