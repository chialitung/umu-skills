# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU Skills WorkBuddy 自动化安装模块.

用法：
    python -m umu_sdk.skills.workbuddy.install           # 安装/更新 WorkBuddy 配置
    python -m umu_sdk.skills.workbuddy.install --check   # 仅检查安装状态
    python -m umu_sdk.skills.workbuddy.install --upgrade # 强制升级 PyPI 包
    python -m umu_sdk.skills.workbuddy.install --workbuddy-dir <路径>

功能：
1. 安装/升级 umu-skills PyPI 包（如果尚未安装）
2. 自动探测 WorkBuddy 配置目录
3. 在 WorkBuddy 的 mcp_servers.json 中注册 umu-skills orchestrator
4. 将 WorkBuddy 技能包复制到 WorkBuddy 配置目录
5. 初始化通用加密凭证目录（默认 ~/.umu_skills）
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.resources as resources
import json
import os
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


def _get_credential_dir() -> Path:
    """返回通用加密凭证目录（跨 AI 工具共享）."""
    return Path.home() / ".umu_skills"


def _get_old_credential_dir() -> Path:
    """返回旧版 Claude Code 专用凭证目录（用于兼容提示）."""
    return Path.home() / ".claude" / "skills" / "umu"


def _get_workbuddy_skill_dir(workbuddy_dir: Path) -> Path:
    """返回 WorkBuddy 配置目录下的技能包目标路径."""
    return workbuddy_dir / "skills" / "umu"


@contextlib.contextmanager
def _get_bundled_skill_dir() -> Iterator[Path]:
    """返回包内自带的 WorkBuddy skill 源目录上下文.

    当脚本从 PyPI 安装的包中运行时，从 wheel 内嵌资源中提取；
    开发模式下直接使用源码树中的文件。
    """
    ref = resources.files("umu_sdk.skills.workbuddy.bundled") / "umu"
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


def _detect_workbuddy_config_dir() -> Path | None:
    """自动探测 WorkBuddy 配置目录.

    探测优先级：
    1. 环境变量 WORKBUDDY_CONFIG_DIR
    2. Windows: %APPDATA%/WorkBuddy, %LOCALAPPDATA%/WorkBuddy
    3. macOS: ~/Library/Application Support/WorkBuddy
    4. Linux: ~/.config/WorkBuddy, ~/.local/share/WorkBuddy

    返回第一个已存在的目录；若都不存在则返回 None。
    """
    if env_dir := os.getenv("WORKBUDDY_CONFIG_DIR"):
        path = Path(env_dir).expanduser()
        if path.exists() and path.is_dir():
            return path

    candidates: list[Path] = []

    if sys.platform == "win32":
        appdata = os.getenv("APPDATA")
        localappdata = os.getenv("LOCALAPPDATA")
        if appdata:
            candidates.append(Path(appdata) / "WorkBuddy")
        if localappdata:
            candidates.append(Path(localappdata) / "WorkBuddy")
        # 备用：用户主目录下常见位置
        candidates.append(Path.home() / "AppData" / "Roaming" / "WorkBuddy")
        candidates.append(Path.home() / "AppData" / "Local" / "WorkBuddy")
    elif sys.platform == "darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "WorkBuddy")
    else:
        # Linux 及其他类 Unix
        candidates.append(Path.home() / ".config" / "WorkBuddy")
        candidates.append(Path.home() / ".local" / "share" / "WorkBuddy")

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate

    return None


def _get_mcp_servers_path(workbuddy_dir: Path) -> Path:
    """返回 WorkBuddy mcp_servers.json 文件路径."""
    return workbuddy_dir / "mcp_servers.json"


def _load_mcp_servers(path: Path) -> dict:
    """读取 mcp_servers.json，不存在或损坏则返回空结构."""
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"mcpServers": {}}


def _save_mcp_servers(path: Path, settings: dict) -> None:
    """保存 mcp_servers.json."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"已更新: {path}")


def _configure_mcp_servers(settings: dict) -> dict:
    """在 mcp_servers.json 中添加/更新 umu-skills orchestrator 配置."""
    mcp_servers = settings.setdefault("mcpServers", {})

    python_cmd = sys.executable
    mcp_servers["umu-skills"] = {
        "type": "stdio",
        "command": python_cmd,
        "args": ["-m", "umu_sdk.skills.server"],
        "env": {
            "UMU_BASE_URL": "https://www.umu.cn",
            "MCP_LOG_LEVEL": "INFO",
            "UMU_SKILL_DIR": str(_get_credential_dir()),
        },
    }

    return settings


def _copy_skill(source: Path, target: Path) -> None:
    """复制 skill 文件到目标目录."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _init_credentials() -> None:
    """初始化凭证文件目录，但不写入任何明文信息."""
    creds_dir = _get_credential_dir()
    creds_dir.mkdir(parents=True, exist_ok=True)
    creds_path = creds_dir / "credentials.enc"
    if not creds_path.exists():
        print("凭证目录已准备就绪，首次使用时会引导你录入账号")


def _check_installation(workbuddy_dir: Path | None = None) -> int:
    """检查当前 WorkBuddy 安装状态并报告."""
    print("=== UMU Skills WorkBuddy 安装状态检查 ===\n")

    ok = True

    # 1. 包检查
    try:
        import umu_sdk

        print(f"✓ umu-skills 包已安装 ({umu_sdk.__file__})")
    except ImportError:
        print("✗ umu-skills 包未安装")
        ok = False

    # 2. WorkBuddy 配置目录检查
    detected_dir = workbuddy_dir or _detect_workbuddy_config_dir()
    if detected_dir is None:
        print("✗ 无法自动检测 WorkBuddy 配置目录")
        print("  请通过 --workbuddy-dir 手动指定，或设置 WORKBUDDY_CONFIG_DIR 环境变量")
        ok = False
    else:
        print(f"✓ WorkBuddy 配置目录: {detected_dir}")

        # 3. mcp_servers.json 检查
        mcp_path = _get_mcp_servers_path(detected_dir)
        if mcp_path.exists():
            try:
                settings = json.loads(mcp_path.read_text(encoding="utf-8"))
                servers = settings.get("mcpServers", {})
                if "umu-skills" in servers:
                    server = servers["umu-skills"]
                    print("✓ mcp_servers.json 已配置 umu-skills")
                    cmd = server.get("command", "")
                    args = server.get("args", [])
                    if args == ["-m", "umu_sdk.skills.server"]:
                        print("  ✓ umu-skills 使用 python -m 启动")
                    elif cmd.startswith("umu-skills-orchestrator"):
                        print("  ⚠ umu-skills 仍使用 console script（可能受 PATH 影响）")
                    else:
                        print(f"  ? umu-skills 命令未知: {cmd} {args}")
                else:
                    print("✗ mcp_servers.json 缺少 umu-skills 配置")
                    ok = False
            except Exception as e:
                print(f"✗ 读取 mcp_servers.json 失败: {e}")
                ok = False
        else:
            print(f"✗ mcp_servers.json 不存在: {mcp_path}")
            ok = False

        # 4. WorkBuddy 技能包检查
        skill_dir = _get_workbuddy_skill_dir(detected_dir)
        if skill_dir.exists() and (skill_dir / "skill.yaml").exists():
            print(f"✓ WorkBuddy 技能包已复制: {skill_dir}")
        else:
            print(f"✗ WorkBuddy 技能包缺失: {skill_dir}")
            ok = False

    # 5. 凭证目录检查
    creds_dir = _get_credential_dir()
    creds_path = creds_dir / "credentials.enc"
    old_creds_path = _get_old_credential_dir() / "credentials.enc"
    if creds_path.exists():
        print(f"✓ 已存在加密凭证文件: {creds_path}")
    elif old_creds_path.exists():
        print(f"○ 发现旧路径加密凭证文件: {old_creds_path}")
        print("  首次保存账号时会自动迁移到新版路径")
    else:
        print(f"○ 尚未保存加密凭证，首次使用 UMU 操作时会引导录入（{creds_dir}）")

    print()
    if ok:
        print("状态正常，重启 WorkBuddy 后即可使用")
        return 0
    print("状态异常，请运行: python -m umu_sdk.skills.workbuddy.install")
    return 1


def install(
    upgrade: bool = False,
    workbuddy_dir: Path | None = None,
) -> None:
    """执行完整 WorkBuddy 安装流程."""
    print("=== UMU Skills WorkBuddy 安装程序 ===\n")

    _ensure_package_installed(upgrade=upgrade)

    # 确定 WorkBuddy 配置目录
    target_dir = workbuddy_dir or _detect_workbuddy_config_dir()
    if target_dir is None:
        print("无法自动检测 WorkBuddy 配置目录。")
        print("请通过 --workbuddy-dir 参数手动指定，例如：")
        print(
            '  python -m umu_sdk.skills.workbuddy.install '
            '--workbuddy-dir "C:\\Users\\xxx\\AppData\\Roaming\\WorkBuddy"'
        )
        print()
        print("常见路径：")
        print("  Windows: %APPDATA%/WorkBuddy 或 %LOCALAPPDATA%/WorkBuddy")
        print("  macOS:   ~/Library/Application Support/WorkBuddy")
        print("  Linux:   ~/.config/WorkBuddy 或 ~/.local/share/WorkBuddy")
        raise SystemExit(1)

    print(f"WorkBuddy 配置目录: {target_dir}\n")

    # 1. 配置 mcp_servers.json
    mcp_path = _get_mcp_servers_path(target_dir)
    settings = _load_mcp_servers(mcp_path)
    settings = _configure_mcp_servers(settings)
    _save_mcp_servers(mcp_path, settings)

    # 2. 复制 WorkBuddy 技能包
    target_skill_dir = _get_workbuddy_skill_dir(target_dir)
    project_source = Path(__file__).resolve().parent / "bundled" / "umu"
    if project_source.exists():
        print(f"使用项目 skill 源: {project_source}\n")
        _copy_skill(project_source, target_skill_dir)
    else:
        print("使用包内自带的 skill 文件\n")
        with _get_bundled_skill_dir() as source:
            _copy_skill(source, target_skill_dir)
    print(f"已复制 WorkBuddy 技能包: {target_skill_dir}")

    # 3. 初始化通用加密凭证目录
    _init_credentials()

    print("\n=== 安装完成 ===")
    print(f"WorkBuddy 配置目录: {target_dir}")
    print(f"MCP 配置: {mcp_path}")
    print(f"技能包: {target_skill_dir}")
    print(f"加密凭证: {_get_credential_dir() / 'credentials.enc'}")
    print("\n下一步：")
    print("1. 在 WorkBuddy 中导入技能包（通常通过 技能市场 → 本地导入 或 设置 → Skills）")
    print("2. 重启 WorkBuddy")
    print("3. 直接用自然语言描述 UMU 操作需求")


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 UMU Skills 到 WorkBuddy")
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
    parser.add_argument(
        "--workbuddy-dir",
        type=Path,
        default=None,
        help="手动指定 WorkBuddy 配置目录",
    )

    args = parser.parse_args()

    try:
        if args.check:
            return _check_installation(workbuddy_dir=args.workbuddy_dir)
        install(upgrade=args.upgrade, workbuddy_dir=args.workbuddy_dir)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"安装失败: {e}")
        return 1
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:
        print(f"安装失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
