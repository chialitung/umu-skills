# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""/umua /umuadmin 显式 admin 角色入口.

固定以 admin 角色作为默认执行角色；admin 未配置时按能力层级 fallback。
"""

from __future__ import annotations

from typing import Any

from ..decorators import SkillContext, skill
from ._runner import run_umu_command


@skill(
    name="umu-admin",
    description="使用 admin 账号执行 UMU 操作（/umua /umuadmin）",
    required_servers=[],
    return_description="统一返回信封",
)
async def umu_admin(
    ctx: SkillContext,
    command: str,
    remember_choice: bool = False,
) -> dict[str, Any]:
    """执行 /umua 命令.

    Args:
        command: 用户自然语言命令。
        remember_choice: 是否记住本次 admin 选择到会话状态。
    """
    return await run_umu_command(
        ctx=ctx,
        command=command,
        default_role="admin",
        remember_choice=remember_choice,
    )


__all__ = ["umu_admin"]
