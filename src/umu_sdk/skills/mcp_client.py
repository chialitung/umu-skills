# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""Skills 编排层的子 MCP 客户端.

通过 stdio 子进程启动并连接 teacher/student/admin 等子 MCP Server，
提供统一的 `call_tool` 调用接口。
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent

from .config import ServerConfig

logger = logging.getLogger("umu.mcp.skills")


@dataclass
class ToolCallResult:
    """统一后的子 MCP 工具调用结果."""

    success: bool
    data: Any = None
    error_code: str = ""
    error_message: str = ""
    raw: CallToolResult | None = field(default=None, repr=False)

    @classmethod
    def from_call_tool_result(cls, result: CallToolResult) -> ToolCallResult:
        """从 MCP CallToolResult 解析为统一结果."""
        if result.isError:
            text = _extract_text(result)
            parsed = _try_parse_json(text)
            return cls(
                success=False,
                data=parsed,
                error_code=parsed.get("error_code", "TOOL_ERROR") if isinstance(parsed, dict) else "TOOL_ERROR",
                error_message=parsed.get("error_message", text) if isinstance(parsed, dict) else text,
                raw=result,
            )

        text = _extract_text(result)
        parsed = _try_parse_json(text)
        if isinstance(parsed, dict):
            success = parsed.get("success", True)
            return cls(
                success=success,
                data=parsed.get("data"),
                error_code=parsed.get("error_code", ""),
                error_message=parsed.get("error_message", ""),
                raw=result,
            )
        return cls(success=True, data=parsed, raw=result)


def _extract_text(result: CallToolResult) -> str:
    """从 CallToolResult 中提取文本内容."""
    for item in result.content:
        if isinstance(item, TextContent):
            return item.text
    return ""


def _try_parse_json(text: str) -> Any:
    """尝试将文本解析为 JSON，失败时返回原文本."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


class MCPTransport(ABC):
    """子 MCP 通信传输抽象.

    未来可扩展为 SSE、HTTP、in-process 等传输方式。
    """

    @abstractmethod
    async def connect(self) -> None:
        """建立连接."""

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接."""

    @abstractmethod
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> ToolCallResult:
        """调用子 MCP 的某个工具."""

    @abstractmethod
    async def list_tools(self) -> list[str]:
        """列出子 MCP 暴露的所有工具名."""


class StdioMCPTransport(MCPTransport):
    """通过 stdio 子进程连接子 MCP."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self._stdio_context: Any = None
        self._session_context: Any = None
        self._session: ClientSession | None = None
        self._streams: tuple[Any, Any] | None = None

    async def connect(self) -> None:
        params = StdioServerParameters(
            command=self.config.command,
            args=self.config.args,
            env=self.config.env,
        )
        logger.info("[%s] 正在启动子 MCP: %s", self.config.name, self.config.command)
        self._stdio_context = stdio_client(params)
        self._streams = await self._stdio_context.__aenter__()
        read, write = self._streams
        self._session_context = ClientSession(read, write)
        self._session = await self._session_context.__aenter__()
        await self._session.initialize()
        logger.info("[%s] 子 MCP 连接成功", self.config.name)

    async def disconnect(self) -> None:
        logger.info("[%s] 正在关闭子 MCP 连接", self.config.name)
        if self._session_context:
            try:
                await self._session_context.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("[%s] 关闭 session 时出错: %s", self.config.name, e)
            self._session_context = None
        if self._stdio_context:
            try:
                await self._stdio_context.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("[%s] 关闭 stdio 时出错: %s", self.config.name, e)
            self._stdio_context = None
        self._session = None
        self._streams = None

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> ToolCallResult:
        if self._session is None:
            raise RuntimeError(f"子 MCP [{self.config.name}] 未连接")

        timeout = timedelta(seconds=read_timeout_seconds) if read_timeout_seconds else None
        logger.debug("[%s] call_tool: %s args=%s", self.config.name, name, arguments)
        result = await self._session.call_tool(
            name,
            arguments=arguments or {},
            read_timeout_seconds=timeout,
        )
        return ToolCallResult.from_call_tool_result(result)

    async def list_tools(self) -> list[str]:
        if self._session is None:
            raise RuntimeError(f"子 MCP [{self.config.name}] 未连接")

        tools_result = await self._session.list_tools()
        return [tool.name for tool in tools_result.tools]


@dataclass
class SubMCPConnection:
    """一个子 MCP 的连接封装."""

    config: ServerConfig
    transport: MCPTransport

    async def start(self) -> None:
        await self.transport.connect()

    async def stop(self) -> None:
        await self.transport.disconnect()


class MCPClientManager:
    """管理多个子 MCP 连接的入口.

    负责启动、停止所有启用的子 MCP，并提供按 server 名调用的统一接口。
    """

    def __init__(self, servers: list[ServerConfig]) -> None:
        self._servers = {s.name: s for s in servers}
        self._connections: dict[str, SubMCPConnection] = {}

    async def start(self) -> None:
        """启动所有启用的子 MCP 连接."""
        for config in self._servers.values():
            if not config.enabled:
                logger.info("[%s] 已禁用，跳过启动", config.name)
                continue
            transport: MCPTransport = StdioMCPTransport(config)
            conn = SubMCPConnection(config=config, transport=transport)
            try:
                await conn.start()
                self._connections[config.name] = conn
            except Exception as e:
                logger.error("[%s] 子 MCP 启动失败: %s", config.name, e)
                raise RuntimeError(f"子 MCP [{config.name}] 启动失败: {e}") from e

    async def stop(self) -> None:
        """关闭所有子 MCP 连接."""
        for name, conn in self._connections.items():
            try:
                await conn.stop()
            except Exception as e:
                logger.warning("[%s] 关闭子 MCP 时出错: %s", name, e)
        self._connections.clear()

    def list_servers(self) -> list[str]:
        """返回所有已连接（已启用且启动成功）的服务器名称."""
        return list(self._connections.keys())

    def get_connection(self, name: str) -> SubMCPConnection:
        """获取指定子 MCP 的连接."""
        if name not in self._connections:
            available = ", ".join(self._connections.keys()) or "无"
            raise RuntimeError(
                f"子 MCP [{name}] 未连接。可用服务器: {available}"
            )
        return self._connections[name]

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> ToolCallResult:
        """调用指定子 MCP 的指定工具."""
        conn = self.get_connection(server)
        return await conn.transport.call_tool(
            tool,
            arguments=arguments,
            read_timeout_seconds=read_timeout_seconds,
        )

    async def list_server_tools(self, server: str) -> list[str]:
        """列出指定子 MCP 的所有工具名."""
        conn = self.get_connection(server)
        return await conn.transport.list_tools()


__all__ = [
    "MCPTransport",
    "StdioMCPTransport",
    "SubMCPConnection",
    "MCPClientManager",
    "ToolCallResult",
]
