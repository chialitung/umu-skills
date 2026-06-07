"""Pytest 配置."""

import asyncio
import pytest


def pytest_collection_modifyitems(config, items):
    """自动为所有 async 测试函数添加 asyncio marker."""
    for item in items:
        if asyncio.iscoroutinefunction(item.function):
            item.add_marker(pytest.mark.asyncio)
