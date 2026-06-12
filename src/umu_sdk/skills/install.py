"""UMU Skills 自动化安装模块.

用法：
    python -m umu_sdk.skills.install

功能：
1. 安装/升级 umu-skills PyPI 包（如果尚未安装）
2. 把项目中的 skill 复制到用户的 Claude Code 全局 skills 目录
3. 创建/更新 .claude/settings.json 中的 MCP server 配置
4. 初始化加密的凭证文件目录

Windows 全局目录：C:\\Users\\<用户名>\\.claude\\skills\\umu
macOS/Linux 全局目录：~/.claude/skills/umu
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _get_claude_config_dir() -> Path:
    """返回 Claude Code 配置目录."""
    return Path.home() / ".claude"


def _get_global_skill_dir() -> Path:
    """返回全局 skill 安装目录."""
    return _get_claude_config_dir() / "skills" / "umu"


def _get_project_skill_dir() -> Path:
    """返回项目中的 skill 源目录."""
    return Path(__file__).resolve().parents[3] / ".claude" / "skills" / "umu"


def _ensure_package_installed(upgrade: bool = False) -> None:
    """确保 umu-skills 包已安装."""
    try:
        import umu_sdk  # noqa: F401

        if not upgrade:
            print("umu-skills 已安装")
            return
    except ImportError:
        pass

    print("正在安装 umu-skills...")
    cmd = [sys.executable, "-m", "pip", "install", "-e", ".[mcp]"]
    project_root = Path(__file__).resolve().parents[3]
    subprocess.run(cmd, cwd=project_root, check=True)
    print("umu-skills 安装完成")


def _copy_skill(source: Path, target: Path) -> None:
    """复制 skill 文件到全局目录."""
    if target.exists():
        print(f"清理旧的 skill 目录: {target}")
        shutil.rmtree(target)

    print(f"复制 skill 到: {target}")
    shutil.copytree(source, target)


def _get_settings_path() -> Path:
    return _get_claude_config_dir() / "settings.json"


def _load_settings() -> dict:
    path = _get_settings_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_settings(settings: dict) -> None:
    path = _get_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"已更新: {path}")


def _configure_mcp_servers(settings: dict) -> dict:
    """在 settings.json 中添加/更新 umu MCP server 配置."""
    mcp_servers = settings.setdefault("mcpServers", {})

    base_env = {
        "UMU_BASE_URL": "${UMU_BASE_URL:-https://www.umu.cn}",
        "MCP_LOG_LEVEL": "${MCP_LOG_LEVEL:-INFO}",
    }

    mcp_servers["umu-teacher"] = {
        "command": "umu-skills-teacher",
        "env": {**base_env},
    }
    mcp_servers["umu-student"] = {
        "command": "umu-skills-student",
        "env": {**base_env},
    }
    mcp_servers["umu-admin"] = {
        "command": "umu-skills-admin",
        "env": {**base_env},
    }

    return settings


def _init_credentials(skill_dir: Path) -> None:
    """初始化凭证文件目录，但不写入任何明文信息."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    creds_path = skill_dir / "credentials.enc"
    if not creds_path.exists():
        # 不创建空文件；credential_manager 会在首次保存时创建
        print("凭证目录已准备就绪，首次使用 /umu 时会引导你录入账号")
    else:
        print("已存在加密凭证文件，保留现有配置")


def install(upgrade: bool = False) -> None:
    """执行完整安装流程."""
    print("=== UMU Skills 安装程序 ===\n")

    _ensure_package_installed(upgrade=upgrade)

    source = _get_project_skill_dir()
    if not source.exists():
        print(f"错误：找不到项目 skill 目录: {source}")
        sys.exit(1)

    target = _get_global_skill_dir()
    _copy_skill(source, target)

    settings = _load_settings()
    settings = _configure_mcp_servers(settings)
    _save_settings(settings)

    _init_credentials(target)

    print("\n=== 安装完成 ===")
    print(f"Skill 目录: {target}")
    print(f"配置文件: {_get_settings_path()}")
    print("\n下一步：")
    print("1. 重启 Claude Code")
    print("2. 输入 /umu 触发 skill，按提示录入账号信息")
    print("3. 账号将加密保存在 skill 目录的 credentials.enc 中")


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 UMU Skills 到 Claude Code")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="强制升级 umu-skills 包",
    )
    args = parser.parse_args()

    try:
        install(upgrade=args.upgrade)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"安装失败: {e}")
        return 1
    except Exception as e:
        print(f"安装失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
