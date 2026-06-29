# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""MCP Tool 工厂.

根据 `src/umu_sdk/tools/operations/` 中被 `@umu_operation()` 标记的业务函数，
自动生成并注册对应角色的 MCP tool。生成的 tool 负责：

- 获取 client 与鉴权
- 注入 `session_id` 通用参数
- 调用无状态业务函数
- 按角色包装错误文案

业务函数本身不感知角色，仅接收 `UMUClient` 和业务参数。
"""

from __future__ import annotations

import inspect
import logging
import typing
from types import ModuleType
from typing import Annotated, Any, Callable

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ...core.client import UMUClient
from ...core.errors import UMUError
from ...tools.decorators import get_operation_info, is_umu_operation


ROLE_PREFIXES: dict[str, str] = {
    "teacher": "tch",
    "admin": "adm",
    "student": "stu",
}

ROLE_LABELS: dict[str, str] = {
    "teacher": "讲师",
    "admin": "管理员",
    "student": "学员",
}

_GetClient = Callable[[str | None], UMUClient]
_RequireAuth = Callable[[UMUClient], str | None]
_Ok = Callable[..., str]
_Err = Callable[..., str]


def register_operations(
    mcp: FastMCP,
    module: ModuleType,
    role: str,
    get_client: _GetClient,
    ok: _Ok,
    err: _Err,
    require_auth: _RequireAuth | None = None,
    logger: logging.Logger | None = None,
    namespace: dict[str, Any] | None = None,
) -> list[str]:
    """扫描模块，将属于指定角色的业务操作注册为 MCP tool.

    Args:
        mcp: FastMCP 服务器实例。
        module: 包含 @umu_operation 函数的模块。
        role: 当前 server 角色，如 "teacher" / "admin" / "student"。
        get_client: 获取 UMUClient 的函数。
        ok: 构造成功响应的函数。
        err: 构造失败响应的函数。
        require_auth: 可选的鉴权检查函数。
        logger: 可选的日志记录器。
        namespace: 可选的命名空间字典；生成 tool 后会以 tool_name 为键写入，
            便于测试或外部代码按名导入。

    Returns:
        本次注册的工具名列表。
    """
    if role not in ROLE_PREFIXES:
        raise ValueError(f"不支持的角色: {role}")

    registered: list[str] = []
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if not is_umu_operation(obj):
            continue

        info = get_operation_info(obj)
        if info is None or role not in info.roles:
            continue

        tool_name = f"{ROLE_PREFIXES[role]}_{info.name}"
        tool_fn = _build_tool_fn(
            operation_fn=obj,
            info=info,
            tool_name=tool_name,
            role=role,
            get_client=get_client,
            ok=ok,
            err=err,
            require_auth=require_auth,
            logger=logger,
        )
        mcp.tool()(tool_fn)
        if namespace is not None:
            namespace[tool_name] = tool_fn
        registered.append(tool_name)

    return registered


def _build_tool_fn(
    operation_fn: Callable[..., Any],
    info: Any,
    tool_name: str,
    role: str,
    get_client: _GetClient,
    ok: _Ok,
    err: _Err,
    require_auth: _RequireAuth | None,
    logger: logging.Logger | None,
) -> Callable[..., Any]:
    """为单个业务函数构建 MCP tool wrapper."""
    sig = inspect.signature(operation_fn)
    params = list(sig.parameters.values())

    # 第一个参数约定为 client: UMUClient，不暴露为 tool 参数
    op_params = params[1:]

    # 解析类型注解，避免 ForwardRef('str') 等问题
    try:
        resolved_hints = typing.get_type_hints(operation_fn)
    except Exception:
        resolved_hints = {}

    # 构造新的参数列表：业务参数（保留原 kind）+ session_id（keyword-only）
    new_params: list[inspect.Parameter] = []

    for param in op_params:
        doc = info.parameter_docs.get(param.name, "")
        base_type = resolved_hints.get(param.name, param.annotation)
        if param.default is inspect.Parameter.empty:
            new_params.append(
                param.replace(
                    annotation=Annotated[base_type, Field(description=doc)],
                    default=inspect.Parameter.empty,
                ),
            )
        else:
            new_params.append(
                param.replace(
                    annotation=Annotated[
                        base_type,
                        Field(default=param.default, description=doc),
                    ],
                    default=param.default,
                ),
            )

    new_params.append(
        inspect.Parameter(
            "session_id",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=Annotated[
                str | None,
                Field(
                    default=None,
                    description="可选的会话 ID。如果提供，在指定会话中执行；如果不提供，使用默认会话。",
                ),
            ],
        ),
    )

    param_names = [p.name for p in op_params]

    async def _tool_wrapper(*args: Any, **kwargs: Any) -> str:
        # 兼容测试与 MCP 框架：位置参数按业务参数顺序映射
        if args:
            if len(args) > len(param_names):
                raise TypeError(
                    f"{tool_name}() takes at most {len(param_names)} positional arguments "
                    f"but {len(args)} were given"
                )
            for name, value in zip(param_names, args):
                if name in kwargs:
                    raise TypeError(f"{tool_name}() got multiple values for argument {name!r}")
                kwargs[name] = value

        session_id = kwargs.pop("session_id", None)
        client = get_client(session_id)

        if require_auth is not None:
            auth_err = require_auth(client)
            if auth_err:
                return err(
                    error_code="NOT_AUTHENTICATED",
                    error_message=auth_err,
                    suggested_action=f"调用 {ROLE_PREFIXES[role]}_login 登录",
                    next_action="retry",
                )

        try:
            result = await operation_fn(client, **kwargs)
            # 业务函数可通过返回 dict 中的 _next_action / _suggested_action
            # 自定义响应信封中的 next_action 与 suggested_action
            if isinstance(result, dict):
                next_action = result.pop("_next_action", "proceed")
                suggested_action = result.pop(
                    "_suggested_action",
                    f"{info.description}成功",
                )
            else:
                next_action = "proceed"
                suggested_action = f"{info.description}成功"
            return ok(
                data=result,
                next_action=next_action,
                suggested_action=suggested_action,
            )
        except UMUError as e:
            if logger is not None:
                logger.exception("%s 失败", info.description)
            return err(
                error_code=e.code or f"{tool_name.upper()}_ERROR",
                error_message=e.args[0] if e.args else str(e),
                suggested_action="请检查参数和权限后重试",
            )
        except Exception as e:
            if logger is not None:
                logger.exception("%s 失败", info.description)
            return err(
                error_code=f"{tool_name.upper()}_ERROR",
                error_message=str(e),
                suggested_action="请检查参数和权限后重试",
            )

    _tool_wrapper.__name__ = tool_name
    _tool_wrapper.__doc__ = info.description
    _tool_wrapper.__signature__ = inspect.Signature(new_params)
    _tool_wrapper.__annotations__ = {p.name: p.annotation for p in new_params}
    _tool_wrapper.__annotations__["return"] = str

    return _tool_wrapper
