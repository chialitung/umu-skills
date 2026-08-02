"""Pytest 配置."""

import asyncio
import os

import pytest


# Skill 层测试（skill_run）依赖 get_configured_roles() 判断能力域可用角色，
# 该函数读取 .env 文件或 UMU_*_USERNAME/PASSWORD 环境变量。
# 在无本地 .env 的环境（如 CI）下提供占位凭据，保证测试可重复；
# setdefault 不会覆盖开发者已有的真实环境变量配置。
for _role in ("ADMIN", "TEACHER", "STUDENT"):
    os.environ.setdefault(f"UMU_{_role}_USERNAME", f"ci-placeholder-{_role.lower()}@example.com")
    os.environ.setdefault(f"UMU_{_role}_PASSWORD", "ci-placeholder-password")


def pytest_collection_modifyitems(config, items):
    """自动为所有 async 测试函数添加 asyncio marker."""
    for item in items:
        if asyncio.iscoroutinefunction(item.function):
            item.add_marker(pytest.mark.asyncio)
