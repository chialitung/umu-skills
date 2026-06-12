"""加密凭证加载器.

供 MCP server 启动时调用，优先读取项目根目录 `.env` 中的明文凭证；
其次读取环境变量；最后尝试读取 skill 目录中 Fernet 加密的 `credentials.enc`。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from ..skills.credential_manager import get_role_credentials as _get_encrypted_credentials
from .env_loader import load_env_credentials as _load_env_credentials


def load_credentials(
    role: Literal["admin", "teacher", "student"],
    env_file: str | Path | None = None,
) -> tuple[str | None, str | None]:
    """加载指定角色的账号凭据.

    优先级：
    1. `.env` 文件（开发阶段优先）
    2. 环境变量
    3. skill 目录中的加密凭证文件 `credentials.enc`（发布阶段）

    Args:
        role: 角色名，teacher / student / admin。
        env_file: 可选的 .env 文件路径。

    Returns:
        (username, password)，如果都未配置则返回 (None, None)。
    """
    # 1. 优先从 .env 读取
    username, password = _load_env_credentials(role, env_file=env_file)
    if username and password:
        return username, password

    # 2. 从环境变量读取
    username = os.getenv(f"UMU_{role.upper()}_USERNAME")
    password = os.getenv(f"UMU_{role.upper()}_PASSWORD")
    if username and password:
        return username, password

    # 3. 从加密凭证文件读取
    return _get_encrypted_credentials(role)


__all__ = ["load_credentials"]
