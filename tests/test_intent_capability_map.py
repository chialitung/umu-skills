"""IntentCapabilityMap 单元测试."""

from __future__ import annotations

import pytest

from umu_sdk.skills.intent_capability_map import IntentCapabilityMap


class TestClassify:
    @pytest.mark.parametrize(
        ("intent", "expected"),
        [
            ("创建课程", "teacher"),
            ("上传 SCORM 资源", "teacher"),
            ("添加小节", "teacher"),
            ("报名学习", "student"),
            ("完成考试", "student"),
            ("查看学习进度", "student"),
            ("查看企业课程", "admin"),
            ("管理部门结构", "admin"),
            ("账号黑名单", "admin"),
        ],
    )
    def test_known_intents(self, intent, expected):
        assert IntentCapabilityMap.classify(intent) == expected

    def test_teacher_priority_over_admin(self):
        # "创建课程" 命中 teacher，"企业" 命中 admin；teacher 优先级更高
        assert IntentCapabilityMap.classify("创建课程并发布到企业") == "teacher"

    def test_student_priority_over_admin(self):
        assert IntentCapabilityMap.classify("报名学习任务") == "student"

    def test_unknown_intent_returns_none(self):
        assert IntentCapabilityMap.classify("今天天气怎么样") is None


class TestHasCourseIdentifier:
    @pytest.mark.parametrize(
        "intent",
        [
            "报名 aet504",
            "查看 group_id=123 的进度",
            "访问 https://www.umu.cn/course/123",
            "学习 group/456",
        ],
    )
    def test_detects_identifier(self, intent):
        assert IntentCapabilityMap.has_course_identifier(intent) is True

    def test_no_identifier(self):
        assert IntentCapabilityMap.has_course_identifier("创建课程") is False


__all__ = ["TestClassify"]
