# Kimi Code CLI 集成设计

**日期**: 2026-06-27  
**背景**: 项目已支持 Claude Code CLI 与腾讯 WorkBuddy 两种 AI 客户端的一键安装与调用，现需按相同标准增加对 Kimi Code CLI 的支持。

## 1. 目标

- 新增 Kimi Code CLI 一键安装模块 `src/umu_sdk/skills/kimi/install.py`。
- 提供 4 个 Kimi Skill：`umu`、`umu-teacher`、`umu-student`、`umu-admin`。
- 在 `~/.kimi-code/mcp.json` 注册 3 个 stdio MCP server：`umu-teacher`、`umu-student`、`umu-admin`。
- 用户安装后可通过 `/skill:umu`（或简写 `/umu`）及 `/umu-teacher`、`/umu-student`、`/umu-admin` 调用。
- 更新 `pyproject.toml`、`README.md`、`AGENTS.md`，补充 Kimi Code CLI 的安装与使用说明。
- 新增对应测试 `tests/test_kimi_install.py`。

## 2. 设计决策

### 2.1 集成架构：Claude-like 多 Server + 多 Skill

Kimi Code CLI 官方文档表明：

- Skill 格式为带 YAML frontmatter 的 `SKILL.md`，与 Claude Code 相同。
- MCP server 配置文件为 `~/.kimi-code/mcp.json`，支持注册多个 server。
- 外部 Skill 可通过 `/skill:<name>` 调用；名称未与系统命令冲突时可简写为 `/<name>`。

因此采用与 Claude Code 一致的架构：

| 维度 | Claude Code | Kimi Code CLI |
|------|-------------|---------------|
| 配置目录 | `~/.claude/` | `~/.kimi-code/` |
| MCP 配置文件 | `settings.json` 的 `mcpServers` | `mcp.json` 的 `mcpServers` |
| Skill 目录 | `~/.claude/skills/` | `~/.kimi-code/skills/` |
| 角色 Server | `umu-teacher` / `umu-student` / `umu-admin` | `umu-teacher` / `umu-student` / `umu-admin` |
| Skill 数量 | 4 个 | 4 个 |

### 2.2 Skill 安装位置

默认安装到 Kimi 专属目录 `~/.kimi-code/skills/`，支持 `KIMI_CODE_HOME` 环境变量覆盖。不默认写入跨工具的 `~/.agents/skills/`，避免与其他客户端产生命名冲突；后续可通过安装参数扩展。

### 2.3 凭证目录

继续复用项目统一的跨客户端凭证目录 `~/.umu_skills/`，与 Claude Code、WorkBuddy 保持一致。

### 2.4 MCP 配置值

Kimi 官方文档未显示支持 `${VAR:-default}` 环境变量默认值语法，因此安装脚本在写入 `mcp.json` 时直接填充解析后的实际值（如 `https://www.umu.cn`）。

## 3. 新增文件

```text
src/umu_sdk/skills/kimi/
├── __init__.py
├── install.py                      # Kimi 一键安装脚本
└── bundled/
    ├── umu/
    │   └── SKILL.md                # /umu 智能路由 Skill
    ├── umu-teacher/
    │   └── SKILL.md                # /umu-teacher 固定讲师角色
    ├── umu-student/
    │   └── SKILL.md                # /umu-student 固定学员角色
    └── umu-admin/
        └── SKILL.md                # /umu-admin 固定管理员角色
tests/
└── test_kimi_install.py            # 安装脚本测试
```

## 4. 修改文件

### 4.1 `pyproject.toml`

- 新增 console script：
  ```toml
  umu-skills-install-kimi = "umu_sdk.skills.kimi.install:main"
  ```
- 在 `[tool.hatch.build.targets.wheel.include]` 中加入：
  ```toml
  "src/umu_sdk/skills/kimi/bundled/**/*"
  ```

### 4.2 `README.md`

新增「在 Kimi Code CLI 中使用」章节，包含：

- 安装命令：`python -m umu_sdk.skills.kimi.install`
- 安装后重启 Kimi Code CLI。
- 配置账号：`~/.umu_skills/` 或通过环境变量。
- 使用示例：`/umu 创建课程`、`/umu-admin 查询账号列表`。

### 4.3 `AGENTS.md`

- 更新「三种使用形态」为 Claude Code / WorkBuddy / Kimi Code CLI / Python SDK。
- 更新一键安装命令列表，增加 Kimi 安装命令。

## 5. MCP Server 配置

安装后写入 `~/.kimi-code/mcp.json` 的内容示例：

```json
{
  "mcpServers": {
    "umu-teacher": {
      "command": "<当前 Python 解释器>",
      "args": ["-m", "umu_sdk.adapters.mcp.teacher"],
      "env": {
        "UMU_BASE_URL": "https://www.umu.cn",
        "MCP_LOG_LEVEL": "INFO",
        "UMU_SKILL_DIR": "<~/.umu_skills>"
      }
    },
    "umu-student": {
      "command": "<当前 Python 解释器>",
      "args": ["-m", "umu_sdk.adapters.mcp.student"],
      "env": {
        "UMU_BASE_URL": "https://www.umu.cn",
        "MCP_LOG_LEVEL": "INFO",
        "UMU_SKILL_DIR": "<~/.umu_skills>"
      }
    },
    "umu-admin": {
      "command": "<当前 Python 解释器>",
      "args": ["-m", "umu_sdk.adapters.mcp.admin"],
      "env": {
        "UMU_BASE_URL": "https://www.umu.cn",
        "MCP_LOG_LEVEL": "INFO",
        "UMU_SKILL_DIR": "<~/.umu_skills>"
      }
    }
  }
}
```

## 6. Skill 文件格式

每个 `SKILL.md` 采用 Kimi 官方格式：

```markdown
---
name: umu
description: 通过自然语言驱动 UMU 平台，自动识别 Teacher/Student/Admin 角色并调用对应工具
type: prompt
whenToUse: 当用户需要操作 UMU 平台（优幕）进行课程、小节、学员、组织架构等教务管理时
---

当用户调用 /skill:umu 或 /umu 时，按以下流程处理...
```

正文内容改编自现有 `src/umu_sdk/skills/bundled/` 下的 Claude skill，保留：

- 角色路由逻辑
- 工具调用约定
- 统一返回信封说明
- 安全与免责声明

## 7. 安装脚本功能

`python -m umu_sdk.skills.kimi.install` 支持：

| 参数 | 作用 |
|------|------|
| `--check` | 检查当前是否已正确安装 |
| `--upgrade` | 强制重新安装/更新 Skill 与 MCP 配置 |
| `--kimi-code-home <path>` | 自定义 Kimi Code CLI 主目录 |
| `--semantic-trigger` | 控制 `umu` Skill 的模型自动调用开关 |
| `alias add/remove/list` | 管理 Skill 别名（如 `umut` → `umu-teacher`） |

安装流程：

1. 探测或确认 `~/.kimi-code/` 目录（优先 `KIMI_CODE_HOME`）。
2. 确保 `umu-skills[mcp]` 已安装，否则提示用户安装。
3. 将 4 个 Skill 目录复制到 `~/.kimi-code/skills/`。
4. 读取/创建 `~/.kimi-code/mcp.json`，注入 3 个 server 配置。
5. 初始化 `~/.umu_skills/` 凭证目录。

## 8. 测试策略

新增 `tests/test_kimi_install.py`，覆盖：

- MCP server 配置写入与更新（不破坏已有其他 server）。
- Skill 目录复制与内容校验。
- `KIMI_CODE_HOME` 环境变量覆盖默认目录。
- `--check` 正确检测安装状态。
- `--upgrade` 正确覆盖旧配置。
- 别名增删查。

## 9. 与现有安装器的关系

- 不修改 `src/umu_sdk/skills/install.py`（Claude）和 `src/umu_sdk/skills/workbuddy/install.py` 的现有行为。
- Kimi 安装器作为独立模块，自行实现目录探测、配置读写等逻辑；仅在确实存在重复且稳定的辅助函数时，才考虑提取到 `src/umu_sdk/skills/utils.py` 这类公共模块。
- Skill 正文尽量与 Claude 版本同源，降低后续维护成本。
