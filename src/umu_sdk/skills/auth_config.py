# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Skill 编排层的账号配置工具.

提供从环境变量 / .env 读取已配置角色的统一入口，供 RoleResolver、
CapabilityResolver 和 SkillContext 使用。
"""

from __future__ import annotations

import os

from ..core.env_loader import load_env_credentials


def get_configured_roles() -> list[str]:
    """返回已配置账号凭据的角色列表.

    优先从 `.env` 文件读取，若不存在则回退到当前环境变量。
    返回顺序固定为 admin / teacher / student，便于界面展示。
    """
    configured: list[str] = []
    for role in ("admin", "teacher", "student"):
        username, password = load_env_credentials(role)
        if not username or not password:
            username = os.getenv(f"UMU_{role.upper()}_USERNAME")
            password = os.getenv(f"UMU_{role.upper()}_PASSWORD")
        if username and password:
            configured.append(role)
    return configured


__all__ = ["get_configured_roles"]
