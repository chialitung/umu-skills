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
                    "teacher_id": "20438403",
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
        assert payload["obj_id"] == "7339916"
        assert payload["obj_type"] == "1"
        assert payload["teacher_id"] == "20438403"
        assert payload["title"] == "E2E_测试课程_SCORM"
        assert payload["status"] == "1"
        assert payload["autoCheck"] == "1"
        assert "sessionType" not in payload
        assert payload["setup"]["allow_cancel"] == "0"
        assert payload["setup"]["user_quota"] == "-1"
        assert len(payload["contactInfo"]) == len(CourseBuilder._DEFAULT_ENROLL_CONTACT_INFO)
        assert payload["contactInfo"][0]["placeHolder"] == "输入真实姓名"
        assert payload["setupInfo"]["payment"]["switch_status"] == "0"

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
        assert payload["status"] == "0"
        assert payload["autoCheck"] == "0"
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
                "isSelected": True,
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
        assert payload["contactInfo"][0]["isSelected"] == "1"

    def test_set_course_enrollment_selected_fields(self, builder, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.return_value = _enroll_save_response()

        result = builder.set_course_enrollment(
            group_id="7339916",
            enabled=True,
            selected_contact_fields=["username", "mobile"],
        )

        assert result["enroll_id"] == "580237"
        payload = json.loads(mock_client.post.call_args.kwargs["data"]["enroll"])
        selected = {f["key"]: f["isSelected"] for f in payload["contactInfo"]}
        assert selected["username"] == "1"
        assert selected["mobile"] == "1"
        assert selected["company"] == "0"

    def test_set_course_enrollment_price_and_sections(self, builder, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.return_value = _enroll_save_response()

        section_questions = [
            {
                "title": "单选题",
                "type": "radio",
                "required": True,
                "options": [
                    {"value": "1", "text": "选项1"},
                    {"value": "2", "text": "选项2"},
                ],
            },
            {
                "title": "开放题",
                "type": "text",
                "required": False,
            },
        ]

        result = builder.set_course_enrollment(
            group_id="7339916",
            enabled=True,
            price_amount=1990,
            section_questions=section_questions,
        )

        assert result["enroll_id"] == "580237"
        payload = json.loads(mock_client.post.call_args.kwargs["data"]["enroll"])
        assert payload["payment"]["switch_status"] == 1
        assert payload["payment"]["amount"] == 1990
        assert len(payload["sectionArr"]) == 2

        radio = payload["sectionArr"][0]
        assert radio["questionInfo"]["domType"] == "radio"
        assert radio["questionInfo"]["questionTitle"] == "单选题"
        assert radio["questionInfo"]["setup"]["required"] == "0"
        assert radio["answerArr"] == [{"answerContent": "选项1"}, {"answerContent": "选项2"}]
        assert radio["questionInfo"]["extend"]["pic_url"] == []

        textarea = payload["sectionArr"][1]
        assert textarea["questionInfo"]["domType"] == "textarea"
        assert textarea["questionInfo"]["setup"]["required"] == "1"

    def test_set_course_enrollment_approval_setting(self, builder, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.side_effect = [
            _enroll_save_response("580240"),
            {"error_code": 0, "error_message": "", "data": {"status": 1}},
        ]

        result = builder.set_course_enrollment(
            group_id="7339916",
            enabled=True,
            approval_setting={
                "course_manager": True,
                "department_manager": False,
                "designee": False,
            },
        )

        assert result["enroll_id"] == "580240"
        calls = mock_client.post.call_args_list
        assert len(calls) == 2
        assert calls[1].args[0] == "https://www.umu.cn/uapi/v1/enroll/save-approval-setting"
        setting = json.loads(calls[1].kwargs["data"]["setting"])
        assert setting["manager_permission"] == 1
        assert setting["department_manager_permission"] == 0
        assert setting["designee_permission"] == 0

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
        assert payload["status"] == "1"

    async def test_tch_set_course_enrollment_tool_invalid_json(self, mock_client):
        with _auth_patch(mock_client):
            result = json.loads(
                await tch_set_course_enrollment("7339916", contact_info_json="not-json")
            )

        assert result["success"] is False
        assert result["error_code"] == "INVALID_JSON"

    async def test_tch_set_course_enrollment_tool_full_params(self, mock_client):
        mock_client.get.return_value = _course_info_response()
        mock_client.post.side_effect = [
            _enroll_save_response("580237"),
            {"error_code": 0, "error_message": "", "data": {"status": 1}},
        ]

        with _auth_patch(mock_client):
            result = json.loads(
                await tch_set_course_enrollment(
                    "7339916",
                    enabled=True,
                    auto_check=True,
                    title="报名标题",
                    desc="报名说明",
                    allow_cancel=True,
                    user_quota=50,
                    price_amount=100,
                    selected_contact_fields='["username", "mobile"]',
                    section_questions_json='[{"title": "单选题", "type": "radio", "required": true, "options": [{"value": "1", "text": "A"}]}]',
                    approval_setting_json='{"course_manager": true, "department_manager": false}',
                    enroll_id="580237",
                )
            )

        assert result["success"] is True
        assert result["data"]["enroll_id"] == "580237"

        calls = mock_client.post.call_args_list
        assert len(calls) == 2
        assert calls[0].args[0] == "https://www.umu.cn/api/enroll/saveenroll"
        assert calls[1].args[0] == "https://www.umu.cn/uapi/v1/enroll/save-approval-setting"

        payload = json.loads(calls[0].kwargs["data"]["enroll"])
        assert payload["title"] == "报名标题"
        assert payload["desc"] == "报名说明"
        assert payload["status"] == "1"
        assert payload["payment"]["amount"] == 100
        assert payload["setup"]["allow_cancel"] == "1"
        assert payload["setup"]["user_quota"] == "50"
        assert len(payload["sectionArr"]) == 1
        assert payload["sectionArr"][0]["answerArr"] == [{"answerContent": "A"}]
        assert payload["sectionArr"][0]["questionInfo"]["setup"]["required"] == "0"

        selected = {f["key"]: f["isSelected"] for f in payload["contactInfo"]}
        assert selected["username"] == "1"
        assert selected["mobile"] == "1"

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
