# AGENTS.md

本文件为 Codex（Codex.ai/code）提供在本仓库中工作时的指导。

## 项目概览

UMU Skills 是一个 **Skill**——用于操作 UMU（优幕）在线学习平台的 AI 技能框架。它通过 Model Context Protocol（MCP）将 UMU 的管理与学习操作封装为 AI 可调用的工具，支持与 Codex、Cursor、Cline 等 AI 客户端集成。

- **语言**: Python >= 3.10
- **构建系统**: hatchling（pyproject.toml）
- **包名**: `umu-skills`
- **源码布局**: `src/umu_sdk/`

### 产品定位

这不是一个通用 SDK，而是一个**基于角色的 Skill 框架**，每个 MCP server 在 UMU 平台中以特定用户身份运行：

| MCP Server | 身份 | 用途 |
|------------|------|------|
| **Teacher MCP** | 讲师 | 创建课程、管理内容、上传资源（SCORM、文档、视频、图片）、添加/修改小节、配置课程设置 |
| **Student MCP** | 学员 | 报名课程、浏览课程、完成小节（视频、文章、问卷、考试、签到）、跟踪学习进度 |
| **Admin MCP** | 管理员 | 账号管理、组织架构（部门/分组/班级）、学习记录、学习项目、课程及其他后台操作 |

### 开发路线图

新功能和工具应按对应角色添加：

- **讲师域**: 课程创建、资源管理、小节编辑、批量操作 → `adapters/mcp/teacher.py` 及配套模块
- **学员域**: 学习流程、进度跟踪、报名、完成 → `adapters/mcp/student.py`
- **管理员域**: 账号管理、组织架构、学习记录、学习项目、课程数据 → `adapters/mcp/admin.py` 及配套模块

Admin MCP 遵循与 Teacher/Student MCP server 相同的模式，并复用现有的 `core/` 基础设施（client、auth、encrypt、session manager）。

## 分支工作流

### 仅本地 `develop`，远程 `master`

- `develop` 是本地开发分支，**不允许推送到远程仓库**。
- 远程仓库只保留 `master` 与语义化版本标签。
- 当功能在本地 `develop` 上完成后，通过 `git merge develop` 合并到 `master`，然后按 **最小发布命令** 一键发布。

### 将 `develop` 合并到 `master` 意味着一次发布

把 `develop` 合并到 `master` 并推送到远程仓库，视为一次发布。每次发布必须完成以下清单：

1. **更新解释性文件**
   - `pyproject.toml`: 升级 `version` 到新的 SemVer。
   - `CHANGELOG.md`: 添加 `[x.y.z] - YYYY-MM-DD` 小节总结变更。
   - **`README.md`: 必须按下方结构化清单逐项检查并更新。** 即使只有小幅功能变更，也应确认 README 已反映当前版本状态；无变更不是跳过检查的理由。
     - [ ] **工具列表完整性**：管理员工具、教师工具、学生工具表格中列出的工具名与代码一致，无遗漏、无已删除工具残留。
     - [ ] **Skill 列表完整性**：内置 Skill 表格中列出的 Skill 名与代码一致，无遗漏、无已删除 Skill 残留。
     - [ ] **数量标题**：工具/Skill 的数量标题（如"管理员工具（50）"）与列表实际条目数一致。
     - [ ] **安装说明 / 环境变量表 / MCP server 启动方式**：若本次发布有变更则更新，无变更也需确认。
   - 其他解释性文档（如存在）仅在实际受影响时更新。

2. **运行发布就绪检查脚本（阻塞项）**
   在提交 release 前必须运行自动化检查脚本，任何失败都必须在继续发布前修复：
   ```bash
   python .github/scripts/check_release_readiness.py
   ```
   该脚本会强制核对：
   - `pyproject.toml` 版本与 `CHANGELOG.md` 最新小节一致。
   - `README.md` 中列出的管理员工具、教师工具、学生工具名称集合与代码中实际定义的工具名称集合一致，且数量标题一致。
   - `README.md` 中列出的内置 Skill 名称集合与代码中实际定义的 Skill 名称集合一致，且数量标题一致。
   **脚本未通过时，禁止执行 `git commit` 和推送。**

3. **校验最小化内容**
   - 无 `.env` 文件、凭据、token、密钥或个人数据。
   - 无临时文件、构建产物、`__pycache__`、`.pytest_cache`、`dist/`、`*。egg-info/`。
   - 无无关代码、实验性或进行中的工作。
   - 确认 `.gitignore` 已排除上述内容。

4. **本地跑通质量门**
   ```bash
   pytest tests/ -v
   ruff check src/
   mypy src/
   python -m build
   ```

5. **提交并推送 `master`**
   ```bash
   git add pyproject.toml CHANGELOG.md README.md
   git commit -m "chore(release): bump version to x.y.z"
   git push origin master
   ```

6. **打语义化标签并推送**
   ```bash
   git tag -a vx.y.z -m "Release vx.y.z"
   git push origin vx.y.z
   ```

7. **验证 PyPI 发布包可正常安装**
   ```bash
   pip install umu-skills==x.y.z
   ```

8. **将发布变更同步回本地 `develop`**
   由于 `README.md` 和 `CHANGELOG.md` 等解释性文件在 `master` 上随发布被更新，而 `develop` 不推送到远程，必须在发布完成后把 `master` 合并回 `develop`，保持两个分支的解释性文件一致，避免后续发布出现版本/日志冲突。
   ```bash
   git checkout develop
   git merge master
   ```
   **注意**：此步骤仅同步本地分支，`develop` 仍然不允许推送到远程。

**敏感凭据只允许存在于 GitHub Secrets (`PYPI_API_TOKEN`)**，绝不允许写入仓库。

## 常用命令

```bash
# 开发安装（editable 模式，包含所有 extras）
pip install -e ".[dev,mcp]"

# 运行测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_session.py -v

# 运行特定测试
pytest tests/test_session.py::test_function_name -v

# 代码风格检查
ruff check src/

# 类型检查
mypy src/

# 构建包
python -m build

# 启动 MCP server（需要对应环境变量）
umu-skills-teacher  # UMU_TEACHER_USERNAME, UMU_TEACHER_PASSWORD
umu-skills-student  # UMU_STUDENT_USERNAME, UMU_STUDENT_PASSWORD
umu-skills-admin    # UMU_ADMIN_USERNAME, UMU_ADMIN_PASSWORD

# 或使用模块入口点（无需配置 PATH）
python -m umu_sdk.adapters.mcp.teacher
python -m umu_sdk.adapters.mcp.student
python -m umu_sdk.adapters.mcp.admin
```

## 架构

### 分层分离

代码库严格区分业务逻辑与协议适配层：

```
src/umu_sdk/
├── core/          # SDK 基础 — HTTP client、auth、加密、models、errors
├── endpoints/     # 业务端点抽象（当前为 CourseEndpoint 框架）
├── tools/         # 业务逻辑层（teacher/student/domain 域）
│   ├── teacher/   # 讲师域逻辑占位包
│   ├── student/   # 学员域逻辑占位包
│   └── domain/    # 企业域占位包
├── adapters/mcp/  # MCP 协议适配层（FastMCP server）
│   ├── admin.py   # Admin MCP server（约 41 个 tools）
│   ├── teacher.py # Teacher MCP server（约 54 个 tools）
│   ├── student.py # Student MCP server（约 24 个 tools）
│   ├── session.py # 多用户 session 管理器
│   ├── utils.py   # MCP 公共辅助函数（登录身份、格式化）
│   ├── course_builder.py  # 课程/小节创建 API 编排
│   ├── cos_upload.py      # 腾讯云 COS SCORM 上传（分片/并发）
│   ├── document_upload.py # 文档上传（小文件直传 + 大文件分片）
│   ├── video_upload.py    # 音视频上传（支持 36 种格式）
│   ├── image_upload.py    # 图片上传处理
│   ├── batch.py           # 批量操作（账号导入、并发执行）
│   └── prompts.py         # MCP prompt 模板
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

**核心原则**: 业务逻辑位于 `tools/`，协议适配位于 `adapters/`。新增 AI 平台只需新增一个 adapter。

### Skill 层

`skills/` 是高阶编排层，面向 AI 提供语义化操作：

- **Skill 封装高频流程**：每个 `@skill` 函数收敛原子工具参数、处理错误、返回统一信封。新增 Skill 放入 `builtin/` 包即可自动注册。
- **透传兜底**：`skill_call_atomic_tool(server, tool, arguments)` 允许 AI 直接调用任何原子工具，用于低频或尚未封装的能力。优先使用 `skill_run`。
- **统一返回信封**：所有 Skill 与透传工具返回 `{success, data, error_code, error_message, suggested_action, next_action}`。
- **跨角色组合**：`SkillContext.call_tool()` 可在同一 Skill 内调用 teacher/student/admin 多个子 MCP。

### 核心组件

**`core/client.py` — UMUClient**
- 基于 `httpx` 的同步 HTTP client
- 双域名 URL 构建：`desktop_url()`（www）与 `mobile_url()`（m）
- 指数退避自动重试（重试次数可配置，默认 3）
- 自定义错误转换：HTTP 状态码 → 具体的 UMUError 子类
- `endpoint()` 方法用于环境相关的路径覆盖

**`core/auth.py` — AuthManager**
- 通过 `POST /passport/ajax/account/login` 登录
- 密码在传输前经 AES-256-CBC 加密
- 从 `estuidtoken` cookie 中提取 token
- Token 有效期：假设为 24 小时（无刷新机制）

**`core/encrypt.py` — 密码加密**
- AES-256-CBC，key/IV 硬编码（从 UMU webpack 反编译得到）
- Key: `muumuumuumuumuumuumuumumumuumuum`, IV: `mumumuumumumumum`
- 填充：PKCS7，输出：Base64

**`adapters/mcp/session.py` — SessionManager**
- 多用户 session 隔离：每个 session 拥有独立的 `UMUClient`（独立的 `httpx.Client` + cookie jar）
- 基于 TTL 的自动过期（默认 24 小时），最大 session 数 100
- 协程安全（`asyncio.Lock`）
- 同时提供异步与同步 session 获取方法（`get_session()` / `get_session_sync()`）

### MCP Server 结构

`teacher.py` 与 `student.py` 遵循相同模式：

1. **全局实例**: `_umu_client`、`_session_manager`（由 lifespan 管理）
2. **Lifespan**: `app_lifespan()` 异步上下文管理器：
   - 启动时从环境变量读取凭据
   - 创建默认 session（若存在凭据则可选自动登录）
   - 关闭时清理所有 session
3. **工具辅助函数**: `_get_client(session_id)`、`_require_auth()`、`_ok()`、`_err()`
4. **工具注册**: 使用 `@mcp.tool()` 装饰的异步函数，返回 JSON 字符串

**学员端特有模式**:
- `_makeweikestatus_sequence()` — 课程完成标准状态机：init(0) → start(1) → playing(3) → achieve(3, vlt_status=1) → end(2)
- 课程标识解析：支持访问码（`aet504`）、短域名、完整 URL（不支持裸 groupId —— 必须提供 sKey）
- 通过多层策略检测报名状态（页面重定向 + API 权限检查）

**讲师端特有模式**:
- 文件顶部的 Windows UTF-8 编码修复（必须位于所有 import 之前）
- 通过 `logging.getLogger("umu.mcp.teacher")` 进行结构化日志
- 课程创建流程：`create_course()` → `create_*_session()` → `bind_resource()`
- SCORM 上传：获取 COS 凭据 → 分片上传 → 注册 → 轮询状态

### 响应格式

所有 MCP 工具返回标准信封的 JSON 字符串：

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

### 错误层级

```
UMUError (base)
├── AuthenticationError (401)
├── ValidationError (422)
├── RateLimitError (429)
├── ServerError (5xx)
└── NetworkError (connection)
```

#### 默认凭据的环境变量重载

**默认自动登录及登录相关工具必须在每次调用/启动时从 `.env` 文件重新读取账号凭据。**

不要依赖 Python 进程启动时加载一次的 `os.environ` 值。`.env` 文件可能在进程运行期间被编辑，必须采用最新凭据。

要求：

1. **使用 `core.env_loader.load_env_credentials(role)`** 读取当前 MCP 角色（`admin`、`teacher`、`student`）的凭据。
2. **调用点**：
   - `app_lifespan()` 在调用 `login_session()` 前进行默认自动登录。
   - 登录工具（`adm_login`、`tch_login`、`stu_login`）在凭据用作默认值或 fallback 时。
3. **行为**：若 `.env` 文件缺失或角色变量未设置，优雅回退到当前 `os.environ` 值或跳过自动登录。
4. **不要缓存** 解析后的 `.env` 字典；每次调用都重新解析。

示例：

```python
from ...core.env_loader import load_env_credentials

username, password = load_env_credentials("admin")
if username and password:
    await _session_manager.login_session(session_id, username, password)
```

## 环境变量

| 变量 | 用途 |
|------|------|
| `UMU_BASE_URL` | UMU 平台 URL（默认：`https://www.umu.cn`） |
| `UMU_TEACHER_USERNAME` / `UMU_TEACHER_PASSWORD` | Teacher MCP 自动登录 |
| `UMU_STUDENT_USERNAME` / `UMU_STUDENT_PASSWORD` | Student MCP 自动登录 |
| `UMU_ADMIN_USERNAME` / `UMU_ADMIN_PASSWORD` | Admin MCP 自动登录 |
| `MCP_LOG_LEVEL` | Teacher MCP 日志级别（默认：INFO） |
| `MCP_LOG_FORMAT` | Teacher MCP 日志格式 |

## CI/CD

GitHub Actions 工作流（`.github/workflows/ci.yml`）：
- 在 `develop`/`master` 推送及针对 `master` 的 PR 时触发
- 矩阵：Python 3.10、3.11、3.12
- 步骤：安装依赖 → pytest → ruff → mypy

## 工具配置

- **ruff**: 行长度 100，目标 Python 3.10
- **pytest**: asyncio mode auto，`pythonpath = ["src"]`
- **mypy**: strict mode，目标 Python 3.10

## 开发规则

### 分页进度上报

**所有自动分页 / 全量获取循环都必须向控制台报告进度。**

适用于任何在内部遍历多页的 MCP 工具（例如 `fetch_all=True`、批量列表枚举、递归资源列表）。单页查询无需进度输出。

要求：

1. **输出目标**：将进度打印到 `sys.stderr`，绝不要写到 `sys.stdout`。MCP server 通过 stdio 通信；写入 stdout 会破坏 JSON-RPC 流。
2. **需包含的信息**：
   - 当前页码
   - 已获取条目数
   - 预期总条目数（若已从首次响应中获知）
   - 百分比（当总数已知时）
   - 完成时的提示
   - 达到硬上限时的安全限制警告
3. **格式示例**：
   ```text
   [adm_list_accounts] 正在获取第 3 页，已获取 1250 / 5000 条 (25%)...
   [adm_list_accounts] 获取完成，共 5000 条，合计 10 页
   [adm_list_accounts] 警告：达到 50 页安全上限，停止获取（已获取 25000 条）
   ```
4. **实现方式**：在循环内使用小型辅助函数或内联 `print(..., file=sys.stderr)`。保持日志行简洁，避免在极小结果集上刷屏。
5. **未来工具**：新增全量获取/分页循环时，遵循相同模式；若场景引入新需求，更新本规则。

### 本地工作台（临时文件）

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

### 最小发布命令

**自动化用的一键发布指令：**

> 将 `master` 分支按最小化原则一键发布至远程仓库，同步更新 `README.md`、`CHANGELOG.md`、`pyproject.toml` 版本号及相关解释性文件，在确认无敏感信息、仅包含项目必需内容且其他用户端可正常使用后，创建并推送语义化标签以触发 GitHub Actions 自动构建并发布到 PyPI。

把本地 `develop` 合并到 `master` 并推送到远程仓库，视为一次发布。具体执行步骤见 [分支工作流](#分支工作流) 中的发布清单。

## 重要提示

- UMU API 为逆向工程所得，端点可能发生变化。代码库依赖真实的 HAR 分析。
- `courses.py` endpoint 是一个框架抽象；实际的 UMU API 调用内联在 MCP 工具函数中。
- `teacher.py` 包含 Windows 专用的 UTF-8 修复，必须保留在文件最顶部、所有 import 之前。
- 测试使用 `pytest-asyncio`，并通过 `conftest.py` 自动注入 marker。
