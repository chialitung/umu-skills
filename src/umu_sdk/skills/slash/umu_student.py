# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""/umus /umustudent 显式 student 角色入口.

固定以 student 角色作为默认执行角色；student 未配置时按能力层级 fallback 到 teacher/admin。
"""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill
from ._runner import run_umu_command


@skill(
    name="umu-student",
    description="使用 student 账号执行 UMU 操作（/umus /umustudent）",
    required_capabilities=[],
    return_description="统一返回信封",
)
async def umu_student(
    ctx: SkillContext,
    command: str,
    remember_choice: bool = False,
) -> dict[str, Any]:
    """执行 /umus 命令.

    Args:
        command: 用户自然语言命令。
        remember_choice: 是否记住本次 student 选择到会话状态。
    """
    return await run_umu_command(
        ctx=ctx,
        command=command,
        default_role="student",
        remember_choice=remember_choice,
    )


__all__ = ["umu_student"]
