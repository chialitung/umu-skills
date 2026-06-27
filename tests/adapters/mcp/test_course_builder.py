"""CourseBuilder 测试."""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.course_builder import CourseBuilder
from umu_sdk.adapters.mcp.teacher import tch_set_course_enrollment, tch_update_course


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.base_url = "https://www.umu.cn"
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    return client


@pytest.fixture
def builder(mock_client):
    return CourseBuilder(mock_client)


def _enroll_save_response(enroll_id: str = "580237"):
    return {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {"enrollId": enroll_id},
    }


def _course_info_response(title: str = "测试课程"):
    return {
        "status": True,
        "error_code": 0,
        "data": {
            "info": {
                "groupInfo": {
                    "groupTitle": title,
                    "title": title,
                    "desc": "",
                    "groupRemark": "",
                    "lesson_type": 0,
                    "content_type": "0",
                    "courseType": "1",
                    "eventType": "7",
                    "headImg": "",
                    "bg_img": "",
                    "custom_head_img": False,
                    "multimedia_id": "",
                    "multimedia_type": "",
                    "province": "",
                    "city": "",
                    "town": "",
                    "address": "",
                    "contact": "",
                    "contactPhone": "",
                    "customerName": "",
                    "coursePerson": "",
                    "maxOnlineUser": "",
                    "maxUserCount": "",
                    "isimportant": "0",
                    "is_lock": "0",
                    "is_repetitive_mode": "0",
                    "stime": 0,
                    "etime": 0,
                    "startTime": "",
                    "endTime": "",
                    "groupTime": [],
                    "setup": {},
                    "enrollStatus": 0,
                    "release_status": "0",
                    "audit_status": "0",
                    "access_code": "abc123",
                    "tags": [],
                },
                "categoryArr": [],
            }
        },
    }


class TestSetCourseEnrollment:
    def test_set_course_enrollment_request_payload(self, builder, mock_client):
        mock_client.get.return_value = _course_info_response("E2E_测试课程_SCORM")
        mock_client.post.return_value = _enroll_save_response("580237")

        result = builder.set_course_enrollment(
            group_id="7339916",
            enabled=True,
            auto_check=True,
        )

        assert result["group_id"] == "7339916"
        assert result["enroll_id"] == "580237"
        assert result["enabled"] is True
        assert result["auto_check"] is True

        calls = mock_client.post.call_args_list
        assert len(calls) == 1
        assert calls[0].args[0] == "https://www.umu.cn/api/enroll/saveenroll"

        payload = json.loads(calls[0].kwargs["data"]["enroll"])
        assert payload["group_id"] == "7339916"
        assert payload["title"] == "E2E_测试课程_SCORM"
        assert payload["status"] == 1
        assert payload["autoCheck"] == 1
        assert payload["sessionType"] == "9"
        assert payload["setup"]["allow_cancel"] == "0"
        assert payload["setup"]["user_quota"] == "-1"
        assert len(payload["contactInfo"]) == len(CourseBuilder._DEFAULT_ENROLL_CONTACT_INFO)

    def test_set_course_enrollment_disabled(self, builder, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.return_value = _enroll_save_response("580238")

        result = builder.set_course_enrollment(
            group_id="7339916",
            enabled=False,
            auto_check=False,
            allow_cancel=True,
            user_quota=100,
            begin_time=1782561347,
            end_time=1782647747,
        )

        assert result["enabled"] is False
        assert result["auto_check"] is False

        payload = json.loads(mock_client.post.call_args.kwargs["data"]["enroll"])
        assert payload["status"] == 0
        assert payload["autoCheck"] == 0
        assert payload["setup"]["allow_cancel"] == "1"
        assert payload["setup"]["user_quota"] == "100"
        assert payload["setup"]["begin_time"] == "1782561347"
        assert payload["setup"]["end_time"] == "1782647747"

    def test_set_course_enrollment_custom_title_and_fields(self, builder, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.return_value = _enroll_save_response()

        custom_fields = [
            {
                "key": "employee_id",
                "questionTitle": "工号",
                "defaultPlaceHolder": "请输入工号",
                "domType": "text",
                "isRequired": True,
            }
        ]

        result = builder.set_course_enrollment(
            group_id="7339916",
            enabled=True,
            title="自定义报名标题",
            contact_info=custom_fields,
        )

        assert result["enroll_id"] == "580237"
        payload = json.loads(mock_client.post.call_args.kwargs["data"]["enroll"])
        assert payload["title"] == "自定义报名标题"
        assert len(payload["contactInfo"]) == 1
        assert payload["contactInfo"][0]["key"] == "employee_id"
        assert payload["contactInfo"][0]["questionTitle"] == "工号"
        assert payload["contactInfo"][0]["isRequired"] == "1"

    def test_set_course_enrollment_failure(self, builder, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.return_value = {
            "status": False,
            "error_code": 100014,
            "error": "save failed",
        }

        with pytest.raises(RuntimeError, match="设置课程报名失败"):
            builder.set_course_enrollment(group_id="7339916", enabled=True)

    def test_set_course_enrollment_empty_group_id(self, builder, mock_client):
        with pytest.raises(ValueError, match="group_id 不能为空"):
            builder.set_course_enrollment(group_id="", enabled=True)


def _auth_patch(mock_client):
    stack = ExitStack()
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=mock_client))
    stack.enter_context(patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value=None))
    return stack


class TestTeacherEnrollmentTools:
    async def test_tch_set_course_enrollment_tool(self, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.return_value = _enroll_save_response("580237")

        with _auth_patch(mock_client):
            result = json.loads(await tch_set_course_enrollment("7339916", enabled=True))

        assert result["success"] is True
        assert result["data"]["enroll_id"] == "580237"
        assert result["data"]["enabled"] is True

        payload = json.loads(mock_client.post.call_args.kwargs["data"]["enroll"])
        assert payload["status"] == 1

    async def test_tch_set_course_enrollment_tool_invalid_json(self, mock_client):
        with _auth_patch(mock_client):
            result = json.loads(
                await tch_set_course_enrollment("7339916", contact_info_json="not-json")
            )

        assert result["success"] is False
        assert result["error_code"] == "INVALID_JSON"

    async def test_tch_update_course_with_enroll_status_only(self, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.return_value = _enroll_save_response("580237")

        with _auth_patch(mock_client):
            result = json.loads(await tch_update_course("7339916", enroll_status=1))

        assert result["success"] is True
        assert result["data"]["enroll"]["enroll_id"] == "580237"
        assert result["data"]["enroll"]["enabled"] is True
        assert "enroll_status" in result["data"]["changes"]

        # 确认调用的是 saveenroll，不是 e_saveGroup
        calls = mock_client.post.call_args_list
        assert len(calls) == 1
        assert calls[0].args[0] == "https://www.umu.cn/api/enroll/saveenroll"

    async def test_tch_update_course_with_enroll_status_and_title(self, mock_client):
        # get 调用顺序：set_course_enrollment 1 次 + update_course 3 次
        get_responses = [
            _course_info_response(),           # set_course_enrollment 取标题
            _course_info_response(),           # update_course 取现有数据
            _course_info_response(),           # update_course 取原始 groupInfo
            _course_info_response("新标题"),   # update_course 返回更新后详情
        ]
        post_responses = [
            _enroll_save_response("580237"),  # saveenroll
            {  # e_saveGroup
                "status": True,
                "error_code": 0,
                "data": {"groupInfo": {"groupInfo": {"groupId": "7339916"}}},
            },
        ]
        mock_client.get.side_effect = get_responses
        mock_client.post.side_effect = post_responses

        with _auth_patch(mock_client):
            result = json.loads(
                await tch_update_course("7339916", title="新标题", enroll_status=1)
            )

        assert result["success"] is True
        assert result["data"]["enroll"]["enabled"] is True
        assert result["data"]["title"] == "新标题"
        assert "groupTitle" in result["data"]["changes"]
        assert "enroll_status" in result["data"]["changes"]

        calls = mock_client.post.call_args_list
        assert len(calls) == 2
        assert calls[0].args[0] == "https://www.umu.cn/api/enroll/saveenroll"
        assert calls[1].args[0] == "https://www.umu.cn/ajax/e_saveGroup"
