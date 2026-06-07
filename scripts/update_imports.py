"""批量更新 import 路径脚本."""

import os
import re
from pathlib import Path

# 项目根目录
ROOT = Path("D:/BaiduSyncdisk/umu-skills/src/umu_sdk")

# 定义替换规则：(模式, 替换)
# 按文件路径匹配
REPLACEMENTS = {
    # 根包 __init__.py — 导出从 core/ 引入
    "umu_sdk/__init__.py": [
        (r"from \.client import UMUClient", "from .core.client import UMUClient"),
        (r"from \.auth import AuthManager", "from .core.auth import AuthManager"),
        (r"from \.encrypt import", "from .core.encrypt import"),
        (r"from \.errors import", "from .core.errors import"),
        (r"from \.models import", "from .core.models import"),
    ],
    # endpoints/courses.py — 引用 core/models
    "umu_sdk/endpoints/courses.py": [
        (r"from \.\.models import", "from ..core.models import"),
        (r"from \.\.errors import", "from ..core.errors import"),
    ],
    # adapters/mcp/*.py — 引用 core 模块（使用绝对 import）
    "umu_sdk/adapters/mcp/batch.py": [
        (r"from \.\.client import UMUClient", "from umu_sdk.core.client import UMUClient"),
    ],
    "umu_sdk/adapters/mcp/session.py": [
        (r"from \.\.client import UMUClient", "from umu_sdk.core.client import UMUClient"),
    ],
    "umu_sdk/adapters/mcp/student.py": [
        (r"from \.\.client import UMUClient", "from umu_sdk.core.client import UMUClient"),
        (r"from \.\.encrypt import encrypt_password", "from umu_sdk.core.encrypt import encrypt_password"),
    ],
    "umu_sdk/adapters/mcp/teacher.py": [
        (r"from \.\.client import UMUClient", "from umu_sdk.core.client import UMUClient"),
    ],
    # 文档字符串中的 import 示例
    "umu_sdk/adapters/mcp/course_builder.py": [
        (r"from umu_sdk\.mcp\.course_builder import CourseBuilder",
         "from umu_sdk.adapters.mcp.course_builder import CourseBuilder"),
    ],
    "umu_sdk/adapters/mcp/image_upload.py": [
        (r"from umu_sdk\.mcp\.image_upload import ImageUploader",
         "from umu_sdk.adapters.mcp.image_upload import ImageUploader"),
    ],
}


def update_file(filepath: Path, rules: list[tuple[str, str]]) -> int:
    """更新单个文件，返回替换次数."""
    content = filepath.read_text(encoding="utf-8")
    original = content
    count = 0

    for pattern, replacement in rules:
        new_content, n = re.subn(pattern, replacement, content)
        if n > 0:
            content = new_content
            count += n
            print(f"  [{filepath.name}] {pattern[:40]}... -> {replacement[:40]}... ({n}次)")

    if content != original:
        filepath.write_text(content, encoding="utf-8")

    return count


def main():
    total = 0
    for rel_path, rules in REPLACEMENTS.items():
        filepath = ROOT / rel_path
        if not filepath.exists():
            print(f"⚠️ 文件不存在: {filepath}")
            continue
        count = update_file(filepath, rules)
        total += count

    print(f"\n✅ 共完成 {total} 处替换")


if __name__ == "__main__":
    main()
