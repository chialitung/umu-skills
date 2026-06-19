# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""/umu 智能路由 Skill.

根据用户意图自动选择执行角色；当存在多个可用角色时，
会请求用户确认或使用 remembered_role 继续上次选择。
"""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill
from ._runner import run_umu_command


@skill(
    name="umu",
    description="智能路由用户意图到最佳角色并执行对应的统一 Skill",
    required_servers=[],
    return_description="统一返回信封，包含 resolved_role、fallback_reason 等字段",
)
async def umu(
    ctx: SkillContext,
    command: str,
    preferred_role: str | None = None,
    remember_choice: bool = False,
) -> dict[str, Any]:
    """执行 /umu 命令.

    Args:
        command: 用户自然语言命令，例如“创建课程 titledemo”。
        preferred_role: 用户在上一次交互中选择的角色（teacher/student/admin）。
        remember_choice: 是否记住本次角色选择到会话状态。
    """
    return await run_umu_command(
        ctx=ctx,
        command=command,
        default_role=preferred_role,
        remember_choice=remember_choice,
    )


__all__ = ["umu"]
