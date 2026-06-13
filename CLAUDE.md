# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

UMU Skills is a **Skill** — an AI skill framework for operating the UMU (优幕) online learning platform. It wraps UMU's management and learning operations as AI-callable tools via the Model Context Protocol (MCP), enabling integration with Claude, Cursor, Cline, and other AI clients.

- **Language**: Python >= 3.10
- **Build system**: hatchling (pyproject.toml)
- **Package name**: `umu-skills`
- **Source layout**: `src/umu_sdk/`

### Product Positioning

This is not a general-purpose SDK. It is a **role-based Skill framework** where each MCP server operates under a specific user identity within the UMU platform:

| MCP Server | Identity | Purpose |
|------------|----------|---------|
| **Teacher MCP** | Lecturer | Create courses, manage content, upload resources (SCORM, documents, videos, images), add/modify sections, configure course settings |
| **Student MCP** | Learner | Enroll in courses, browse lessons, complete sections (videos, articles, questionnaires, exams, check-ins), track learning progress |
| **Admin MCP** | Administrator | Manage accounts, org structure (departments/groups/classes), learning records, learning programs, courses, and other backend operations |

### Development Roadmap

New features and tools should be added under the corresponding role:

- **Teacher domain**: Course creation, resource management, section editing, batch operations → `adapters/mcp/teacher.py` and supporting modules
- **Student domain**: Learning flow, progress tracking, enrollment, completion → `adapters/mcp/student.py`
- **Admin domain**: Account management, org structure, learning records, learning programs, course data → `adapters/mcp/admin.py` and supporting modules

Admin MCP follows the same pattern as Teacher/Student MCP servers and reuses the existing `core/` infrastructure (client, auth, encrypt, session manager).

## Branch Workflow

### Local `develop`, remote `master` only

- `develop` 是本地开发分支，**不允许推送到远程仓库**。
- 远程仓库只保留 `master` 与语义化版本标签。
- 当功能在本地 `develop` 上完成后，通过 `git merge develop` 合并到 `master`，然后按 **Minimal Release Command** 一键发布。

### Merging `develop` into `master` means a release

把 `develop` 合并到 `master` 并推送到远程仓库，视为一次发布。每次发布必须完成以下清单：

1. **更新解释性文件**
   - `pyproject.toml`: 升级 `version` 到新的 SemVer。
   - `CHANGELOG.md`: 添加 `[x.y.z] - YYYY-MM-DD` 小节总结变更。
   - **`README.md`: 必须检查并更新安装说明、功能列表、环境变量表、MCP server 启动方式等与本次发布相关的内容。** 即使只有小幅功能变更，也应确认 README 已反映当前版本状态；无变更不是跳过检查的理由。
   - 其他解释性文档（如存在）仅在实际受影响时更新。

2. **校验最小化内容**
   - 无 `.env` 文件、凭据、token、密钥或个人数据。
   - 无临时文件、构建产物、`__pycache__`、`.pytest_cache`、`dist/`、`*。egg-info/`。
   - 无无关代码、实验性或进行中的工作。
   - 确认 `.gitignore` 已排除上述内容。

3. **本地跑通质量门**
   ```bash
   pytest tests/ -v
   ruff check src/
   mypy src/
   python -m build
   ```

4. **提交并推送 `master`**
   ```bash
   git add pyproject.toml CHANGELOG.md README.md
   git commit -m "chore(release): bump version to x.y.z"
   git push origin master
   ```

5. **打语义化标签并推送**
   ```bash
   git tag -a vx.y.z -m "Release vx.y.z"
   git push origin vx.y.z
   ```

6. **验证 PyPI 发布包可正常安装**
   ```bash
   pip install umu-skills==x.y.z
   ```

7. **将发布变更同步回本地 `develop`**
   由于 `README.md` 和 `CHANGELOG.md` 等解释性文件在 `master` 上随发布被更新，而 `develop` 不推送到远程，必须在发布完成后把 `master` 合并回 `develop`，保持两个分支的解释性文件一致，避免后续发布出现版本/日志冲突。
   ```bash
   git checkout develop
   git merge master
   ```
   **注意**：此步骤仅同步本地分支，`develop` 仍然不允许推送到远程。

**敏感凭据只允许存在于 GitHub Secrets (`PYPI_API_TOKEN`)**，绝不允许写入仓库。

## Common Commands

```bash
# Development install (editable mode with all extras)
pip install -e ".[dev,mcp]"

# Run tests
pytest tests/ -v

# Run a single test file
pytest tests/test_session.py -v

# Run a specific test
pytest tests/test_session.py::test_function_name -v

# Lint
ruff check src/

# Type check
mypy src/

# Build package
python -m build

# Start MCP servers (requires env vars)
umu-skills-teacher  # UMU_TEACHER_USERNAME, UMU_TEACHER_PASSWORD
umu-skills-student  # UMU_STUDENT_USERNAME, UMU_STUDENT_PASSWORD
umu-skills-admin    # UMU_ADMIN_USERNAME, UMU_ADMIN_PASSWORD

# Or use the module entry point (does not require PATH setup)
python -m umu_sdk.adapters.mcp.teacher
python -m umu_sdk.adapters.mcp.student
python -m umu_sdk.adapters.mcp.admin
```

## Architecture

### Layer Separation

The codebase follows a strict separation between business logic and protocol adapters:

```
src/umu_sdk/
├── core/          # SDK foundation — HTTP client, auth, encryption, models, errors
├── endpoints/     # Business endpoint abstractions (currently CourseEndpoint framework)
├── tools/         # Business logic layer (teacher/student/domain domains)
│   ├── teacher/   # Placeholder package for teacher domain logic
│   ├── student/   # Placeholder package for student domain logic
│   └── domain/    # Placeholder for enterprise domain
├── adapters/mcp/  # MCP protocol adapters (FastMCP servers)
│   ├── admin.py   # Admin MCP server (~41 tools)
│   ├── teacher.py # Teacher MCP server (~54 tools)
│   ├── student.py # Student MCP server (~24 tools)
│   ├── session.py # Multi-user session manager
│   ├── utils.py   # Shared MCP helpers (login identity, formatting)
│   ├── course_builder.py  # Course/section creation API orchestration
│   ├── cos_upload.py      # Tencent COS SCORM upload with multipart/concurrency
│   ├── document_upload.py # Document upload (small file direct + large file multipart)
│   ├── video_upload.py    # Audio/video upload (36 formats supported)
│   ├── image_upload.py    # Image upload handler
│   ├── batch.py           # Batch operations (account import, concurrent execution)
│   └── prompts.py         # MCP prompt templates
└── skills/        # 声明式 Skill 编排层（已实现）
    ├── builtin/   # 内置高频 Skill（teacher/student/admin）
    │   ├── course_creation.py       # 创建课程 + SCORM 小节
    │   ├── learning_flow.py         # 报名、学习进度
    │   ├── onboarding.py            # 批量开户并报名
    │   ├── teacher_resources.py     # 资源上传与列表
    │   ├── teacher_sections.py      # 小节创建与列表
    │   ├── teacher_courses.py       # 课程查询
    │   ├── student_learning.py      # 学习流程（解析、浏览、签到）
    │   ├── student_assessment.py    # 问卷与考试
    │   ├── student_course_completion.py  # 自动完成课程
    │   ├── admin_organization.py    # 部门/分组/班级查询
    │   ├── admin_accounts.py        # 账号查询、禁用/启用、编辑
    │   ├── admin_courses.py         # 企业课程查询
    │   ├── admin_data.py            # 学习记录查询
    │   └── admin_learning_programs.py  # 学习项目查询
    ├── decorators.py    # @skill 装饰器与 SkillContext
    ├── registry.py      # SkillRegistry 自动加载
    ├── mcp_client.py    # 子 MCP 客户端管理
    ├── server.py        # Orchestrator MCP server（skill_run / skill_call_atomic_tool）
    └── config.py        # 配置与安装脚本
```

**Key principle**: Business logic lives in `tools/`, protocol adapters in `adapters/`. Adding a new AI platform only requires adding a new adapter.

### Skill Layer

`skills/` 是高阶编排层，面向 AI 提供语义化操作：

- **Skill 封装高频流程**：每个 `@skill` 函数收敛原子工具参数、处理错误、返回统一信封。新增 Skill 放入 `builtin/` 包即可自动注册。
- **透传兜底**：`skill_call_atomic_tool(server, tool, arguments)` 允许 AI 直接调用任何原子工具，用于低频或尚未封装的能力。优先使用 `skill_run`。
- **统一返回信封**：所有 Skill 与透传工具返回 `{success, data, error_code, error_message, suggested_action, next_action}`。
- **跨角色组合**：`SkillContext.call_tool()` 可在同一 Skill 内调用 teacher/student/admin 多个子 MCP。

### Core Components

**`core/client.py` — UMUClient**
- Synchronous HTTP client built on `httpx`
- Dual-domain URL building: `desktop_url()` (www) and `mobile_url()` (m)
- Auto-retry with exponential backoff (configurable retries, default 3)
- Custom error translation: HTTP status → typed UMUError subclasses
- `endpoint()` method for environment-specific path overrides

**`core/auth.py` — AuthManager**
- Login via `POST /passport/ajax/account/login`
- Password is AES-256-CBC encrypted before transmission
- Token extracted from `estuidtoken` cookie
- Token expiry: assumed 24h (no refresh mechanism)

**`core/encrypt.py` — Password Encryption**
- AES-256-CBC with hardcoded key/IV (reverse-engineered from UMU webpack)
- Key: `muumuumuumuumuumuumuumumumuumuum`, IV: `mumumuumumumumum`
- Padding: PKCS7, Output: Base64

**`adapters/mcp/session.py` — SessionManager**
- Multi-user session isolation: each session has its own `UMUClient` (independent `httpx.Client` + cookie jar)
- TTL-based auto-expiry (default 24h), max sessions 100
- Coroutine-safe (`asyncio.Lock`)
- Both async and sync session getters (`get_session()` / `get_session_sync()`)

### MCP Server Structure

Both `teacher.py` and `student.py` follow the same pattern:

1. **Global instances**: `_umu_client`, `_session_manager` (managed by lifespan)
2. **Lifespan**: `app_lifespan()` async context manager that:
   - Reads credentials from environment variables on startup
   - Creates default session (optionally auto-login if credentials present)
   - Cleans up all sessions on shutdown
3. **Tool helpers**: `_get_client(session_id)`, `_require_auth()`, `_ok()`, `_err()`
4. **Tool registration**: `@mcp.tool()` decorated async functions returning JSON strings

**Student-specific patterns**:
- `_makeweikestatus_sequence()` — Standard state machine for lesson completion: init(0) → start(1) → playing(3) → achieve(3, vlt_status=1) → end(2)
- Course identifier resolution: supports access code (`aet504`), short domain, full URL (but not bare groupId — sKey is required)
- Enrollment detection via multi-layer strategy (page redirect + API permission checks)

**Teacher-specific patterns**:
- Windows UTF-8 encoding fix at the very top of the file (must run before any imports)
- Structured logging via `logging.getLogger("umu.mcp.teacher")`
- Course creation flow: `create_course()` → `create_*_session()` → `bind_resource()`
- SCORM upload: get COS credentials → multipart upload → register → poll status

### Response Format

All MCP tools return JSON strings with a standardized envelope:

```json
{
  "success": true/false,
  "data": {...},
  "error_code": "",
  "error_message": "",
  "suggested_action": "",
  "next_action": "proceed|needs_enrollment|needs_user_input|lesson_completed"
}
```

### Error Hierarchy

```
UMUError (base)
├── AuthenticationError (401)
├── ValidationError (422)
├── RateLimitError (429)
├── ServerError (5xx)
└── NetworkError (connection)
```

#### Environment Variable Reloading for Default Credentials

**Default auto-login and login-related tools MUST re-read account credentials from the `.env` file on every invocation/startup.**

Do not rely on `os.environ` values that were loaded once when the Python process started. The `.env` file may be edited after the process begins, and the latest credentials must be picked up.

Requirements:

1. **Use `core.env_loader.load_env_credentials(role)`** to read credentials for the current MCP role (`admin`, `teacher`, `student`).
2. **Call sites**:
   - `app_lifespan()` default auto-login before calling `login_session()`.
   - Login tools (`adm_login`, `tch_login`, `stu_login`) when credentials are used as defaults or fallbacks.
3. **Behavior**: if the `.env` file is missing or the role variables are not set, gracefully fall back to the current `os.environ` value or skip auto-login.
4. **Do not cache** the parsed `.env` dictionary across calls; parse it every time.

Example:

```python
from ...core.env_loader import load_env_credentials

username, password = load_env_credentials("admin")
if username and password:
    await _session_manager.login_session(session_id, username, password)
```

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `UMU_BASE_URL` | UMU platform URL (default: `https://www.umu.cn`) |
| `UMU_TEACHER_USERNAME` / `UMU_TEACHER_PASSWORD` | Auto-login for teacher MCP |
| `UMU_STUDENT_USERNAME` / `UMU_STUDENT_PASSWORD` | Auto-login for student MCP |
| `UMU_ADMIN_USERNAME` / `UMU_ADMIN_PASSWORD` | Auto-login for admin MCP |
| `MCP_LOG_LEVEL` | Teacher MCP log level (default: INFO) |
| `MCP_LOG_FORMAT` | Teacher MCP log format |

## CI/CD

GitHub Actions workflow (`.github/workflows/ci.yml`):
- Runs on pushes to `develop`/`master` and PRs to `master`
- Matrix: Python 3.10, 3.11, 3.12
- Steps: install deps → pytest → ruff → mypy

## Tool Configuration

- **ruff**: line-length 100, target Python 3.10
- **pytest**: asyncio mode auto, `pythonpath = ["src"]`
- **mypy**: strict mode, Python 3.10 target

## Development Rules

### Pagination Progress Reporting

**All automatic pagination / fetch-all loops MUST report progress to the console.**

This applies to any MCP tool that internally walks through multiple pages (e.g. `fetch_all=True`, batch list enumeration, recursive resource listing). Single-page queries do not need progress output.

Requirements:

1. **Output destination**: print progress to `sys.stderr`, never `sys.stdout`. MCP servers communicate over stdio; writing to stdout corrupts the JSON-RPC stream.
2. **Information to include**:
   - Current page number
   - Items fetched so far
   - Total items expected (if known from the first response)
   - Percentage (when total is known)
   - Completion message when done
   - Safety-limit warning if the loop hits a hard cap
3. **Format example**:
   ```text
   [adm_list_accounts] 正在获取第 3 页，已获取 1250 / 5000 条 (25%)...
   [adm_list_accounts] 获取完成，共 5000 条，合计 10 页
   [adm_list_accounts] 警告：达到 50 页安全上限，停止获取（已获取 25000 条）
   ```
4. **Implementation**: use a small helper or inline `print(..., file=sys.stderr)` inside the loop. Keep log lines concise and avoid flooding the console on very small result sets.
5. **Future tools**: when adding a new fetch-all/pagination loop, follow the same pattern and update this rule if the scenario introduces new requirements.

### Local Workbench (Temporary Files)

项目根目录下的 `workbench/` 目录是本地临时工作区，用于存放非交付的辅助文件，例如：

- `hars/` — HAR 抓包文件（接口分析）
- `outputs/` — 工具调用结果、API 响应导出、运行日志
- `scripts/` — 一次性调试脚本、问题复现脚本
- `scratch/` — 其他临时草稿、实验数据

**约束：**

1. `workbench/` 及其全部内容已加入 `.gitignore`，不得提交到仓库，也不得包含在发布包中。
2. **禁止自动删除、移动或清理 `workbench/` 下的任何文件。** 只有在用户明确指示时才能操作。
3. 这些文件只能由用户手动管理和删除；AI / 工具脚本不得擅自处理。
4. 生成临时输出时，应优先写入 `workbench/` 的对应子目录，避免污染源码树。

### Minimal Release Command

**One-line release instruction for automation:**

> 将 `master` 分支按最小化原则一键发布至远程仓库，同步更新 `README.md`、`CHANGELOG.md`、`pyproject.toml` 版本号及相关解释性文件，在确认无敏感信息、仅包含项目必需内容且其他用户端可正常使用后，创建并推送语义化标签以触发 GitHub Actions 自动构建并发布到 PyPI。

把本地 `develop` 合并到 `master` 并推送到远程仓库，视为一次发布。具体执行步骤见 [Branch Workflow](#branch-workflow) 中的发布清单。

## Important Notes

- The UMU API is reverse-engineered; endpoints may change. The codebase relies on actual HAR analysis.
- `courses.py` endpoint is a framework abstraction; the actual UMU API calls are inline in the MCP tool functions.
- `teacher.py` has a Windows-specific UTF-8 fix that must remain at the top of the file, before all other imports.
- Tests use `pytest-asyncio` with automatic marker injection via `conftest.py`.
