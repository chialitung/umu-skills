# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""跨角色共享的业务操作层.

本包存放不依赖具体 MCP server 身份的无状态业务函数，供 teacher/admin/student
三个 server 按需注册为原子 tool。
"""

from __future__ import annotations

from . import collaboration
from . import course_management
from . import learning
from . import programs
from . import resource_management
from . import section_management

__all__ = [
    "collaboration",
    "course_management",
    "learning",
    "programs",
    "resource_management",
    "section_management",
]
