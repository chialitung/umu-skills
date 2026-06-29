"""Tests for complex sign-in section creation/update and student completion."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.course_builder import CourseBuilder
from umu_sdk.adapters.mcp.student import stu_check_in_with_answers
from umu_sdk.adapters.mcp.teacher import tch_create_signin_section, tch_update_signin_section


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.base_url = "https://www.umu.cn"
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    client.mobile_url.side_effect = lambda path: f"https://m.umu.cn{path}"
    client.auth.get_auth_headers.return_value = {"Authorization": "Bearer token"}
    client.auth.is_authenticated.return_value = True
    return client


@pytest.fixture
def builder(mock_client):
    return CourseBuilder(mock_client)


@contextmanager
def _patch_teacher_auth(mock_client):
    with patch("umu_sdk.adapters.mcp.teacher._get_client", return_value=mock_client), \
            patch("umu_sdk.adapters.mcp.teacher._require_auth", return_value=None):
        yield


@contextmanager
def _patch_student_client(mock_client):
    with patch("umu_sdk.adapters.mcp.student._get_client", return_value=mock_client):
        yield


def _savesession_response(session_id: str = "262285000"):
    return {
        "status": True,
        "error_code": 0,
        "error": "success",
        "data": {"session_id": session_id},
    }


def _signin_page_data() -> dict[str, Any]:
    """Mobile sign-in page data used by _fetch_signin_questions."""
    return {
        "info": {
            "sessionArr": {
                "sectionArr": [
                    {
                        "questionInfo": {
                            "id": "262285100",
                            "questionId": "262285100",
                            "questionTitle": "请输入姓名",
                            "domType": "textarea",
                            "setup": {"required": "1"},
                        },
                        "answerArr": [{"id": "171199900", "answerId": "171199900"}],
                    },
                    {
                        "questionInfo": {
                            "id": "262285101",
                            "questionId": "262285101",
                            "questionTitle": "请选择部门",
                            "domType": "radio",
                            "setup": {"required": "0"},
                        },
                        "answerArr": [
                            {"id": "171199901", "answerId": "171199901", "answerContent": "研发"},
                            {"id": "171199902", "answerId": "171199902", "answerContent": "销售"},
                        ],
                    },
                    {
                        "questionInfo": {
                            "id": "262285102",
                            "questionId": "262285102",
                            "questionTitle": "选择兴趣",
                            "domType": "checkbox",
                            "setup": {"required": "1"},
                        },
                        "answerArr": [
                            {"id": "171199903", "answerId": "171199903", "answerContent": "技术"},
                            {"id": "171199904", "answerId": "171199904", "answerContent": "管理"},
                        ],
                    },
                    {
                        "questionInfo": {
                            "id": "262285103",
                            "questionId": "262285103",
                            "questionTitle": "工作年限",
                            "domType": "number",
                            "setup": {"required": "0", "defaultValue": 3},
                            "extend": {"min": 0, "max": 50},
                        },
                        "answerArr": [],
                    },
                ]
            }
        }
    }


class TestCreateSigninSection:
    def test_creates_all_question_types(self, builder, mock_client):
        mock_client.post.return_value = _savesession_response("262285000")

        result = builder.create_signin_section(
            group_id="7343000",
            session_title="复杂签到测试",
            signin_info_list=[
                {"type": "textarea", "title": "请输入姓名", "required": True, "hint": "真实姓名"},
                {"type": "radio", "title": "请选择部门", "required": True, "options": ["研发", "销售"]},
                {"type": "checkbox", "title": "选择兴趣", "required": True, "options": ["技术", "管理"], "min_options": 1, "max_options": 2},
                {"type": "number", "title": "工作年限", "required": False, "min": 0, "max": 50, "default": 3},
                {"type": "paragraph", "content": "<p>说明文字</p>"},
            ],
        )

        assert result["session_id"] == "262285000"
        assert result["signin_info_count"] == 5

        payload = json.loads(mock_client.post.call_args.kwargs["data"]["session_data"])
        session_info = payload["sessionInfo"]
        assert session_info["sessionType"] == "6"
        assert session_info["setup"]["advance"] == 1
        assert session_info["autoCheck"] == 1

        sections = payload["sectionArr"]
        assert len(sections) == 5

        textarea = sections[0]
        assert textarea["questionInfo"]["domType"] == "textarea"
        assert textarea["questionInfo"]["pattern"] == "3"
        assert textarea["questionInfo"]["setup"]["required"] == "0"
        assert textarea["answerArr"][0]["answerContent"] == "真实姓名"

        radio = sections[1]
        assert radio["questionInfo"]["domType"] == "radio"
        assert radio["questionInfo"]["pattern"] == "0"
        assert radio["questionInfo"]["setup"]["required"] == "0"
        assert len(radio["answerArr"]) == 2

        checkbox = sections[2]
        assert checkbox["questionInfo"]["domType"] == "checkbox"
        assert checkbox["questionInfo"]["pattern"] == "1"
        assert checkbox["questionInfo"]["setup"]["required"] == "0"
        assert checkbox["questionInfo"]["setup"]["limitOptionsMin"] == 1
        assert checkbox["questionInfo"]["setup"]["limitOptionsMax"] == 2
        # trailing empty option
        assert checkbox["answerArr"][-1]["answerContent"] == ""

        number = sections[3]
        assert number["questionInfo"]["domType"] == "number"
        assert number["questionInfo"]["pattern"] == "8"
        assert number["questionInfo"]["extend"]["min"] == 0
        assert number["questionInfo"]["extend"]["max"] == 50
        assert number["questionInfo"]["setup"]["required"] == "1"
        assert number["questionInfo"]["setup"]["defaultValue"] == 3

        paragraph = sections[4]
        assert paragraph["questionInfo"]["domType"] == "paragraph"
        assert paragraph["questionInfo"]["pattern"] == "4"
        assert paragraph["questionInfo"]["desc"] == "<p>说明文字</p>"

    def test_required_semantics_inverted(self, builder, mock_client):
        mock_client.post.return_value = _savesession_response()

        builder.create_signin_section(
            group_id="7343000",
            session_title="语义测试",
            signin_info_list=[
                {"type": "radio", "title": "必填", "required": True, "options": ["A"]},
                {"type": "radio", "title": "选填", "required": False, "options": ["B"]},
            ],
        )

        payload = json.loads(mock_client.post.call_args.kwargs["data"]["session_data"])
        sections = payload["sectionArr"]
        assert sections[0]["questionInfo"]["setup"]["required"] == "0"
        assert sections[1]["questionInfo"]["setup"]["required"] == "1"

    def test_empty_signin_info_raises(self, builder, mock_client):
        with pytest.raises(ValueError, match="signin_info_list 不能为空"):
            builder.create_signin_section(
                group_id="7343000",
                session_title="空签到",
                signin_info_list=[],
            )


class TestUpdateSigninSection:
    def _session_detail(self, with_number: bool = False) -> dict[str, Any]:
        sections = [
            {
                "questionInfo": {
                    "questionId": "262285200",
                    "questionTitle": "旧文本",
                    "domType": "textarea",
                    "pattern": "3",
                    "setup": {"required": "0"},
                },
                "answerArr": [{"answerId": "171199910", "answerContent": ""}],
            },
            {
                "questionInfo": {
                    "questionId": "262285201",
                    "questionTitle": "旧单选",
                    "domType": "radio",
                    "pattern": "0",
                    "setup": {"required": "0"},
                },
                "answerArr": [
                    {"answerId": "171199911", "answerContent": "A"},
                    {"answerId": "171199912", "answerContent": "B"},
                ],
            },
        ]
        if with_number:
            sections.append({
                "questionInfo": {
                    "questionId": "262285202",
                    "questionTitle": "旧数值",
                    "domType": "number",
                    "pattern": "8",
                    "setup": {"required": "0", "defaultValue": 1},
                    "extend": {"min": 0, "max": 10},
                },
                "answerArr": [],
            })
        return {
            "status": True,
            "error_code": 0,
            "data": {
                "info": {
                    "sessionInfo": {
                        "sessionId": "262285000",
                        "sessionTitle": "旧签到",
                        "sessionType": "6",
                        "autoCheck": 1,
                        "is_require": 1,
                        "point_ratio": 1,
                        "setup": {"advance": 1, "type_name": "签到"},
                        "multimedia_id": 0,
                    },
                    "sectionArr": sections,
                }
            }
        }

    def test_update_preserves_ids_for_same_type(self, builder, mock_client):
        mock_client.get.return_value = self._session_detail()
        mock_client.post.return_value = _savesession_response("262285000")

        result = builder.update_signin_section(
            group_id="7343000",
            session_id="262285000",
            session_title="更新后签到",
            signin_info_list=[
                {"type": "textarea", "title": "新文本", "required": True},
                {"type": "radio", "title": "新单选", "required": True, "options": ["A", "B"]},
            ],
        )

        assert result["session_id"] == "262285000"
        payload = json.loads(mock_client.post.call_args.kwargs["data"]["session_data"])
        sections = payload["sectionArr"]
        assert sections[0]["questionInfo"]["questionId"] == "262285200"
        assert sections[1]["questionInfo"]["questionId"] == "262285201"
        assert sections[1]["answerArr"][0]["answerId"] == "171199911"

    def test_update_replaces_type_and_adds_number(self, builder, mock_client):
        mock_client.get.return_value = self._session_detail()
        mock_client.post.return_value = _savesession_response("262285000")

        builder.update_signin_section(
            group_id="7343000",
            session_id="262285000",
            signin_info_list=[
                {"type": "number", "title": "新数值", "required": True, "min": 1, "max": 10, "default": 5},
            ],
        )

        payload = json.loads(mock_client.post.call_args.kwargs["data"]["session_data"])
        sections = payload["sectionArr"]
        assert len(sections) == 1
        assert sections[0]["questionInfo"]["domType"] == "number"
        assert sections[0]["questionInfo"]["pattern"] == "8"
        assert sections[0]["questionInfo"]["questionId"] == ""

    def test_update_enables_advance_when_missing(self, builder, mock_client):
        detail = self._session_detail()
        detail["data"]["info"]["sessionInfo"]["setup"]["advance"] = 0
        mock_client.get.return_value = detail
        mock_client.post.return_value = _savesession_response()

        result = builder.update_signin_section(
            group_id="7343000",
            session_id="262285000",
            signin_info_list=[
                {"type": "textarea", "title": "文本", "required": True},
            ],
        )

        assert "advance: 1" in result["changes"]
        payload = json.loads(mock_client.post.call_args.kwargs["data"]["session_data"])
        assert payload["sessionInfo"]["setup"]["advance"] == 1


class TestTeacherSigninTools:
    @pytest.mark.asyncio
    async def test_tch_create_signin_section_tool(self, mock_client):
        mock_client.post.return_value = _savesession_response("262285300")

        with _patch_teacher_auth(mock_client):
            result = json.loads(await tch_create_signin_section(
                group_id="7343000",
                session_title="工具签到",
                signin_info_json=json.dumps([
                    {"type": "number", "title": "年限", "required": True, "min": 0, "max": 30, "default": 1},
                ]),
            ))

        assert result["success"] is True
        assert result["data"]["session_id"] == "262285300"

    @pytest.mark.asyncio
    async def test_tch_update_signin_section_tool(self, mock_client):
        mock_client.get.return_value = {
            "status": True,
            "error_code": 0,
            "data": {
                "info": {
                    "sessionInfo": {
                        "sessionId": "262285301",
                        "sessionTitle": "旧",
                        "sessionType": "6",
                        "autoCheck": 1,
                        "is_require": 1,
                        "point_ratio": 1,
                        "setup": {"advance": 1, "type_name": "签到"},
                        "multimedia_id": 0,
                    },
                    "sectionArr": [],
                }
            },
        }
        mock_client.post.return_value = _savesession_response("262285301")

        with _patch_teacher_auth(mock_client):
            result = json.loads(await tch_update_signin_section(
                group_id="7343000",
                session_id="262285301",
                session_title="新",
                signin_info_json=json.dumps([
                    {"type": "radio", "title": "单选", "required": True, "options": ["A", "B"]},
                ]),
            ))

        assert result["success"] is True
        assert result["data"]["session_id"] == "262285301"


class TestStudentCheckInWithAnswers:
    @pytest.mark.asyncio
    async def test_auto_discovery_submits_correct_payload(self, mock_client):
        # element info returns share URL
        mock_client.get.return_value = {"status": True, "error_code": 0, "data": {"share_url": "https://m.umu.cn/ssu_abc123"}}
        mock_client.post.side_effect = [
            {"status": True, "error_code": 0, "data": {}},  # insertAnswer
            {"status": True, "error_code": 0, "data": {}},  # makeweikestatus sequence returns multiple times
            {"status": True, "error_code": 0, "data": {}},
            {"status": True, "error_code": 0, "data": {}},
        ]

        with _patch_student_client(mock_client), \
                patch("umu_sdk.tools.shared.learning_helpers._get_html", return_value="<html></html>"), \
                patch("umu_sdk.tools.shared.learning_helpers._extract_signin_page_data_json", return_value=_signin_page_data()):
            result = json.loads(await stu_check_in_with_answers(
                element_id="262285400",
                answers_json=json.dumps([
                    {"type": "textarea", "text": "张三"},
                    {"type": "radio", "answer_id": "171199901"},
                    {"type": "checkbox", "answer_ids": ["171199903", "171199904"]},
                    {"type": "number", "value": 5},
                ]),
            ))

        assert result["success"] is True
        assert result["data"]["action"] == "complex_checkin_completed"

        insert_call = mock_client.post.call_args_list[0]
        assert insert_call.args[0] == "https://m.umu.cn/ajax/insertAnswer"
        q_payload = json.loads(insert_call.args[1]["q"])
        assert q_payload["answerList"] == ["171199901", "171199903", "171199904"]
        assert q_payload["answerInfo"] == [{"id": "262285100", "text": "张三"}]
        assert q_payload["answerNumber"] == {"262285103": 5}
        assert q_payload["sessionId"] == "262285400"

    @pytest.mark.asyncio
    async def test_explicit_question_ids_skip_discovery(self, mock_client):
        mock_client.post.side_effect = [
            {"status": True, "error_code": 0, "data": {}},
            {"status": True, "error_code": 0, "data": {}},
            {"status": True, "error_code": 0, "data": {}},
            {"status": True, "error_code": 0, "data": {}},
        ]

        with _patch_student_client(mock_client):
            result = json.loads(await stu_check_in_with_answers(
                element_id="262285400",
                answers_json=json.dumps([
                    {"question_id": "q1", "type": "textarea", "text": "text"},
                    {"question_id": "q2", "type": "radio", "answer_id": "a1"},
                    {"question_id": "q3", "type": "checkbox", "answer_ids": ["a2", "a3"]},
                    {"question_id": "q4", "type": "number", "value": 10},
                ]),
            ))

        assert result["success"] is True
        q_payload = json.loads(mock_client.post.call_args_list[0].args[1]["q"])
        assert q_payload["answerList"] == ["a1", "a2", "a3"]
        assert q_payload["answerInfo"] == [{"id": "q1", "text": "text"}]
        assert q_payload["answerNumber"] == {"q4": 10}

    @pytest.mark.asyncio
    async def test_invalid_json_returns_error(self, mock_client):
        with _patch_student_client(mock_client):
            result = json.loads(await stu_check_in_with_answers(
                element_id="262285400",
                answers_json="not json",
            ))

        assert result["success"] is False
        assert result["error_code"] == "INVALID_ANSWERS_JSON"

    @pytest.mark.asyncio
    async def test_missing_answer_id_returns_error(self, mock_client):
        with _patch_student_client(mock_client):
            result = json.loads(await stu_check_in_with_answers(
                element_id="262285400",
                answers_json=json.dumps([
                    {"question_id": "q1", "type": "radio"},
                ]),
            ))

        assert result["success"] is False
        assert result["error_code"] == "INVALID_ANSWERS"
