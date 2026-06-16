# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

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


def _get_claude_config_dir() -> Path:
    """返回 Claude Code 配置目录."""
    return Path.home() / ".claude"


def _get_global_skill_dir() -> Path:
    """返回全局 skill 安装目录."""
    return _get_claude_config_dir() / "skills" / "umu"


def _get_project_skill_dir() -> Path:
    """返回项目中的 skill 源目录（开发模式优先使用）."""
    return Path(__file__).resolve().parents[3] / ".claude" / "skills" / "umu"


# SKILL.md 模板中两套 description / 触发条件片段
_DESCRIPTION_EXPLICIT = """\
当用户输入 /umu 斜杠命令时触发。
自动识别意图属于 Teacher（讲师）、Student（学员）还是 Admin（管理员）角色，
并调用对应的 umu-teacher、umu-student、umu-admin MCP server 工具完成任务。
覆盖课程创建、资源管理、小节编辑、学员报名、学习进度查询、考试/问卷/签到、
账号管理、组织架构查询等全部 UMU 平台操作场景。
"""

_DESCRIPTION_SEMANTIC = """\
当用户输入 /umu 斜杠命令，或明确表达需要在 UMU 在线学习平台上完成具体操作时触发。
本 skill 用于操作 UMU 平台：课程创建、资源管理、小节编辑、学员报名、学习进度查询、考试/问卷/签到、账号管理、组织架构查询等。
只有在用户请求执行 UMU 平台能够完成的具体操作时，才调用本 skill。
不要仅因为用户提到通用教育词汇（如“课程”“学员”“考试”“签到”“部门”）就触发。
<!-- ALIASES_PLACEHOLDER -->
"""

_TRIGGER_EXPLICIT = """\
以下情况必须调用本 skill：

1. 用户输入 `/umu`。

2. 用户明确请求与 UMU 平台相关的操作且包含 `/umu` 命令。
"""

_TRIGGER_SEMANTIC = """\
以下情况必须调用本 skill：

1. 用户输入 `/umu`。
2. 用户明确请求在 UMU 平台上完成具体操作，例如：
   - "帮我在 UMU 上创建一个课程"
   - "把 SCORM 课件上传到 UMU"
   - "查询 UMU 上某学员的学习进度"
   - "在 UMU 里批量创建学员账号"
   - "帮我报名 UMU 课程 aet504"
   - "导出 UMU 平台的学习记录"
   - "禁用 UMU 上的某个账号"
3. 用户提到 `UMU` 且上下文表明需要操作 UMU 平台。
<!-- ALIASES_TRIGGER_PLACEHOLDER -->

以下情况不要调用本 skill：
- 用户只是讨论通用教育概念，如 "学员是什么意思"、"考试怎么准备"。
- 用户提到 "课程"、"学习"、"签到" 等词汇但没有 UMU 平台上下文。
- 用户请求设计课程大纲、制定学习计划等 UMU 平台无法直接完成的操作。
"""

MAX_ALIAS_LENGTH = 50
MAX_ALIASES = 10
_ALIAS_PATTERN = re.compile(r"^[\w\s一-鿿\-_.]+$")


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


def add_alias(skill_dir: Path, alias: str) -> tuple[bool, str]:
    """添加一个平台别名."""
    alias = alias.strip()
    if not alias:
        return False, "别名不能为空"
    if len(alias) > MAX_ALIAS_LENGTH:
        return False, f"别名长度不能超过 {MAX_ALIAS_LENGTH} 个字符"
    if not _ALIAS_PATTERN.match(alias):
        return False, "别名只能包含中文、英文、数字、空格、连字符、下划线和点号"

    existing = _load_aliases(skill_dir)
    if len(existing) >= MAX_ALIASES:
        return False, f"别名数量已达上限（最多 {MAX_ALIASES} 个）"
    if alias in existing:
        return False, f"别名 '{alias}' 已存在"

    existing.append(alias)
    _save_aliases(skill_dir, existing)
    return True, f"别名 '{alias}' 已添加"


def remove_alias(skill_dir: Path, alias: str) -> tuple[bool, str]:
    """删除一个平台别名."""
    alias = alias.strip()
    existing = _load_aliases(skill_dir)
    if alias not in existing:
        return False, f"别名 '{alias}' 不存在"
    existing.remove(alias)
    _save_aliases(skill_dir, existing)
    return True, f"别名 '{alias}' 已删除"


def list_aliases(skill_dir: Path) -> list[str]:
    """返回当前所有别名."""
    return _load_aliases(skill_dir)


def _render_skill_md(
    skill_dir: Path,
    semantic_trigger_enabled: bool,
    aliases: list[str] | None = None,
) -> None:
    """根据 semantic_trigger 开关和别名列表渲染 SKILL.md 文件."""
    if aliases is None:
        aliases = _load_aliases(skill_dir)

    skill_md_path = skill_dir / "SKILL.md"
    content = skill_md_path.read_text(encoding="utf-8")

    description = _DESCRIPTION_SEMANTIC if semantic_trigger_enabled else _DESCRIPTION_EXPLICIT
    trigger = _TRIGGER_SEMANTIC if semantic_trigger_enabled else _TRIGGER_EXPLICIT

    if semantic_trigger_enabled and aliases:
        alias_desc_text = (
            f"此外，用户也可以使用以下别名指代 UMU 平台：{', '.join(aliases)}。"
        )
        alias_trigger_text = (
            "4. 用户使用以下别名指代 UMU 平台且上下文表明需要操作平台："
            + "、".join(f"`{a}`" for a in aliases)
            + "。"
        )
    else:
        alias_desc_text = ""
        alias_trigger_text = ""

    description = description.replace(
        "<!-- ALIASES_PLACEHOLDER -->\n",
        alias_desc_text + "\n" if alias_desc_text else "",
    )
    trigger = trigger.replace(
        "<!-- ALIASES_TRIGGER_PLACEHOLDER -->\n",
        alias_trigger_text + "\n" if alias_trigger_text else "",
    )

    # 替换 frontmatter 中的 description 块（从 description: | 到下一个 --- 之间）
    content = re.sub(
        r"description:\s*\|.*?<!-- END_DESCRIPTION -->",
        "description: |\n  <!-- BEGIN_DESCRIPTION -->\n  "
        + description.replace("\n", "\n  ")
        + "\n  <!-- END_DESCRIPTION -->",
        content,
        count=1,
        flags=re.DOTALL,
    )

    # 替换正文中的触发条件章节
    content = re.sub(
        r"<!-- BEGIN_TRIGGER -->.*?<!-- END_TRIGGER -->",
        f"<!-- BEGIN_TRIGGER -->\n{trigger}\n<!-- END_TRIGGER -->",
        content,
        count=1,
        flags=re.DOTALL,
    )

    skill_md_path.write_text(content, encoding="utf-8")


def _perform_install(source: Path, semantic_trigger: bool | None = None) -> None:
    """执行 skill 复制、settings 更新、凭证目录初始化和配置管理."""
    target = _get_global_skill_dir()

    # 1. 读取已有配置（如果存在），用于保留用户选择
    existing_config = _load_skill_config(target) if target.exists() else {}

    # 2. 确定最终开关值：CLI 参数 > 已有配置 > 默认 False
    final_semantic_trigger = semantic_trigger
    if final_semantic_trigger is None:
        final_semantic_trigger = existing_config.get("semantic_trigger_enabled", False)

    # 3. 保留已有别名列表（非列表视为空）
    existing_aliases = existing_config.get("aliases", [])
    if not isinstance(existing_aliases, list):
        existing_aliases = []

    # 4. 合并并保存 config.json（保留未知字段以向前兼容）
    config_to_save: dict = {
        "semantic_trigger_enabled": final_semantic_trigger,
        "aliases": existing_aliases,
    }
    for key, value in existing_config.items():
        if key not in config_to_save:
            config_to_save[key] = value

    # 5. 复制源文件（会清空 target）
    _copy_skill(source, target)

    # 6. 写入 config.json 并渲染 SKILL.md
    _save_skill_config(target, config_to_save)
    _render_skill_md(
        target,
        semantic_trigger_enabled=final_semantic_trigger,
        aliases=existing_aliases,
    )

    # 6. 更新 settings.json 和凭证目录
    settings = _load_settings()
    settings = _configure_mcp_servers(settings)
    _save_settings(settings)

    _init_credentials(target)


def install(upgrade: bool = False, semantic_trigger: bool | None = None) -> None:
    """执行完整安装流程."""
    print("=== UMU Skills 安装程序 ===\n")

    _ensure_package_installed(upgrade=upgrade)

    project_source = _get_project_skill_dir()
    if project_source.exists():
        print(f"使用项目 skill 源: {project_source}\n")
        _perform_install(project_source, semantic_trigger=semantic_trigger)
    else:
        print("使用包内自带的 skill 文件\n")
        with _get_bundled_skill_dir() as source:
            _perform_install(source, semantic_trigger=semantic_trigger)

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

    # 5. 语义触发开关检查
    skill_config = _load_skill_config(skill_dir)
    semantic_enabled = skill_config.get("semantic_trigger_enabled", False)
    status = "已开启" if semantic_enabled else "已关闭"
    print(f"○ 语义自动触发: {status}")

    # 6. 别名检查
    aliases = list_aliases(skill_dir)
    if aliases:
        print(f"○ 已配置别名: {', '.join(aliases)}")
    else:
        print("○ 暂无别名配置")

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
    parser.add_argument(
        "--semantic-trigger",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="安装时是否启用语义自动触发（默认保留已有配置，首次安装为关闭）",
    )

    subparsers = parser.add_subparsers(dest="command", help="附加命令")
    alias_parser = subparsers.add_parser("alias", help="管理 UMU 平台别名")
    alias_sub = alias_parser.add_subparsers(dest="alias_action", required=True)

    add_parser = alias_sub.add_parser("add", help="添加别名")
    add_parser.add_argument("name", help="别名")

    remove_parser = alias_sub.add_parser("remove", help="删除别名")
    remove_parser.add_argument("name", help="别名")

    alias_sub.add_parser("list", help="列出别名")

    args = parser.parse_args()

    try:
        if args.command == "alias":
            skill_dir = _get_global_skill_dir()
            if args.alias_action == "add":
                success, msg = add_alias(skill_dir, args.name)
            elif args.alias_action == "remove":
                success, msg = remove_alias(skill_dir, args.name)
            else:  # list
                aliases = list_aliases(skill_dir)
                print("当前别名：" + ("、".join(aliases) if aliases else "无"))
                return 0

            print(msg)
            if success and args.alias_action in ("add", "remove"):
                config = _load_skill_config(skill_dir)
                semantic_enabled = config.get("semantic_trigger_enabled", False)
                _render_skill_md(skill_dir, semantic_trigger_enabled=semantic_enabled)
            return 0 if success else 1

        if args.check:
            return _check_installation()
        install(upgrade=args.upgrade, semantic_trigger=args.semantic_trigger)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"安装失败: {e}")
        return 1
    except Exception as e:
        print(f"安装失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
