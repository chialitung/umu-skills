# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""加密凭证加载器.

供 MCP server 启动时调用，支持多来源按优先级加载：
1. 显式传入参数 / 环境变量
2. 项目根目录 `.env` 中的明文凭证（开发阶段便利）
3. skill 目录中 Fernet 加密的 `credentials.enc`（发布阶段）
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Literal

from ..skills.credential_manager import get_role_credentials as _get_encrypted_credentials
from .env_loader import load_env_credentials as _load_env_credentials


class CredentialSource(str, Enum):
    """凭证来源."""

    EXPLICIT = "explicit"  # 显式传入参数或环境变量
    DOTENV = "dotenv"  # .env 文件
    ENCRYPTED = "encrypted"  # 加密凭证文件
    NONE = "none"  # 未找到


def load_credentials_with_source(
    role: Literal["admin", "teacher", "student"],
    env_file: str | Path | None = None,
    explicit_username: str | None = None,
    explicit_password: str | None = None,
) -> tuple[str | None, str | None, CredentialSource]:
    """加载指定角色的账号凭据，并返回来源.

    优先级：
    1. 显式传入参数（explicit_username / explicit_password）
    2. 环境变量 UMU_{ROLE}_USERNAME / UMU_{ROLE}_PASSWORD
    3. .env 文件
    4. skill 目录中的加密凭证文件 credentials.enc

    Args:
        role: 角色名，teacher / student / admin。
        env_file: 可选的 .env 文件路径。
        explicit_username: 显式用户名，优先级最高。
        explicit_password: 显式密码，优先级最高。

    Returns:
        (username, password, source)
    """
    # 1. 显式传入参数
    if explicit_username and explicit_password:
        return explicit_username, explicit_password, CredentialSource.EXPLICIT

    # 2. 环境变量（真正的进程环境变量，不是 .env 注入的）
    env_username = os.getenv(f"UMU_{role.upper()}_USERNAME")
    env_password = os.getenv(f"UMU_{role.upper()}_PASSWORD")
    if env_username and env_password:
        return env_username, env_password, CredentialSource.EXPLICIT

    # 3. .env 文件
    dotenv_username, dotenv_password = _load_env_credentials(role, env_file=env_file)
    if dotenv_username and dotenv_password:
        return dotenv_username, dotenv_password, CredentialSource.DOTENV

    # 4. 加密凭证
    enc_username, enc_password = _get_encrypted_credentials(role)
    if enc_username and enc_password:
        return enc_username, enc_password, CredentialSource.ENCRYPTED

    return None, None, CredentialSource.NONE


def load_credentials(
    role: Literal["admin", "teacher", "student"],
    env_file: str | Path | None = None,
) -> tuple[str | None, str | None]:
    """加载指定角色的账号凭据（向后兼容的便捷包装）.

    优先级与 load_credentials_with_source 一致，但不返回来源。

    Args:
        role: 角色名，teacher / student / admin。
        env_file: 可选的 .env 文件路径。

    Returns:
        (username, password)，如果都未配置则返回 (None, None)。
    """
    username, password, _ = load_credentials_with_source(role, env_file=env_file)
    return username, password


__all__ = ["load_credentials", "load_credentials_with_source", "CredentialSource"]
