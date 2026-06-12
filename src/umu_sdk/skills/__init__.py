"""UMU Skills 统一技能编排层.

本包提供将多个子 MCP（teacher/student/admin）封装为高阶 Skill 的能力，
让 AI 只需调用一个 Skill 即可完成跨角色的复杂流程。

主要组件：
- `server`：统一 MCP Server 入口
- `config`：子 MCP 配置加载
- `mcp_client`：子 MCP 连接与调用管理
- `registry`：Skill 注册与发现
- `decorators`：`@skill()` 装饰器与 `SkillContext`
- `models`：Skill 相关的 Pydantic 模型
- `builtin`：内置示例 Skill
"""

from .decorators import SkillContext, skill
from .mcp_client import MCPClientManager, StdioMCPTransport
from .models import ServerConfig, SkillInfo, SkillsConfig
from .registry import SkillRegistry
from .server import main, mcp

__all__ = [
    "main",
    "mcp",
    "ServerConfig",
    "SkillsConfig",
    "SkillInfo",
    "SkillContext",
    "skill",
    "SkillRegistry",
    "MCPClientManager",
    "StdioMCPTransport",
]
