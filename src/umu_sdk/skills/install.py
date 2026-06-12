"""UMU Skills 自动化安装模块.

用法：
    python -m umu_sdk.skills.install           # 安装/更新 Skill 与 MCP 配置
    python -m umu_sdk.skills.install --check   # 仅检查安装状态
    python -m umu_sdk.skills.install --upgrade # 强制升级 PyPI 包

功能：
1. 安装/升级 umu-skills PyPI 包（如果尚未安装）
2. 把 skill 文件复制到用户的 Claude Code 全局 skills 目录
3. 创建/更新 .claude/settings.json 中的 MCP server 配置
4. 初始化加密的凭证文件目录

Windows 全局目录：C:\\Users\\<用户名>\\.claude\\skills\\umu
macOS/Linux 全局目录：~/.claude/skills/umu
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.resources as resources
import json
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


def _get_claude_config_dir() -> Path:
    """返回 Claude Code 配置目录."""
    return Path.home() / ".claude"


def _get_global_skill_dir() -> Path:
    """返回全局 skill 安装目录."""
    return _get_claude_config_dir() / "skills" / "umu"


def _get_project_skill_dir() -> Path:
    """返回项目中的 skill 源目录（开发模式优先使用）."""
    return Path(__file__).resolve().parents[3] / ".claude" / "skills" / "umu"


@contextlib.contextmanager
def _get_bundled_skill_dir() -> Iterator[Path]:
    """返回包内自带的 skill 源目录上下文.

    当脚本从 PyPI 安装的包中运行时，项目目录下的 `.claude/skills/umu`
    不存在，此时从 wheel 内嵌的 bundled 资源中提取。
    """
    ref = resources.files("umu_sdk.skills.bundled") / "umu"
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

    # 使用当前 Python 解释器 + `python -m` 启动 MCP server，
    # 避免依赖 console scripts 所在目录在 PATH 中
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


def _init_credentials(skill_dir: Path) -> None:
    """初始化凭证文件目录，但不写入任何明文信息."""
    skill_dir.mkdir(parents=True, exist_ok=True)
    creds_path = skill_dir / "credentials.enc"
    if not creds_path.exists():
        print("凭证目录已准备就绪，首次使用 /umu 时会引导你录入账号")


def _perform_install(source: Path) -> None:
    """执行 skill 复制、settings 更新和凭证目录初始化."""
    target = _get_global_skill_dir()
    _copy_skill(source, target)

    settings = _load_settings()
    settings = _configure_mcp_servers(settings)
    _save_settings(settings)

    _init_credentials(target)


def install(upgrade: bool = False) -> None:
    """执行完整安装流程."""
    print("=== UMU Skills 安装程序 ===\n")

    _ensure_package_installed(upgrade=upgrade)

    project_source = _get_project_skill_dir()
    if project_source.exists():
        print(f"使用项目 skill 源: {project_source}\n")
        _perform_install(project_source)
    else:
        print("使用包内自带的 skill 文件\n")
        with _get_bundled_skill_dir() as source:
            _perform_install(source)

    print("\n=== 安装完成 ===")
    print(f"Skill 目录: {_get_global_skill_dir()}")
    print(f"配置文件: {_get_settings_path()}")
    print("\n下一步：重启 Claude Code，然后输入 /umu 触发 skill")


def _check_installation() -> int:
    """检查当前安装状态并报告."""
    print("=== UMU Skills 安装状态检查 ===\n")

    ok = True

    # 1. 包检查
    try:
        import umu_sdk

        print(f"✓ umu-skills 包已安装 ({umu_sdk.__file__})")
    except ImportError:
        print("✗ umu-skills 包未安装")
        ok = False

    # 2. Skill 目录检查
    skill_dir = _get_global_skill_dir()
    if skill_dir.exists() and (skill_dir / "SKILL.md").exists():
        print(f"✓ Skill 目录存在: {skill_dir}")
    else:
        print(f"✗ Skill 目录缺失: {skill_dir}")
        ok = False

    # 3. settings.json 检查
    settings_path = _get_settings_path()
    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            servers = settings.get("mcpServers", {})
            required = {"umu-teacher", "umu-student", "umu-admin"}
            missing = required - set(servers.keys())
            if missing:
                print(f"✗ settings.json 缺少 MCP server: {missing}")
                ok = False
            else:
                print("✓ settings.json 已配置三个 MCP server")
                for name in required:
                    server = servers[name]
                    cmd = server.get("command", "")
                    args = server.get("args", [])
                    if args == ["-m", f"umu_sdk.adapters.mcp.{name.split('-')[1]}"]:
                        print(f"  ✓ {name} 使用 python -m 启动")
                    elif cmd.startswith("umu-skills-"):
                        print(f"  ⚠ {name} 仍使用 console script（可能受 PATH 影响）")
                    else:
                        print(f"  ? {name} 命令未知: {cmd} {args}")
        except Exception as e:
            print(f"✗ 读取 settings.json 失败: {e}")
            ok = False
    else:
        print(f"✗ settings.json 不存在: {settings_path}")
        ok = False

    # 4. 凭证目录检查
    creds_path = skill_dir / "credentials.enc"
    if creds_path.exists():
        print(f"✓ 已存在加密凭证文件: {creds_path}")
    else:
        print("○ 尚未保存加密凭证，首次 /umu 会引导录入")

    print()
    if ok:
        print("状态正常，重启 Claude Code 后即可使用 /umu")
        return 0
    print("状态异常，请运行: python -m umu_sdk.skills.install")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 UMU Skills 到 Claude Code")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="强制升级 umu-skills 包",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查安装状态，不执行安装",
    )
    args = parser.parse_args()

    try:
        if args.check:
            return _check_installation()
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
