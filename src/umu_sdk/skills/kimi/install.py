# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU Skills Kimi Code CLI 自动化安装模块.

用法：
    python -m umu_sdk.skills.kimi.install           # 安装/更新 Skill 与 MCP 配置
    python -m umu_sdk.skills.kimi.install --check   # 仅检查安装状态
    python -m umu_sdk.skills.kimi.install --upgrade # 强制升级 PyPI 包

功能：
1. 安装/升级 umu-skills PyPI 包（如果尚未安装）
2. 把 skill 文件复制到用户的 Kimi Code CLI 全局 skills 目录
3. 创建/更新 ~/.kimi-code/mcp.json 中的 MCP server 配置
4. 初始化加密的凭证文件目录（默认 ~/.umu_skills）

Skill 文件目录：
    Windows: C:\\Users\\<用户名>\\.kimi-code\\skills\\umu
    macOS/Linux: ~/.kimi-code/skills/umu

加密凭证目录：
    Windows: C:\\Users\\<用户名>\\.umu_skills
    macOS/Linux: ~/.umu_skills
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.resources as resources
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator

# Windows 中文输出修复（必须在任何打印之前）
if sys.platform == "win32":
    try:
        import io

        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _get_kimi_code_home() -> Path:
    """返回 Kimi Code CLI 主目录."""
    env_home = os.getenv("KIMI_CODE_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".kimi-code"


def _get_global_skills_root() -> Path:
    """返回 Kimi Code CLI 全局 skill 安装根目录."""
    return _get_kimi_code_home() / "skills"


def _get_global_skill_dir(skill_name: str = "umu") -> Path:
    """返回指定 skill 的全局安装目录."""
    return _get_global_skills_root() / skill_name


def _get_credential_dir() -> Path:
    """返回通用加密凭证目录（跨 AI 工具共享）."""
    return Path.home() / ".umu_skills"


def _get_old_credential_dir() -> Path:
    """返回旧版 Claude Code 专用凭证目录（用于兼容提示）."""
    return Path.home() / ".claude" / "skills" / "umu"


def _get_project_skills_root() -> Path:
    """返回项目中的 skill 源根目录（开发模式优先使用）."""
    return Path(__file__).resolve().parents[3] / ".kimi-code" / "skills"


@contextlib.contextmanager
def _get_bundled_skills_root() -> Iterator[Path]:
    """返回包内自带的 skill 源根目录上下文.

    当脚本从 PyPI 安装的包中运行时，项目目录下的 `.kimi-code/skills`
    不存在，此时从 wheel 内嵌的 bundled 资源中提取。
    """
    ref = resources.files("umu_sdk.skills.kimi.bundled")
    with resources.as_file(ref) as path:
        yield path


def _ensure_package_installed(upgrade: bool = False) -> None:
    """确保 umu-skills 包已安装."""
    is_installed = False
    try:
        import umu_sdk  # noqa: F401

        is_installed = True
        if not upgrade:
            print("umu-skills 已安装")
            return
    except ImportError:
        pass

    action = "升级" if is_installed else "安装"
    print(f"正在{action} umu-skills...")
    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    cmd.append("umu-skills[mcp]")
    subprocess.run(cmd, check=True)
    print(f"umu-skills {action}完成")


def _copy_skill(source: Path, target: Path) -> None:
    """复制 skill 文件到全局目录."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _get_mcp_servers_path() -> Path:
    """返回 Kimi Code CLI mcp.json 文件路径."""
    return _get_kimi_code_home() / "mcp.json"


def _load_mcp_servers() -> dict:
    """读取 mcp.json，不存在或损坏则返回空结构."""
    path = _get_mcp_servers_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"mcpServers": {}}


def _save_mcp_servers(settings: dict) -> None:
    """保存 mcp.json."""
    path = _get_mcp_servers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"已更新: {path}")


def _configure_mcp_servers(settings: dict) -> dict:
    """在 mcp.json 中添加/更新 umu MCP server 配置."""
    mcp_servers = settings.setdefault("mcpServers", {})

    # Kimi mcp.json 不支持 ${VAR:-default} 语法，直接写入解析后的值
    base_url = os.getenv("UMU_BASE_URL", "https://www.umu.cn")
    log_level = os.getenv("MCP_LOG_LEVEL", "INFO")
    base_env = {
        "UMU_BASE_URL": base_url,
        "MCP_LOG_LEVEL": log_level,
        "UMU_SKILL_DIR": str(_get_credential_dir()),
    }

    python_cmd = sys.executable
    mcp_servers["umu-teacher"] = {
        "command": python_cmd,
        "args": ["-m", "umu_sdk.adapters.mcp.teacher"],
        "env": {**base_env},
    }
    mcp_servers["umu-student"] = {
        "command": python_cmd,
        "args": ["-m", "umu_sdk.adapters.mcp.student"],
        "env": {**base_env},
    }
    mcp_servers["umu-admin"] = {
        "command": python_cmd,
        "args": ["-m", "umu_sdk.adapters.mcp.admin"],
        "env": {**base_env},
    }

    return settings


def _init_credentials(creds_dir: Path) -> None:
    """初始化凭证文件目录，但不写入任何明文信息."""
    creds_dir.mkdir(parents=True, exist_ok=True)
    creds_path = creds_dir / "credentials.enc"
    if not creds_path.exists():
        print("凭证目录已准备就绪，首次使用 /umu 时会引导你录入账号")


def _get_skill_config_path(skill_dir: Path) -> Path:
    """返回 skill 配置 JSON 文件路径."""
    return skill_dir / "config.json"


def _load_skill_config(skill_dir: Path) -> dict:
    """读取 skill 目录下的 config.json，不存在或损坏则返回空字典."""
    config_path = _get_skill_config_path(skill_dir)
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_skill_config(skill_dir: Path, config: dict) -> None:
    """保存配置到 skill 目录下的 config.json."""
    config_path = _get_skill_config_path(skill_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_aliases(skill_dir: Path) -> list[str]:
    """从 config.json 读取 aliases 列表，损坏或缺失返回空列表."""
    config = _load_skill_config(skill_dir)
    aliases = config.get("aliases", [])
    if not isinstance(aliases, list):
        return []
    return [str(a).strip() for a in aliases if str(a).strip()]


def _save_aliases(skill_dir: Path, aliases: list[str]) -> None:
    """保存别名列表到 config.json，保留其他字段."""
    config = _load_skill_config(skill_dir)
    config["aliases"] = aliases
    _save_skill_config(skill_dir, config)
