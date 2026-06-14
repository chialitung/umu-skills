"""发布就绪检查脚本.

在最小发布流程中运行，自动核对 README.md 与项目实际代码保持一致，
任何一项失败都会以非零退出码报告：

1. pyproject.toml 的 version 必须与 CHANGELOG.md 最新小节版本一致。
2. README.md 中列出的管理员工具、教师工具、学生工具名称集合必须与代码中
   @mcp.tool() 实际定义的工具名称集合一致，且数量标题一致。
3. README.md 中列出的内置 Skill 名称集合必须与代码中 @skill() 实际定义的
   Skill 名称集合一致，且数量标题一致。

该脚本是阻塞项：未通过前不得执行 release commit 和推送。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent.parent


def fail(message: str) -> None:
    """打印错误并退出."""
    print(f"[FAIL] {message}", file=sys.stderr)
    sys.exit(1)


def info(message: str) -> None:
    """打印信息."""
    print(f"[INFO] {message}")


def get_pyproject_version() -> str:
    """读取 pyproject.toml 版本."""
    path = ROOT / "pyproject.toml"
    if not path.exists():
        fail("pyproject.toml 不存在")
    text = path.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        fail("pyproject.toml 中未找到 version 字段")
    assert match is not None
    return match.group(1)


def get_changelog_latest_version() -> str:
    """读取 CHANGELOG.md 第一个带版本的章节."""
    path = ROOT / "CHANGELOG.md"
    if not path.exists():
        fail("CHANGELOG.md 不存在")
    text = path.read_text(encoding="utf-8")
    match = re.search(r"^## \[([0-9]+\.[0-9]+\.[0-9]+)\]", text, re.MULTILINE)
    if not match:
        fail("CHANGELOG.md 中未找到版本小节（格式：## [x.y.z]）")
    assert match is not None
    return match.group(1)


def extract_mcp_tool_names(file_path: Path) -> set[str]:
    """从 MCP server 文件中提取 @mcp.tool() 装饰的工具名集合."""
    if not file_path.exists():
        fail(f"{file_path} 不存在")
    text = file_path.read_text(encoding="utf-8")
    # 匹配 @mcp.tool() 后（允许中间有其他装饰器）的 async def name(
    pattern = r"@mcp\.tool\([^)]*\)\s*(?:@[^\n]+\s*)*async def\s+(\w+)\s*\("
    return set(re.findall(pattern, text))


def extract_skill_names() -> set[str]:
    """从 builtin skills 目录中提取 @skill(name=...) 声明的 Skill 名集合."""
    skill_dir = ROOT / "src" / "umu_sdk" / "skills" / "builtin"
    if not skill_dir.exists():
        fail("skills/builtin 目录不存在")
    names: set[str] = set()
    for py_file in skill_dir.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        for match in re.finditer(
            r"@skill\(\s*name\s*=\s*\"([^\"]+)\"",
            text,
        ):
            names.add(match.group(1))
    return names


def extract_readme_section(text: str, section_start_pattern: str) -> str:
    """从 README.md 中提取指定章节的内容，直到下一个同层级标题."""
    match = re.search(section_start_pattern, text)
    if not match:
        fail(f"README.md 中未找到匹配 '{section_start_pattern}' 的章节")
    assert match is not None
    start = match.end()
    # 找到下一个 ### 标题或文件结尾
    next_match = re.search(r"\n### ", text[start:])
    end = start + next_match.start() if next_match else len(text)
    return text[start:end]


def extract_readme_tool_counts() -> dict[str, tuple[int, set[str]]]:
    """从 README.md 提取各角色工具数量标题和工具名集合.

    返回 {role: (count, names)}。
    """
    path = ROOT / "README.md"
    if not path.exists():
        fail("README.md 不存在")
    text = path.read_text(encoding="utf-8")

    result: dict[str, tuple[int, set[str]]] = {}

    role_patterns = {
        "admin": (r"### 管理员工具[（(](\d+)[）)]", r"adm_[a-z_]+"),
        "teacher": (r"### 教师工具[（(](\d+)[）)]", r"tch_[a-z_]+"),
        "student": (r"### 学生工具[（(](\d+)[）)]", r"stu_[a-z_]+"),
    }

    for role, (heading_pattern, name_pattern) in role_patterns.items():
        heading_match = re.search(heading_pattern, text)
        if not heading_match:
            fail(f"README.md 中未找到 {role} 工具数量标题")
        assert heading_match is not None
        count = int(heading_match.group(1))

        section = extract_readme_section(text, heading_pattern)
        names = set(re.findall(r"`(" + name_pattern + r")`", section))
        result[role] = (count, names)

    return result


def extract_readme_skill_count_and_names() -> tuple[int, set[str]]:
    """从 README.md 提取内置 Skill 数量标题和 Skill 名集合."""
    path = ROOT / "README.md"
    if not path.exists():
        fail("README.md 不存在")
    text = path.read_text(encoding="utf-8")

    heading_match = re.search(
        r"内置 Skill 覆盖高频场景[（(]共\s*(\d+)\s*[）)]",
        text,
    )
    if not heading_match:
        fail("README.md 中未找到 Skill 总数标题")
    assert heading_match is not None
    count = int(heading_match.group(1))

    section = extract_readme_section(text, re.escape(heading_match.group(0)))
    # 表格行第一列通常是 `skill_name`
    names = set(re.findall(r"\|\s*`([a-z_][a-z0-9_]*)`\s*\|", section))
    return count, names


def main() -> int:
    """执行所有检查."""
    info("开始发布就绪检查...")

    # 1. 版本一致性
    pyproject_version = get_pyproject_version()
    changelog_version = get_changelog_latest_version()
    if pyproject_version != changelog_version:
        fail(
            f"版本不一致：pyproject.toml 为 {pyproject_version}，"
            f"CHANGELOG.md 最新为 {changelog_version}"
        )
    info(f"版本一致：{pyproject_version}")

    # 2. 工具名称集合与数量一致性
    actual_tool_names = {
        "admin": extract_mcp_tool_names(
            ROOT / "src" / "umu_sdk" / "adapters" / "mcp" / "admin.py"
        ),
        "teacher": extract_mcp_tool_names(
            ROOT / "src" / "umu_sdk" / "adapters" / "mcp" / "teacher.py"
        ),
        "student": extract_mcp_tool_names(
            ROOT / "src" / "umu_sdk" / "adapters" / "mcp" / "student.py"
        ),
    }
    readme_tool_info = extract_readme_tool_counts()

    for role in ("admin", "teacher", "student"):
        actual_names = actual_tool_names[role]
        declared_count, declared_names = readme_tool_info[role]

        if declared_count != len(actual_names):
            fail(
                f"{role} 工具数量标题不一致：README.md 声明 {declared_count} 个，"
                f"实际代码 {len(actual_names)} 个。"
            )

        missing = actual_names - declared_names
        extra = declared_names - actual_names
        if missing or extra:
            messages = []
            if missing:
                messages.append(f"README 遗漏：{sorted(missing)}")
            if extra:
                messages.append(f"README 多列/已删除：{sorted(extra)}")
            fail(f"{role} 工具列表与代码不一致：{'；'.join(messages)}")

        info(f"{role} 工具列表一致：{len(actual_names)} 个")

    # 3. Skill 名称集合与数量一致性
    actual_skill_names = extract_skill_names()
    declared_skill_count, declared_skill_names = extract_readme_skill_count_and_names()

    if declared_skill_count != len(actual_skill_names):
        fail(
            f"Skill 数量标题不一致：README.md 声明 {declared_skill_count} 个，"
            f"实际代码 {len(actual_skill_names)} 个。"
        )

    missing_skills = actual_skill_names - declared_skill_names
    extra_skills = declared_skill_names - actual_skill_names
    if missing_skills or extra_skills:
        messages = []
        if missing_skills:
            messages.append(f"README 遗漏：{sorted(missing_skills)}")
        if extra_skills:
            messages.append(f"README 多列/已删除：{sorted(extra_skills)}")
        fail(f"Skill 列表与代码不一致：{'；'.join(messages)}")

    info(f"Skill 列表一致：{len(actual_skill_names)} 个")

    print("\n[PASS] 所有发布就绪检查通过，可以继续执行 release commit。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
