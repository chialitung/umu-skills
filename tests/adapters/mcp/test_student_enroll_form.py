"""Tests for student complex enrollment form helpers."""

from __future__ import annotations

import json
from typing import Any

import pytest

from umu_sdk.adapters.mcp.student import (
    _build_insert_answer_payload,
    _parse_enroll_form,
    _validate_enroll_form,
)


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
