"""发布就绪检查脚本.

在最小发布流程中运行，自动核对以下项目，任何一项失败都会以非零退出码报告：

1. pyproject.toml 的 version 必须与 CHANGELOG.md 最新小节版本一致。
2. README.md 中声明的管理员/教师/学生工具数量必须与代码中实际数量一致。
3. README.md 中声明的内置 Skill 总数必须与代码中实际数量一致。
4. README.md 中功能特性和角色说明里的管理员能力描述必须包含"课程审核"。

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


def count_mcp_tools(file_path: Path) -> int:
    """统计文件中 @mcp.tool() 装饰器数量."""
    if not file_path.exists():
        fail(f"{file_path} 不存在")
    text = file_path.read_text(encoding="utf-8")
    return len(re.findall(r"^\s*@mcp\.tool\([^)]*\)\s*$", text, re.MULTILINE))


def count_skills() -> int:
    """统计 builtin skills 目录中 @skill(...) 装饰器数量."""
    skill_dir = ROOT / "src" / "umu_sdk" / "skills" / "builtin"
    if not skill_dir.exists():
        fail("skills/builtin 目录不存在")
    total = 0
    for py_file in skill_dir.glob("*.py"):
        if py_file.name == "__init__.py":
            continue
        text = py_file.read_text(encoding="utf-8")
        total += len(re.findall(r"^\s*@skill\(", text, re.MULTILINE))
    return total


def extract_readme_counts() -> dict[str, int]:
    """从 README.md 提取工具和 Skill 数量."""
    path = ROOT / "README.md"
    if not path.exists():
        fail("README.md 不存在")
    text = path.read_text(encoding="utf-8")

    counts: dict[str, int] = {}

    admin_match = re.search(r"### 管理员工具[（(](\d+)[）)]", text)
    if admin_match:
        counts["admin"] = int(admin_match.group(1))
    else:
        fail("README.md 中未找到管理员工具数量（格式：### 管理员工具（N））")

    teacher_match = re.search(r"### 教师工具[（(](\d+)[）)]", text)
    if teacher_match:
        counts["teacher"] = int(teacher_match.group(1))
    else:
        fail("README.md 中未找到教师工具数量（格式：### 教师工具（N））")

    student_match = re.search(r"### 学生工具[（(](\d+)[）)]", text)
    if student_match:
        counts["student"] = int(student_match.group(1))
    else:
        fail("README.md 中未找到学生工具数量（格式：### 学生工具（N））")

    skill_match = re.search(r"内置 Skill 覆盖高频场景[（(]共\s*(\d+)\s*[）)]", text)
    if skill_match:
        counts["skill"] = int(skill_match.group(1))
    else:
        fail("README.md 中未找到 Skill 总数（格式：内置 Skill 覆盖高频场景（共 N）：）")

    for key, match in (
        ("admin", admin_match),
        ("teacher", teacher_match),
        ("student", student_match),
        ("skill", skill_match),
    ):
        assert match is not None

    return counts


def check_admin_capability_mentioned() -> None:
    """检查 README.md 中管理员能力描述是否包含课程审核."""
    path = ROOT / "README.md"
    text = path.read_text(encoding="utf-8")

    # 功能特性段落
    feature_match = re.search(
        r"- \*\*三角色 MCP 服务器\*\*：.*管理员[（(]([^)]+)[）)]",
        text,
        re.DOTALL,
    )
    if feature_match:
        admin_desc = feature_match.group(1)
        if "课程审核" not in admin_desc:
            fail(
                "README.md 功能特性中管理员描述缺少'课程审核'，"
                f"当前为：{admin_desc}"
            )
    else:
        fail("README.md 中未找到功能特性里的管理员描述")

    assert feature_match is not None

    # 角色说明段落
    role_match = re.search(
        r"- \*\*Admin（管理员）\*\*[：:](.+?)(?:\n|$)",
        text,
    )
    if role_match:
        admin_role = role_match.group(1).strip()
        if "课程审核" not in admin_role:
            fail(
                "README.md 角色说明中 Admin 描述缺少'课程审核'，"
                f"当前为：{admin_role}"
            )
    else:
        fail("README.md 中未找到角色说明里的 Admin 描述")

    assert role_match is not None


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

    # 2. 工具数量一致性
    actual_counts = {
        "admin": count_mcp_tools(ROOT / "src" / "umu_sdk" / "adapters" / "mcp" / "admin.py"),
        "teacher": count_mcp_tools(ROOT / "src" / "umu_sdk" / "adapters" / "mcp" / "teacher.py"),
        "student": count_mcp_tools(ROOT / "src" / "umu_sdk" / "adapters" / "mcp" / "student.py"),
    }
    readme_counts = extract_readme_counts()

    for role in ("admin", "teacher", "student"):
        actual = actual_counts[role]
        declared = readme_counts[role]
        if actual != declared:
            fail(
                f"{role} 工具数量不一致：README.md 声明 {declared} 个，"
                f"实际代码 {actual} 个。请更新 README.md。"
            )
        info(f"{role} 工具数量一致：{actual}")

    # 3. Skill 数量一致性
    actual_skill_count = count_skills()
    declared_skill_count = readme_counts["skill"]
    if actual_skill_count != declared_skill_count:
        fail(
            f"Skill 数量不一致：README.md 声明 {declared_skill_count} 个，"
            f"实际代码 {actual_skill_count} 个。请更新 README.md。"
        )
    info(f"Skill 数量一致：{actual_skill_count}")

    # 4. 能力描述检查
    check_admin_capability_mentioned()
    info("README.md 管理员能力描述包含'课程审核'")

    print("\n[PASS] 所有发布就绪检查通过，可以继续执行 release commit。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
