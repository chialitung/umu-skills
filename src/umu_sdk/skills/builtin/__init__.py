# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""内置示例 Skill.

本包包含若干高阶 Skill 示例，用于演示如何基于 @skill 装饰器编排
Teacher / Student / Admin 子 MCP 的原子工具。
"""

from .admin_instructors import get_instructors
from .admin_tasks import get_user_tasks
from .admin_teaching_records import get_teaching_records

__all__ = ["get_instructors", "get_user_tasks", "get_teaching_records"]
