# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""意图到能力角色的映射.

维护关键词/正则到 capability（teacher/student/admin）的轻量规则表。
未来可扩展为 LLM/embedding 分类，但第一阶段用规则表保证可预测性。
"""

from __future__ import annotations

import re


class IntentCapabilityMap:
    """将自然语言意图映射到推荐能力角色."""

    # 关键词到 capability 的映射；同一 capability 内按匹配优先级排列
    KEYWORDS: dict[str, list[str]] = {
        "teacher": [
            "创建课程",
            "新建课程",
            "上传",
            "资源",
            "scorm",
            "添加小节",
            "编辑小节",
            "课程协同",
            "转让课程",
            "提交审核",
            "访问权限",
            "指定账户",
            "定时关闭",
            "自动关闭",
        ],
        "student": [
            "报名",
            "学习",
            "完成",
            "浏览",
            "签到",
            "问卷",
            "考试",
            "进度",
            "我的课程",
        ],
        "admin": [
            "企业课程",
            "审核课程",
            "账号",
            "部门",
            "分组",
            "班级",
            "组织架构",
            "学习记录",
            "授课记录",
            "学习任务",
            "黑名单",
        ],
    }

    @classmethod
    def classify(cls, intent: str) -> str | None:
        """根据意图文本返回推荐 capability（teacher/student/admin）.

        若多个 capability 都有关键词命中，按 teacher > student > admin
        的优先级返回（创建课程、学习等动作优先于后台查询）。
        未命中时返回 None。
        """
        lowered = intent.lower()
        scores: dict[str, int] = {}
        for capability, keywords in cls.KEYWORDS.items():
            score = 0
            for keyword in keywords:
                if keyword.lower() in lowered:
                    score += 1
            if score:
                scores[capability] = score

        if not scores:
            return None

        # 优先级：teacher > student > admin
        for capability in ("teacher", "student", "admin"):
            if capability in scores:
                return capability

        return None

    @classmethod
    def has_course_identifier(cls, intent: str) -> bool:
        """判断意图中是否包含可能的课程标识（访问码、URL、group_id）。"""
        patterns = [
            r"aet\d+",
            r"group[_-]?id[=\s]?\d+",
            r"umu\.cn",
            r"course/\d+",
            r"group/\d+",
        ]
        lowered = intent.lower()
        return any(re.search(p, lowered) for p in patterns)


__all__ = ["IntentCapabilityMap"]
