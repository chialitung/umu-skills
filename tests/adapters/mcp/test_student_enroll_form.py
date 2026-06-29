"""Tests for student complex enrollment form helpers."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.student import (
    _build_insert_answer_payload,
    _check_needs_enroll_form,
    _parse_enroll_form,
    _validate_enroll_form,
)


@contextmanager
def _patch_student_client():
    """模拟 Student MCP 客户端，返回可断言的 MagicMock。"""
    client = MagicMock()
    client.desktop_url.side_effect = lambda path: f"https://www.umu.cn{path}"
    client.mobile_url.side_effect = lambda path: f"https://m.umu.cn{path}"
    client.auth.get_auth_headers.return_value = {"Authorization": "Bearer token"}
    client.auth.is_authenticated.return_value = True

    with patch("umu_sdk.adapters.mcp.student._get_client", return_value=client):
        yield client


@pytest.fixture
def sample_page_data_with_number() -> dict[str, Any]:
    """pageData containing a number-type section question (answerArr empty)."""
    return {
        "data": {
            "info": {
                "enroll": {
                    "enrollId": "580263",
                    "shareUrl": "https://m.umu.cn/sse_2qX59938",
                    "contactInfo": [
                        {
                            "questionTitle": "姓名",
                            "domType": "text",
                            "key": "username",
                            "isRequired": "1",
                            "isSelected": "1",
                            "questionDefaultValue": [{"value": "", "text": ""}],
                        }
                    ],
                    "sectionArr": [
                        {
                            "questionInfo": {
                                "questionId": "262285534",
                                "questionTitle": "必填数值型",
                                "domType": "number",
                                "desc": "",
                                "setup": {"required": "0", "defaultValue": 3},
                                "extend": {"min": 1, "max": 5},
                            },
                            "answerArr": [],
                        },
                        {
                            "questionInfo": {
                                "questionId": "262285535",
                                "questionTitle": "选填数值型",
                                "domType": "number",
                                "desc": "",
                                "setup": {"required": "1", "defaultValue": 5},
                                "extend": {"min": 0, "max": 10},
                            },
                            "answerArr": [],
                        },
                    ],
                }
            }
        }
    }


@pytest.fixture
def sample_page_data() -> dict[str, Any]:
    """Minimal pageData containing contactInfo and sectionArr."""
    return {
        "data": {
            "info": {
                "enroll": {
                    "enrollId": "580260",
                    "shareUrl": "https://m.umu.cn/sse_2qX29ed3",
                    "contactInfo": [
                        {
                            "questionTitle": "姓名",
                            "domType": "text",
                            "key": "username",
                            "isRequired": "1",
                            "isSelected": "1",
                            "questionDefaultValue": [{"value": "", "text": ""}],
                        },
                        {
                            "questionTitle": "公司",
                            "domType": "text",
                            "key": "company",
                            "isRequired": "1",
                            "isSelected": "1",
                            "questionDefaultValue": [{"value": "", "text": ""}],
                        },
                        {
                            "questionTitle": "部门",
                            "domType": "text",
                            "key": "department",
                            "isRequired": "1",
                            "isSelected": "0",
                            "questionDefaultValue": [{"value": "", "text": ""}],
                        },
                    ],
                    "sectionArr": [
                        {
                            "questionInfo": {
                                "questionId": "262285231",
                                "domType": "paragraph",
                                "desc": "说明文字",
                                "setup": {"required": "0"},
                            },
                            "answerArr": [],
                        },
                        {
                            "questionInfo": {
                                "questionId": "262285229",
                                "questionTitle": "输入你的职场地址",
                                "domType": "textarea",
                                "setup": {"required": "1"},
                            },
                            "answerArr": [
                                {"answerId": "171198747", "answerContent": "请输入内容"}
                            ],
                        },
                        {
                            "questionInfo": {
                                "questionId": "262285230",
                                "questionTitle": "选择你的部门",
                                "domType": "radio",
                                "setup": {"required": "0"},
                            },
                            "answerArr": [
                                {"answerId": "171198748", "answerContent": "财务部"},
                                {"answerId": "171198749", "answerContent": "人事部"},
                                {"answerId": "171198750", "answerContent": "行政部"},
                            ],
                        },
                        {
                            "questionInfo": {
                                "questionId": "262285232",
                                "questionTitle": "你希望学习什么",
                                "domType": "checkbox",
                                "setup": {
                                    "required": "1",
                                    "limitOptionsMin": 2,
                                    "limitOptionsMax": 3,
                                },
                            },
                            "answerArr": [
                                {"answerId": "171198751", "answerContent": "通用力"},
                                {"answerId": "171198752", "answerContent": "领导力"},
                                {"answerId": "171198753", "answerContent": "专业力"},
                            ],
                        },
                    ],
                }
            }
        }
    }


class TestParseEnrollForm:
    def test_parses_contact_fields(self, sample_page_data: dict[str, Any]) -> None:
        form = _parse_enroll_form(sample_page_data)
        fields = form["contact_fields"]
        assert len(fields) == 3
        assert fields[0]["key"] == "username"
        assert fields[0]["selected"] is True
        assert fields[2]["key"] == "department"
        assert fields[2]["selected"] is False

    def test_parses_section_questions(self, sample_page_data: dict[str, Any]) -> None:
        form = _parse_enroll_form(sample_page_data)
        questions = form["section_questions"]
        assert len(questions) == 4

        # paragraph: no answer required
        assert questions[0]["type"] == "paragraph"
        assert questions[0]["required"] is False

        # textarea with setup.required="1" is optional (inverted semantics)
        assert questions[1]["type"] == "textarea"
        assert questions[1]["required"] is False
        assert questions[1]["answer_id"] == "171198747"

        # radio with setup.required="0" is required (inverted semantics)
        assert questions[2]["type"] == "radio"
        assert questions[2]["required"] is True
        assert len(questions[2]["options"]) == 3

        # checkbox with setup.required="1" is optional (inverted semantics)
        assert questions[3]["type"] == "checkbox"
        assert questions[3]["required"] is False
        assert questions[3]["min_options"] == 2
        assert questions[3]["max_options"] == 3


class TestParseNumberQuestion:
    def test_parses_number_question(
        self, sample_page_data_with_number: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data_with_number)
        questions = form["section_questions"]
        assert len(questions) == 2

        q0, q1 = questions
        assert q0["type"] == "number"
        assert q0["required"] is True
        assert q0["min"] == 1
        assert q0["max"] == 5
        assert q0.get("answer_id") in (None, "")

        assert q1["type"] == "number"
        assert q1["required"] is False
        assert q1["min"] == 0
        assert q1["max"] == 10


class TestValidateNumberQuestion:
    def test_required_number_missing_fails(
        self, sample_page_data_with_number: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data_with_number)
        err = _validate_enroll_form(
            form["contact_fields"],
            form["section_questions"],
            contact_answers={"username": "student"},
            section_answers=[],
        )
        assert err is not None
        assert "必填数值型" in err

    def test_number_below_min_fails(
        self, sample_page_data_with_number: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data_with_number)
        err = _validate_enroll_form(
            form["contact_fields"],
            form["section_questions"],
            contact_answers={"username": "student"},
            section_answers=[{"question_id": "262285534", "number": 0}],
        )
        assert err is not None
        assert "1" in err

    def test_number_above_max_fails(
        self, sample_page_data_with_number: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data_with_number)
        err = _validate_enroll_form(
            form["contact_fields"],
            form["section_questions"],
            contact_answers={"username": "student"},
            section_answers=[{"question_id": "262285534", "number": 6}],
        )
        assert err is not None
        assert "5" in err

    def test_valid_number_passes(
        self, sample_page_data_with_number: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data_with_number)
        err = _validate_enroll_form(
            form["contact_fields"],
            form["section_questions"],
            contact_answers={"username": "student"},
            section_answers=[
                {"question_id": "262285534", "number": 3},
                {"question_id": "262285535", "number": 7},
            ],
        )
        assert err is None


class TestBuildInsertAnswerPayloadNumber:
    def test_uses_question_id_and_string_value(
        self, sample_page_data_with_number: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data_with_number)
        payload = _build_insert_answer_payload(
            form["section_questions"],
            [
                {"question_id": "262285534", "number": 4},
                {"question_id": "262285535", "number": 3},
            ],
            "580263",
        )
        assert payload["answerNumber"] == {
            "262285534": "4",
            "262285535": "3",
        }


class TestValidateEnrollForm:
    def test_missing_required_contact_fails(
        self, sample_page_data: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data)
        err = _validate_enroll_form(
            form["contact_fields"],
            form["section_questions"],
            contact_answers={"username": "student"},  # missing company
            section_answers=[],
        )
        assert err is not None
        assert "公司" in err

    def test_missing_required_section_fails(
        self, sample_page_data: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data)
        err = _validate_enroll_form(
            form["contact_fields"],
            form["section_questions"],
            contact_answers={"username": "student", "company": "UMU"},
            section_answers=[],  # missing required radio "department"
        )
        assert err is not None
        assert "部门" in err

    def test_checkbox_min_limit_fails(
        self, sample_page_data: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data)
        err = _validate_enroll_form(
            form["contact_fields"],
            form["section_questions"],
            contact_answers={"username": "student", "company": "UMU"},
            section_answers=[
                {"question_id": "262285230", "answer_id": "171198750"},
                {"question_id": "262285232", "answer_ids": ["171198751"]},  # min 2
            ],
        )
        assert err is not None
        assert "至少" in err

    def test_valid_answers_pass(
        self, sample_page_data: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data)
        err = _validate_enroll_form(
            form["contact_fields"],
            form["section_questions"],
            contact_answers={"username": "student", "company": "UMU"},
            section_answers=[
                {"question_id": "262285229", "text": "杭州"},
                {"question_id": "262285230", "answer_id": "171198750"},
                {"question_id": "262285232", "answer_ids": ["171198751", "171198752"]},
            ],
        )
        assert err is None


class TestBuildInsertAnswerPayload:
    def test_builds_payload(self, sample_page_data: dict[str, Any]) -> None:
        form = _parse_enroll_form(sample_page_data)
        payload = _build_insert_answer_payload(
            form["section_questions"],
            [
                {"question_id": "262285229", "text": "杭州阿里园区"},
                {"question_id": "262285230", "answer_id": "171198750"},
                {"question_id": "262285232", "answer_ids": ["171198751", "171198752"]},
            ],
            "580260",
        )
        assert payload["answerList"] == ["171198750", "171198751", "171198752"]
        assert payload["answerInfo"] == [
            {"id": "171198747", "text": "杭州阿里园区"}
        ]
        assert payload["answerNumber"] == {}
        assert payload["sessionId"] == 0
        assert payload["enrollId"] == "580260"

    def test_payload_json_serializable(
        self, sample_page_data: dict[str, Any]
    ) -> None:
        form = _parse_enroll_form(sample_page_data)
        payload = _build_insert_answer_payload(
            form["section_questions"],
            [
                {"question_id": "262285229", "text": "杭州阿里园区"},
                {"question_id": "262285230", "answer_id": "171198750"},
            ],
            "580260",
        )
        # The payload must be serializable to the format used by /ajax/insertAnswer
        serialized = json.dumps(payload, ensure_ascii=False)
        parsed = json.loads(serialized)
        assert parsed["enrollId"] == "580260"


class TestCheckNeedsEnrollForm:
    def test_no_form_fields_returns_false(self) -> None:
        client = MagicMock()
        parsed_form = {
            "enroll_id": "580260",
            "contact_fields": [],
            "section_questions": [],
        }

        with patch(
            "umu_sdk.tools.shared.learning_helpers._get_enroll_short_url",
            return_value=("580260", "https://m.umu.cn/sse_xxx"),
        ), patch(
            "umu_sdk.tools.shared.learning_helpers._fetch_enroll_form_page",
            return_value={"data": {}},
        ), patch(
            "umu_sdk.tools.shared.learning_helpers._parse_enroll_form",
            return_value=parsed_form,
        ):
            needs_form, summary = _check_needs_enroll_form(client, "g1", "sk1")

        assert needs_form is False
        assert summary is None

    def test_selected_contact_fields_return_true(self) -> None:
        client = MagicMock()
        parsed_form = {
            "enroll_id": "580260",
            "contact_fields": [
                {"key": "username", "title": "姓名", "selected": True},
            ],
            "section_questions": [],
        }

        with patch(
            "umu_sdk.tools.shared.learning_helpers._get_enroll_short_url",
            return_value=("580260", "https://m.umu.cn/sse_xxx"),
        ), patch(
            "umu_sdk.tools.shared.learning_helpers._fetch_enroll_form_page",
            return_value={"data": {}},
        ), patch(
            "umu_sdk.tools.shared.learning_helpers._parse_enroll_form",
            return_value=parsed_form,
        ):
            needs_form, summary = _check_needs_enroll_form(client, "g1", "sk1")

        assert needs_form is True
        assert summary is not None
        assert summary["contact_fields"][0]["key"] == "username"

    def test_section_questions_return_true(self) -> None:
        client = MagicMock()
        parsed_form = {
            "enroll_id": "580260",
            "contact_fields": [],
            "section_questions": [
                {"question_id": "q1", "title": "选择部门", "type": "radio"},
            ],
        }

        with patch(
            "umu_sdk.tools.shared.learning_helpers._get_enroll_short_url",
            return_value=("580260", "https://m.umu.cn/sse_xxx"),
        ), patch(
            "umu_sdk.tools.shared.learning_helpers._fetch_enroll_form_page",
            return_value={"data": {}},
        ), patch(
            "umu_sdk.tools.shared.learning_helpers._parse_enroll_form",
            return_value=parsed_form,
        ):
            needs_form, summary = _check_needs_enroll_form(client, "g1", "sk1")

        assert needs_form is True
        assert summary is not None
        assert summary["section_questions"][0]["type"] == "radio"


class TestStuEnrollCourseFormDetection:
    @pytest.mark.asyncio
    async def test_simple_enroll_returns_proceed(self) -> None:
        from umu_sdk.adapters.mcp.student import stu_enroll_course

        with _patch_student_client() as client:
            client.post.return_value = {
                "error_code": 0,
                "data": {"is_enrolled": 2, "pay_status": "success"},
            }

            result = await stu_enroll_course(enroll_id="580260")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["is_enrolled"] == 2
        assert parsed["data"]["enroll_form_required"] is False
        assert parsed["next_action"] == "proceed"

    @pytest.mark.asyncio
    async def test_pre_enroll_with_form_returns_needs_enroll_form(self) -> None:
        from umu_sdk.adapters.mcp.student import stu_enroll_course

        form_summary = {
            "enroll_id": "580260",
            "contact_fields": [{"key": "username", "title": "姓名", "selected": True}],
            "section_questions": [],
        }

        with _patch_student_client() as client, \
                patch(
                    "umu_sdk.tools.operations.learning._resolve_course_identifier",
                    return_value=("g1", "sk1", "https://m.umu.cn/course?groupId=g1&sKey=sk1"),
                ), \
                patch(
                    "umu_sdk.tools.operations.learning._check_needs_enroll_form",
                    return_value=(True, form_summary),
                ):
            client.post.return_value = {
                "error_code": 0,
                "data": {"is_enrolled": 1, "pay_status": "pay"},
            }

            result = await stu_enroll_course(
                enroll_id="580260",
                course_identifier="bei162",
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["is_enrolled"] == 1
        assert parsed["data"]["enroll_form_required"] is True
        assert parsed["data"]["enroll_form_summary"] == form_summary
        assert parsed["next_action"] == "needs_enroll_form"

    @pytest.mark.asyncio
    async def test_pre_enroll_without_form_returns_proceed(self) -> None:
        from umu_sdk.adapters.mcp.student import stu_enroll_course

        with _patch_student_client() as client, \
                patch(
                    "umu_sdk.tools.operations.learning._resolve_course_identifier",
                    return_value=("g1", "sk1", "https://m.umu.cn/course?groupId=g1&sKey=sk1"),
                ), \
                patch(
                    "umu_sdk.tools.operations.learning._check_needs_enroll_form",
                    return_value=(False, None),
                ):
            client.post.return_value = {
                "error_code": 0,
                "data": {"is_enrolled": 1, "pay_status": "pay"},
            }

            result = await stu_enroll_course(
                enroll_id="580260",
                course_identifier="bei162",
            )

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["enroll_form_required"] is False
        assert parsed["next_action"] == "proceed"


class TestStuGetCourseStructureFormDetection:
    @pytest.mark.asyncio
    async def test_needs_enrollment_and_form(self) -> None:
        from umu_sdk.adapters.mcp.student import stu_get_course_structure

        form_summary = {
            "enroll_id": "580260",
            "contact_fields": [{"key": "username", "title": "姓名", "selected": True}],
            "section_questions": [],
        }

        with _patch_student_client() as client, \
                patch(
                    "umu_sdk.tools.operations.learning._resolve_course_identifier",
                    return_value=("g1", "sk1", "https://m.umu.cn/course?groupId=g1&sKey=sk1"),
                ), \
                patch(
                    "umu_sdk.tools.operations.learning._check_needs_enroll",
                    return_value=(True, "580260"),
                ), \
                patch(
                    "umu_sdk.tools.operations.learning._check_needs_enroll_form",
                    return_value=(True, form_summary),
                ):
            client.get.return_value = {"error_code": 0, "data": {"list": []}}

            result = await stu_get_course_structure(course_identifier="bei162")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["needs_enrollment"] is True
        assert parsed["data"]["enroll_form_required"] is True
        assert parsed["data"]["enroll_form_summary"] == form_summary
        assert parsed["next_action"] == "needs_enroll_form"


class TestStuCompleteCourseFormDetection:
    @pytest.mark.asyncio
    async def test_pre_enroll_with_form_stops_before_learning(self) -> None:
        from umu_sdk.adapters.mcp.student import stu_complete_course

        form_summary = {
            "enroll_id": "580260",
            "contact_fields": [{"key": "username", "title": "姓名", "selected": True}],
            "section_questions": [],
        }

        with _patch_student_client() as client, \
                patch(
                    "umu_sdk.tools.operations.learning._resolve_course_identifier",
                    return_value=("g1", "sk1", "https://m.umu.cn/course?groupId=g1&sKey=sk1"),
                ), \
                patch(
                    "umu_sdk.tools.operations.learning._get_course_structure_impl",
                    return_value={
                        "group_id": "g1",
                        "s_key": "sk1",
                        "enrollment_status": "needs_enrollment",
                        "needs_enrollment": True,
                        "enroll_id": "580260",
                        "is_enrolled": 1,
                        "lessons": [],
                    },
                ), \
                patch(
                    "umu_sdk.tools.operations.learning._check_needs_enroll_form",
                    return_value=(True, form_summary),
                ):
            client.post.return_value = {
                "error_code": 0,
                "data": {"is_enrolled": 1, "pay_status": "pay"},
            }

            result = await stu_complete_course(course_identifier="bei162")

        parsed = json.loads(result)
        assert parsed["success"] is True
        assert parsed["data"]["enroll_form_required"] is True
        assert parsed["data"]["enroll_form_summary"] == form_summary
        assert parsed["next_action"] == "needs_enroll_form"
        # 不应继续获取课程小节列表
        assert not client.get.called
