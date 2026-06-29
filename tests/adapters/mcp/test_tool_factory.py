"""MCP Tool 工厂测试."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import FastMCP

from umu_sdk.adapters.mcp.tool_factory import register_operations
from umu_sdk.core.client import UMUClient
from umu_sdk.tools.decorators import umu_operation


@umu_operation(
    name="demo_operation",
    description="演示操作",
    roles=["teacher", "admin"],
    parameter_docs={"arg1": "参数1"},
)
async def demo_operation(client: UMUClient, arg1: str) -> dict:
    """演示操作."""
    return {"arg1": arg1}


@pytest.fixture
def mock_module():
    import types

    module = types.ModuleType("mock_ops")
    module.demo_operation = demo_operation
    return module


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.auth.is_authenticated.return_value = True
    return client


def _get_client(session_id: str | None = None) -> UMUClient:
    return MagicMock(spec=UMUClient)


def _require_auth(client: UMUClient) -> str | None:
    if not client.auth.is_authenticated():
        return "未登录"
    return None


def _ok(data=None, **kwargs):
    import json

    return json.dumps({"success": True, "data": data, **kwargs}, ensure_ascii=False)


def _err(error_code="", error_message="", **kwargs):
    import json

    return json.dumps(
        {"success": False, "error_code": error_code, "error_message": error_message, **kwargs},
        ensure_ascii=False,
    )


class TestRegisterOperations:
    async def test_registers_tools_for_role(self, mock_module):
        mcp = FastMCP("test-teacher")
        registered = register_operations(
            mcp=mcp,
            module=mock_module,
            role="teacher",
            get_client=_get_client,
            ok=_ok,
            err=_err,
        )
        assert "tch_demo_operation" in registered
        tools = mcp._tool_manager._tools
        assert "tch_demo_operation" in tools
        assert "adm_demo_operation" not in tools

    async def test_tool_signature(self, mock_module):
        mcp = FastMCP("test-admin")
        register_operations(
            mcp=mcp,
            module=mock_module,
            role="admin",
            get_client=_get_client,
            ok=_ok,
            err=_err,
        )
        tool_fn = mcp._tool_manager._tools["adm_demo_operation"].fn
        sig = inspect.signature(tool_fn)
        params = list(sig.parameters.keys())
        assert "arg1" in params
        assert "session_id" in params
        assert sig.parameters["session_id"].kind is inspect.Parameter.KEYWORD_ONLY

    async def test_tool_execution(self, mock_module, mock_client):
        mcp = FastMCP("test-teacher")
        register_operations(
            mcp=mcp,
            module=mock_module,
            role="teacher",
            get_client=lambda _sid: mock_client,
            ok=_ok,
            err=_err,
            require_auth=_require_auth,
        )
        tool_fn = mcp._tool_manager._tools["tch_demo_operation"].fn
        result = await tool_fn(arg1="hello")
        assert '"success": true' in result
        assert '"arg1": "hello"' in result

    async def test_tool_auth_failure(self, mock_module, mock_client):
        mock_client.auth.is_authenticated.return_value = False
        mcp = FastMCP("test-teacher")
        register_operations(
            mcp=mcp,
            module=mock_module,
            role="teacher",
            get_client=lambda _sid: mock_client,
            ok=_ok,
            err=_err,
            require_auth=_require_auth,
        )
        tool_fn = mcp._tool_manager._tools["tch_demo_operation"].fn
        result = await tool_fn(arg1="hello")
        assert '"success": false' in result
        assert "NOT_AUTHENTICATED" in result

    async def test_unsupported_role(self, mock_module):
        mcp = FastMCP("test-invalid")
        with pytest.raises(ValueError, match="不支持的角色"):
            register_operations(
                mcp=mcp,
                module=mock_module,
                role="invalid",
                get_client=_get_client,
                ok=_ok,
                err=_err,
            )
