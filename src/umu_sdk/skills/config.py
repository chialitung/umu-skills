# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Skills 编排层配置管理.

支持从默认值、JSON 配置文件和环境变量加载子 MCP 服务器配置。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .models import ServerConfig, SkillsConfig

DEFAULT_SERVERS: list[ServerConfig] = [
    ServerConfig(name="teacher", command="umu-skills-teacher"),
    ServerConfig(name="student", command="umu-skills-student"),
    ServerConfig(name="admin", command="umu-skills-admin"),
]


def _env_override_bool(value: str | None) -> bool | None:
    """将环境变量字符串解析为布尔值."""
    if value is None:
        return None
    return value.lower() in {"1", "true", "yes", "on"}


def _apply_env_overrides(config: SkillsConfig) -> SkillsConfig:
    """应用环境变量覆盖到配置.

    支持的环境变量：
    - UMU_SKILLS_SERVERS：逗号分隔的启用服务器名称，如 "teacher,student"
    - UMU_SKILLS_TIMEOUT：调用子 MCP 工具的超时秒数
    """
    servers_env = os.getenv("UMU_SKILLS_SERVERS")
    if servers_env:
        enabled = {name.strip() for name in servers_env.split(",") if name.strip()}
        for server in config.servers:
            server.enabled = server.name in enabled

    timeout_env = os.getenv("UMU_SKILLS_TIMEOUT")
    if timeout_env:
        try:
            config.read_timeout_seconds = float(timeout_env)
        except ValueError:
            pass

    return config


def load_config_from_dict(data: dict[str, Any]) -> SkillsConfig:
    """从字典加载配置."""
    return SkillsConfig.model_validate(data)


def load_config_from_file(path: str | Path) -> SkillsConfig:
    """从 JSON 文件加载配置."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return load_config_from_dict(data)


def get_config(
    path: str | Path | None = None,
    use_env_overrides: bool = True,
) -> SkillsConfig:
    """获取 Skills 编排层配置.

    加载优先级：
    1. 显式传入的 path 参数
    2. 环境变量 UMU_SKILLS_CONFIG 指向的 JSON 文件
    3. 默认配置（包含 teacher/student/admin 三个子 MCP）

    Args:
        path: 配置文件路径，为 None 则尝试环境变量或默认值。
        use_env_overrides: 是否应用环境变量覆盖。

    Returns:
        解析后的 SkillsConfig。
    """
    config_path = path or os.getenv("UMU_SKILLS_CONFIG")

    if config_path:
        config = load_config_from_file(config_path)
    else:
        config = SkillsConfig(servers=list(DEFAULT_SERVERS))

    if use_env_overrides:
        config = _apply_env_overrides(config)

    return config


__all__ = [
    "DEFAULT_SERVERS",
    "get_config",
    "load_config_from_dict",
    "load_config_from_file",
]
