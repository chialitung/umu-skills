"""IntentCapabilityMap 单元测试."""

from __future__ import annotations

import pytest

from umu_sdk.skills.intent_capability_map import IntentCapabilityMap


class TestClassify:
    @pytest.mark.parametrize(
        ("intent", "expected"),
        [
            ("创建课程", "course_management"),
            ("上传 SCORM 资源", "course_management"),
            ("添加小节", "course_management"),
            ("报名学习", "learning"),
            ("完成考试", "learning"),
            ("查看学习进度", "learning"),
            ("查看企业课程", "data_query"),
            ("管理部门结构", "organization"),
            ("账号黑名单", "course_audit"),
        ],
    )
    def test_known_intents(self, intent, expected):
        assert IntentCapabilityMap.classify(intent) == expected

    def test_teacher_priority_over_admin(self):
        # "创建课程" 命中 course_management，"企业课程" 命中 data_query；
        # 课程管理优先级更高
        assert IntentCapabilityMap.classify("创建课程并发布到企业") == "course_management"

    def test_student_priority_over_admin(self):
        assert IntentCapabilityMap.classify("报名学习任务") == "learning"

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
