""".env 文件加载工具.

提供不依赖第三方库的标准库实现，用于在 MCP server 启动或登录类 Tool
被调用时重新读取项目根目录 `.env` 文件中的账号凭据。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal


_ENV_PATH_CANDIDATES = (
    Path.cwd() / ".env",
    Path(__file__).resolve().parents[3] / ".env",
)

_VAR_PATTERN = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=[ \t]*(.*)$")


def find_env_file(path: str | Path | None = None) -> Path | None:
    """定位 .env 文件.

    优先使用传入路径，其次尝试当前工作目录和项目根目录。
    """
    if path is not None:
        p = Path(path)
        return p if p.is_file() else None

    for candidate in _ENV_PATH_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def parse_env_file(path: str | Path | None = None) -> dict[str, str]:
    """解析 .env 文件，返回键值对字典.

    行为说明：
    - 忽略空行和注释行（以 # 开头）
    - 不处理变量插值、引号转义等高级特性
    - 键值对按文件顺序解析，后出现的同名键会覆盖先出现的
    """
    env_path = find_env_file(path)
    if env_path is None:
        return {}

    result: dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return result

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _VAR_PATTERN.match(line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip().strip("\"'").strip()
        result[key] = value
    return result


def get_role_credentials(
    role: Literal["admin", "teacher", "student"],
    env_file: str | Path | None = None,
) -> tuple[str | None, str | None]:
    """从 .env 文件读取指定角色的账号凭据.

    Args:
        role: 角色名，对应 .env 中的变量前缀。
        env_file: 可选的 .env 文件路径，默认自动查找。

    Returns:
        (username, password)，如果任一未配置则返回 None。
    """
    env_vars = parse_env_file(env_file)
    username = env_vars.get(f"UMU_{role.upper()}_USERNAME")
    password = env_vars.get(f"UMU_{role.upper()}_PASSWORD")
    return username, password


def load_env_credentials(
    role: Literal["admin", "teacher", "student"],
    env_file: str | Path | None = None,
) -> tuple[str | None, str | None]:
    """重新读取 .env 文件并返回指定角色的凭据.

    此函数每次调用都会重新解析 .env 文件，确保获取到最新的账号信息。
    """
    return get_role_credentials(role, env_file=env_file)
