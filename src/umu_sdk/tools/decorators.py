# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""业务操作装饰器.

提供 `@umu_operation()` 装饰器，用于声明可被多个 MCP server 自动注册为 tool 的
无状态业务函数。被装饰函数第一个参数必须是 `UMUClient`，其余参数作为工具入参。
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class OperationInfo:
    """业务操作元数据."""

    name: str
    description: str
    roles: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    parameter_docs: dict[str, str] = field(default_factory=dict)


# 被装饰函数上挂载的属性名
_UMU_OPERATION_ATTR = "_umu_operation"


OperationCallable = Callable[..., Any]


def umu_operation(
    name: str,
    description: str,
    roles: list[str],
    capabilities: list[str] | None = None,
    parameter_docs: dict[str, str] | None = None,
) -> Callable[[OperationCallable], OperationCallable]:
    """声明一个跨角色共享的 UMU 业务操作.

    被装饰的函数第一个参数必须是 `client: UMUClient`，其余参数会暴露为 MCP tool
    的输入参数。

    Args:
        name: 操作名（不含角色前缀），如 "delete_learning_program"。
        description: 操作描述。
        roles: 暴露此操作的角色列表，如 ["teacher", "admin"]。
        capabilities: 操作所属的能力域列表，如 ["program_management"]。
            为空时按模块路径自动推断。
        parameter_docs: 各参数的中文说明，key 为参数名。

    Example:
        @umu_operation(
            name="delete_learning_program",
            description="删除学习项目",
            roles=["teacher", "admin"],
            capabilities=["program_management"],
            parameter_docs={"program_id": "学习项目 ID"},
        )
        async def delete_learning_program(client: UMUClient, program_id: str) -> dict[str, Any]:
            ...
    """

    def decorator(func: OperationCallable) -> OperationCallable:
        info = OperationInfo(
            name=name,
            description=description,
            roles=list(roles),
            capabilities=list(capabilities or []),
            parameter_docs=parameter_docs or {},
        )
        setattr(func, _UMU_OPERATION_ATTR, info)
        return func

    return decorator


def is_umu_operation(obj: Any) -> bool:
    """判断对象是否是被 @umu_operation 装饰的函数."""
    return inspect.isfunction(obj) and hasattr(obj, _UMU_OPERATION_ATTR)


def get_operation_info(obj: Any) -> OperationInfo | None:
    """从对象中提取 OperationInfo."""
    return getattr(obj, _UMU_OPERATION_ATTR, None)


__all__ = [
    "umu_operation",
    "is_umu_operation",
    "get_operation_info",
    "OperationInfo",
    "OperationCallable",
]
