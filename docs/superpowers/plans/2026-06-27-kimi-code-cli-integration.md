# Kimi Code CLI 集成实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 umu-skills 项目增加 Kimi Code CLI 的一键安装与调用支持，使用户可通过 `/umu`、`/umu-teacher`、`/umu-student`、`/umu-admin` 调用 UMU 平台能力。

**Architecture:** 参考现有 Claude Code 安装器，新增 `src/umu_sdk/skills/kimi/install.py` 与 4 个 Kimi 格式 `SKILL.md` 资源；安装时向 `~/.kimi-code/mcp.json` 注册 3 个 stdio MCP server，并将 skill 复制到 `~/.kimi-code/skills/`；同步更新 `pyproject.toml`、`README.md`、`AGENTS.md` 与测试。

**Tech Stack:** Python 3.10+, FastMCP, httpx, pytest, pathlib

## Global Constraints

- 目标 Python 版本：>= 3.10
- 行长度：100（Ruff 默认）
- 类型检查：mypy strict（但项目禁用部分误差码）
- 不得修改 Claude Code 与 WorkBuddy 现有安装器行为
- 凭证目录继续复用 `~/.umu_skills`
- Skill 与 MCP 配置默认写入 `~/.kimi-code/`（可被 `KIMI_CODE_HOME` 覆盖）
- MCP 配置中环境变量使用解析后的实际值，不使用 `${VAR:-default}` 语法

---

## Task 1: 创建 Kimi 安装器包结构

**Files:**
- Create: `src/umu_sdk/skills/kimi/__init__.py`
- Create: `src/umu_sdk/skills/kimi/bundled/__init__.py`
- Create directories: `src/umu_sdk/skills/kimi/bundled/umu/`, `umu-teacher/`, `umu-student/`, `umu-admin/`

**Interfaces:**
- Consumes: none
- Produces: package markers so `importlib.resources.files("umu_sdk.skills.kimi.bundled")` works

- [ ] **Step 1: 创建包标记文件**

```python
# src/umu_sdk/skills/kimi/__init__.py
# umu-skills: unofficial UMU platform automation helpers
"""Kimi Code CLI 安装与集成模块."""
```

```python
# src/umu_sdk/skills/kimi/bundled/__init__.py
# umu-skills: unofficial UMU platform automation helpers
"""Kimi Code CLI bundled skill 资源包."""
```

- [ ] **Step 2: 验证包可导入**

Run:
```bash
python -c "from importlib import resources; print(resources.files('umu_sdk.skills.kimi')); print(resources.files('umu_sdk.skills.kimi.bundled'))"
```

Expected: 输出两个 PosixPath/WindowsPath，分别指向 `src/umu_sdk/skills/kimi` 与 `src/umu_sdk/skills/kimi/bundled`。

- [ ] **Step 3: Commit**

```bash
git add src/umu_sdk/skills/kimi/__init__.py src/umu_sdk/skills/kimi/bundled/__init__.py
git commit -m "chore(kimi): add installer package structure"
```

---

## Task 2: 创建主 Skill 文件 `umu/SKILL.md`

**Files:**
- Create: `src/umu_sdk/skills/kimi/bundled/umu/SKILL.md`

**Interfaces:**
- Consumes: none
- Produces: `src/umu_sdk/skills/kimi/bundled/umu/SKILL.md`，Kimi Code CLI 安装后复制到 `~/.kimi-code/skills/umu/SKILL.md`

- [ ] **Step 1: 写入 SKILL.md**

```markdown
---
name: umu
description: |
  <!-- BEGIN_DESCRIPTION -->
  当用户输入 /umu 斜杠命令时触发。
  自动识别意图属于 Teacher（讲师）、Student（学员）还是 Admin（管理员）角色，
  并调用对应的 umu-teacher、umu-student、umu-admin MCP server 工具完成任务。
  覆盖课程创建、资源管理、小节编辑、学员报名、学习进度查询、考试/问卷/签到、
  账号管理、组织架构查询等全部 UMU 平台操作场景。
  <!-- END_DESCRIPTION -->
---

# /umu — UMU 平台操作助手

本 skill 指导 Kimi Code CLI 通过 MCP 工具操作 UMU（优幕）学习平台。

## 触发条件

<!-- BEGIN_TRIGGER -->
以下情况必须调用本 skill：

1. 用户输入 `/umu` 或 `/skill:umu`。

2. 用户明确请求与 UMU 平台相关的操作且包含 `/umu` 命令。
<!-- END_TRIGGER -->

## 前置条件

本 skill 依赖以下 MCP server 已在 Kimi Code CLI 中配置：

- `umu-teacher` — 讲师角色工具
- `umu-student` — 学员角色工具
- `umu-admin` — 管理员角色工具

如果这些 server 不可用，提示用户运行：

```bash
python -m umu_sdk.skills.kimi.install --check
```

根据检查结果，必要时重新安装：

```bash
python -m umu_sdk.skills.kimi.install --upgrade
```

然后重启 Kimi Code CLI。

## 账号配置

使用 UMU 平台功能前，需要配置至少一个角色的登录账号：

- **Teacher（讲师）**：创建课程、上传资源、管理小节
- **Student（学员）**：报名课程、学习、查看进度
- **Admin（管理员）**：账号管理、组织架构、数据查询

账号信息会加密保存在本地：

- 凭证文件位置：`~/.umu_skills/credentials.enc`
- Skill 文件位置：`~/.kimi-code/skills/umu/`（Kimi Code CLI 默认 skill 目录）
- 加密方式：Fernet 对称加密
- 密钥保护：操作系统 keyring（Windows DPAPI / macOS Keychain / Linux Secret Service）

**安全约束（必须遵守）**：
- **只能通过 `credential_manager.set_role_credentials()` 保存账号到加密的 `credentials.enc`。**
- **绝不能把账号密码写入 `.env` 文件、文本文件或任何其他明文位置。**
- **不要在对话中重复展示用户的明文密码。**

### 首次配置流程

当用户触发 `/umu` 但所需角色缺少账号时：

1. 告诉用户当前缺少的角色，以及这些信息将加密保存。
2. 逐个询问账号和密码：
   - "请提供讲师账号（用户名/邮箱/手机号）"
   - "请提供讲师密码"
   - 对 student、admin 重复同样的问题
3. 调用 `umu_sdk.skills.credential_manager.set_role_credentials(role, username, password)` 保存。
4. 提示用户**保存后必须重启 Kimi Code CLI**，MCP server 才能读取新凭证并开始执行 UMU 操作。

示例对话：

> 用户：/umu 帮我创建一个课程
> Kimi：要使用 UMU 平台功能，还需要配置讲师账号和密码。账号信息将加密保存在本地。请提供讲师登录用户名。
> 用户：teacher@example.com
> Kimi：请提供讲师登录密码。
> 用户：mypassword
> Kimi：讲师账号已加密保存。请重启 Kimi Code CLI，之后就可以开始执行 UMU 操作了。

### 随时新增/修改账号

用户可以随时通过 `/umu` 以对话方式管理账号：

- "添加管理员账号"
- "修改我的讲师账号"
- "更新管理员密码"
- "把学员账号改成 xxx"
- "删除 student 的账号信息"

处理流程：

1. 识别要新增/修改/删除的角色。
2. 询问新的用户名和密码（删除除外）。
3. 调用 `set_role_credentials(role, username, password)` 保存，或调用 `delete_role_credentials(role)` 删除。
4. 提示用户重启 Kimi Code CLI。

> **注意**：保存或删除账号后，必须重启 Kimi Code CLI 才能让 MCP server 重新读取凭证。

## Skill 设置

用户可以通过 `/umu` 对话方式管理 skill 本身的行为，无需手动编辑配置文件。

### 语义触发开关

`semantic_trigger_enabled` 控制 Kimi 是否能在日常对话中通过语义识别自动调用本 skill：

- **关闭（默认）**：只有用户输入 `/umu` 时才触发。
- **开启**：用户明确表达需要在 UMU 在线学习平台上完成具体操作时自动触发。

**注意**：开启后不会仅因“课程”“学员”“考试”等通用教育词汇就触发，必须基于“在 UMU 平台上完成具体操作”的完整意图。

当用户表达以下意图时，直接调用安装脚本切换开关，**不需要询问账号密码**：

- "/umu 打开语义触发"
- "/umu 启用语义触发"
- "/umu 开启语义识别"
- "/umu 关闭语义触发"
- "/umu 禁用语义触发"
- "/umu semantic trigger on/off"（英文也可识别）

处理流程：

1. 判断用户要开启还是关闭。
2. 运行对应命令：
   - 开启：`python -m umu_sdk.skills.kimi.install --semantic-trigger`
   - 关闭：`python -m umu_sdk.skills.kimi.install --no-semantic-trigger`
3. 读取命令输出或 `~/.kimi-code/skills/umu/config.json` 确认状态。
4. 向用户汇报当前状态，并**提醒必须重启 Kimi Code CLI** 才能生效。

示例对话：

> 用户：/umu 打开语义触发
> Kimi：正在启用语义自动触发...
> Kimi：语义自动触发已开启。请重启 Kimi Code CLI，之后表达需要在 UMU 平台上完成具体操作时我会自动调用 /umu skill。

> 用户：/umu 关闭语义触发
> Kimi：正在禁用语义自动触发...
> Kimi：语义自动触发已关闭。之后只有输入 `/umu` 时我才会调用 skill。

### 平台别名管理

用户可以通过 `/umu` 对话为 UMU 平台添加自定义别名。别名仅在语义触发开启时生效。

可识别的用户意图示例：

- "/umu 添加别名 敏学社"
- "/umu 我使用的学习平台叫敏学社"
- "/umu 我的平台叫优幕学堂"
- "/umu 删除别名 敏学社"
- "/umu 列出所有别名"

处理流程：

1. 判断用户要添加、删除还是列出别名；添加时去除"我使用的学习平台叫""我的平台叫"等前缀，提取真正的别名。
2. 运行对应命令：
   - 添加：`python -m umu_sdk.skills.kimi.install alias add <别名>`
   - 删除：`python -m umu_sdk.skills.kimi.install alias remove <别名>`
   - 列出：`python -m umu_sdk.skills.kimi.install alias list`
3. 读取命令输出确认结果。
4. 向用户汇报当前别名列表，并**提醒必须重启 Kimi Code CLI** 才能生效。

示例对话：

> 用户：/umu 我使用的学习平台叫敏学社
> Kimi：正在添加别名"敏学社"...
> Kimi：别名"敏学社"已添加。当前别名列表：敏学社。
> Kimi：请重启 Kimi Code CLI，之后提到"敏学社"且表达平台操作意图时，我会自动调用 /umu skill。

**约束**：

- 别名数量上限 10 个。
- 单个别名长度上限 50 个字符。
- 别名只能包含中文、英文、数字、空格、连字符、下划线和点号。

## 执行流程

每次收到 UMU 相关请求时，按以下步骤执行：

### 1. 识别用户意图与角色

根据用户自然语言描述，判断涉及的角色：

| 角色 | 典型请求 |
|------|---------|
| Teacher | 创建课程、上传资源、添加/修改小节、设置课程信息 |
| Student | 报名课程、学习、完成小节（视频、文章、问卷、考试、签到）、学习进度跟踪 |
| Admin | 创建/禁用/启用账号、查询账号、管理部门/群组/班级、**查询学习记录** |

如果请求涉及多个角色（例如"批量创建学员账号并报名某课程"），按正确顺序调用：通常 Admin → Student。

### 2. 检查账号凭证

在调用工具前，确认所需角色已配置账号：

- 调用 `umu_sdk.skills.credential_manager.has_role_credentials(role)` 检查。
- 如果缺少凭证，进入"账号配置"章节的首次配置流程，询问用户账号密码并保存。

不要在没有凭证的情况下盲目调用需要登录的工具。

### 3. 选择具体 MCP 工具

根据需求选择最匹配的工具。参考 `references/tools.md` 中的完整工具列表和命名规律：

- `tch_` 开头 → Teacher MCP
- `stu_` 开头 → Student MCP
- `adm_` 开头 → Admin MCP

优先使用专门化的工具而不是通用工具。例如：
- 创建 SCORM 小节 → `tch_create_scorm_section`
- 创建视频小节 → `tch_create_video_section`
- 批量创建账号 → `adm_create_account` 或 `stu_batch_import_accounts`

### 4. 收集必需参数

不要假设参数值。检查所选工具的必需参数：

- 如果用户已经提供，直接使用。
- 如果缺失，通过对话向用户询问，一次只问最必要的信息。
- 如果可以从上一步结果推导（如先创建课程得到 `group_id`，再用于添加小节），则自动传递，不需要再问用户。

常见需要询问的参数：
- 课程标题、访问码、URL
- 资源文件本地路径
- 账号姓名/手机号/邮箱
- 小节标题、内容、题目 JSON

### 5. 调用工具并处理结果

按顺序调用工具。每个工具返回标准 JSON 信封：

```json
{
  "success": true,
  "data": {},
  "error_code": "",
  "error_message": "",
  "suggested_action": "",
  "next_action": "proceed"
}
```

处理规则：
- `success = true`：继续下一步或向用户汇报结果。
- `success = false`：读取 `error_message` 和 `suggested_action`，向用户解释原因并给出建议。
- `next_action = "needs_user_input"`：向用户询问缺失信息。
- `next_action = "needs_enrollment"`：提示用户先报名课程。
- `next_action = "retry"`：在修正参数后重试。

### 6. 多步骤工作流编排

对于复杂任务，自动拆解为多个 MCP 调用。例如：

**"创建一个带 SCORM 小节的课程"**
1. 询问课程标题和 SCORM 资源 ID（或先上传 SCORM）。
2. 调用 `tch_create_course` 创建空课程。
3. 从返回中提取 `group_id`。
4. 调用 `tch_create_scorm_section` 绑定 SCORM 资源。
5. 汇报结果。

**"批量创建学员并报名某课程"**
1. 询问学员列表（姓名/手机/邮箱）和课程标识。
2. 对每个学员调用 `adm_create_account`。
3. 对创建成功的学员调用 `stu_enroll_course`。
4. 汇总创建与报名结果。

**"查看我在某课程的学习进度"**
1. 询问课程标识（访问码/短域名/URL）。
2. 调用 `stu_get_course_structure` 获取课程结构与完成状态。
3. 如有需要，调用 `stu_get_learning_progress` 获取更详细进度。
4. 以清晰格式汇报。

**"查询某学员最近的学习记录"**
1. 询问学员姓名/邮箱/手机号/用户名，或从上下文获取。
2. 调用 `adm_list_learning_records(student_keywords=..., fetch_all=True)`。
3. 工具内部自动解析学员关键词并返回学习明细。
4. 按课程名称、完成率、学习时长汇总汇报。

**"查询某班级在某时间段内的学习完成情况"**
1. 如不确定班级名称或 ID，调用 `adm_list_classes` 获取班级列表。
2. 调用 `adm_list_learning_records(class_names=..., start_day=..., end_day=..., fetch_all=True)`。
3. 工具内部自动解析班级名称并返回学习明细。
4. 汇总统计完成率、学习时长等指标。

**"查询某部门在某时间段内的学习完成情况"**
1. 如不确定部门 ID，调用 `adm_list_departments` 获取部门列表。
2. 调用 `adm_list_learning_records(department_ids=..., start_day=..., end_day=..., fetch_all=True)`。
3. 汇总统计完成率、学习时长等指标。

**"查询某课程最近被哪些学员学习过"**
1. 询问课程名称关键词。
2. 调用 `adm_list_learning_records(course_title=..., fetch_all=True)`。
3. 按学员、部门、完成状态汇总汇报。

## 参数收集原则

- **一次只问一件事**：避免一次性抛出所有参数让用户填写。
- **提供默认值或示例**：当参数有常见取值时，给出建议。
- **文件路径要绝对路径**：如果工具要求本地文件路径，确保用户提供的是绝对路径。
- **JSON 内容要先生成再调用**：如 `questions_json`、`signin_info_json` 等，先在对话中向用户确认内容，再传入工具。

## 错误处理

- **认证失败**：提示用户检查已配置的账号信息，必要时引导重新录入或修改账号。
- **缺少权限**：说明该操作需要哪个角色，询问是否切换角色。
- **资源不存在**：询问用户是否要先创建资源（如先上传 SCORM 再创建小节）。
- **分页数据**：如果工具支持 `fetch_all` 或分页，按需求决定是返回第一页还是全部获取。

## 安全与注意事项

- 不要在对话中展示用户的明文密码。
- 批量操作前向用户确认影响的记录数。
- 删除、禁用等破坏性操作需要用户明确确认。
- 如果用户请求模糊，先复述你的理解，得到确认后再执行。

## 参考文件

- `references/tools.md` — 完整 MCP 工具列表，按角色分类。
```

- [ ] **Step 2: 验证 SKILL.md 可被解析**

Run:
```bash
python -c "
import re, yaml
from pathlib import Path
p = Path('src/umu_sdk/skills/kimi/bundled/umu/SKILL.md')
text = p.read_text(encoding='utf-8')
assert text.startswith('---')
_, fm, _ = text.split('---', 2)
data = yaml.safe_load(fm)
assert data['name'] == 'umu'
assert 'description' in data
print('OK')
"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/umu_sdk/skills/kimi/bundled/umu/SKILL.md
git commit -m "feat(kimi): add main umu skill for Kimi Code CLI"
```

---

## Task 3: 创建固定角色 Skill 文件

**Files:**
- Create: `src/umu_sdk/skills/kimi/bundled/umu-teacher/SKILL.md`
- Create: `src/umu_sdk/skills/kimi/bundled/umu-student/SKILL.md`
- Create: `src/umu_sdk/skills/kimi/bundled/umu-admin/SKILL.md`

**Interfaces:**
- Consumes: none
- Produces: 三个固定角色 skill 文件

- [ ] **Step 1: 写入 umu-teacher/SKILL.md**

```markdown
---
name: umu-teacher
description: |
  当用户输入 /umu-teacher 或 /umut 斜杠命令时触发。
  固定以 Teacher（讲师）角色执行 UMU 平台操作。
  覆盖课程创建、资源管理、小节编辑、课程设置等全部讲师场景。
---

# /umu-teacher — UMU 讲师操作助手

本 skill 指导 Kimi Code CLI 以讲师身份通过 MCP 工具操作 UMU（优幕）学习平台。

## 触发条件

以下情况必须调用本 skill：

1. 用户输入 `/umu-teacher` 或 `/umut`。
2. 用户明确请求以讲师身份在 UMU 平台上执行操作。

## 前置条件

本 skill 依赖以下 MCP server 已在 Kimi Code CLI 中配置：

- `umu-teacher` — 讲师角色工具

如果该 server 不可用，提示用户运行：

```bash
python -m umu_sdk.skills.kimi.install --check
```

根据检查结果，必要时重新安装：

```bash
python -m umu_sdk.skills.kimi.install --upgrade
```

然后重启 Kimi Code CLI。

## 账号配置

使用 UMU 讲师功能前，需要配置讲师登录账号。

账号信息会加密保存在本地：

- 凭证文件位置：`~/.umu_skills/credentials.enc`
- Skill 文件位置：`~/.kimi-code/skills/umu-teacher/`
- 加密方式：Fernet 对称加密
- 密钥保护：操作系统 keyring（Windows DPAPI / macOS Keychain / Linux Secret Service）

**安全约束（必须遵守）**：
- **只能通过 `credential_manager.set_role_credentials('teacher', username, password)` 保存账号到加密的 `credentials.enc`。**
- **绝不能把账号密码写入 `.env` 文件、文本文件或任何其他明文位置。**
- **不要在对话中重复展示用户的明文密码。**

### 首次配置流程

当用户触发 `/umu-teacher` 但缺少讲师账号时：

1. 告诉用户需要配置讲师账号，且这些信息将加密保存。
2. 逐个询问账号和密码：
   - "请提供讲师账号（用户名/邮箱/手机号）"
   - "请提供讲师密码"
3. 调用 `umu_sdk.skills.credential_manager.set_role_credentials('teacher', username, password)` 保存。
4. 提示用户**保存后必须重启 Kimi Code CLI**，MCP server 才能读取新凭证并开始执行 UMU 操作。

### 随时新增/修改账号

用户可以随时通过 `/umu-teacher` 以对话方式管理账号：

- "修改讲师账号"
- "更新讲师密码"
- "删除 teacher 的账号信息"

处理流程：

1. 识别要新增/修改/删除的角色。
2. 询问新的用户名和密码（删除除外）。
3. 调用 `set_role_credentials('teacher', username, password)` 保存，或调用 `delete_role_credentials('teacher')` 删除。
4. 提示用户重启 Kimi Code CLI。

> **注意**：保存或删除账号后，必须重启 Kimi Code CLI 才能让 MCP server 重新读取凭证。

## 执行流程

每次收到 `/umu-teacher` 请求时：

1. **固定使用 teacher 角色**：无论用户说什么，都把请求交给 `umu-teacher` MCP server 处理。
2. **检查账号凭证**：调用 `credential_manager.has_role_credentials('teacher')` 检查；缺少则进入账号配置流程。
3. **选择具体 MCP 工具**：根据需求选择最匹配的讲师工具（`tch_` 开头）。
4. **收集必需参数**：如果用户已提供则直接使用；缺失则一次只问最必要的信息。
5. **调用工具并处理结果**：按标准 JSON 信封处理 success/error/next_action。

## 错误处理

- **认证失败**：提示用户检查讲师账号信息，必要时引导重新录入。
- **缺少权限**：说明该操作需要讲师角色。
- **资源不存在**：询问用户是否要先上传资源（如先上传 SCORM 再创建小节）。
- **分页数据**：如果工具支持 `fetch_all` 或分页，按需求决定返回第一页还是全部获取。

## 安全与注意事项

- 不要在对话中展示用户的明文密码。
- 批量操作前向用户确认影响的记录数。
- 删除课程、小节等破坏性操作需要用户明确确认。
- 如果用户请求模糊，先复述你的理解，得到确认后再执行。

## 参考文件

- `~/.kimi-code/skills/umu/references/tools.md` — 完整 MCP 工具列表，按角色分类。
```

- [ ] **Step 2: 写入 umu-student/SKILL.md**

复制 `src/umu_sdk/skills/kimi/bundled/umu-teacher/SKILL.md` 到 `src/umu_sdk/skills/kimi/bundled/umu-student/SKILL.md`，并做以下全局替换：

| From | To |
|------|-----|
| `umu-teacher` | `umu-student` |
| `/umu-teacher` | `/umu-student` |
| `/umut` | `/umus` |
| `Teacher（讲师）` | `Student（学员）` |
| `讲师` | `学员` |
| `'teacher'` | `'student'` |
| `tch_` | `stu_` |
| 讲师场景描述 | 学员场景：报名课程、浏览课程、完成小节、学习进度跟踪 |
| 资源不存在处理 | 需要报名：如果 `next_action` 为 `needs_enrollment`，提示用户先报名课程 |

- [ ] **Step 3: 写入 umu-admin/SKILL.md**

复制 `src/umu_sdk/skills/kimi/bundled/umu-teacher/SKILL.md` 到 `src/umu_sdk/skills/kimi/bundled/umu-admin/SKILL.md`，并做以下全局替换：

| From | To |
|------|-----|
| `umu-teacher` | `umu-admin` |
| `/umu-teacher` | `/umu-admin` |
| `/umut` | `/umua` |
| `Teacher（讲师）` | `Admin（管理员）` |
| `讲师` | `管理员` |
| `'teacher'` | `'admin'` |
| `tch_` | `adm_` |
| 讲师场景描述 | 管理员场景：账号管理、组织架构、学习记录、学习项目、企业课程 |

- [ ] **Step 4: 验证三个文件都存在且 frontmatter 合法**

Run:
```bash
python -c "
import yaml
from pathlib import Path
for name in ('umu-teacher', 'umu-student', 'umu-admin'):
    p = Path(f'src/umu_sdk/skills/kimi/bundled/{name}/SKILL.md')
    text = p.read_text(encoding='utf-8')
    _, fm, _ = text.split('---', 2)
    data = yaml.safe_load(fm)
    assert data['name'] == name
print('OK')
"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add src/umu_sdk/skills/kimi/bundled/umu-teacher/SKILL.md src/umu_sdk/skills/kimi/bundled/umu-student/SKILL.md src/umu_sdk/skills/kimi/bundled/umu-admin/SKILL.md
git commit -m "feat(kimi): add role-specific skills for Kimi Code CLI"
```

---

## Task 4: 实现 Kimi 安装器核心函数

**Files:**
- Create: `src/umu_sdk/skills/kimi/install.py`

**Interfaces:**
- Consumes: none
- Produces: `install.py` with helpers for path detection, mcp.json I/O, skill copy, credential init

- [ ] **Step 1: 写入安装器主体（不含 alias 子命令和 main）**

```python
# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU Skills Kimi Code CLI 自动化安装模块.

用法：
    python -m umu_sdk.skills.kimi.install           # 安装/更新 Skill 与 MCP 配置
    python -m umu_sdk.skills.kimi.install --check   # 仅检查安装状态
    python -m umu_sdk.skills.kimi.install --upgrade # 强制升级 PyPI 包

功能：
1. 安装/升级 umu-skills PyPI 包（如果尚未安装）
2. 把 skill 文件复制到用户的 Kimi Code CLI 全局 skills 目录
3. 创建/更新 ~/.kimi-code/mcp.json 中的 MCP server 配置
4. 初始化加密的凭证文件目录（默认 ~/.umu_skills）

Skill 文件目录：
    Windows: C:\\Users\\<用户名>\\.kimi-code\\skills\\umu
    macOS/Linux: ~/.kimi-code/skills/umu

加密凭证目录：
    Windows: C:\\Users\\<用户名>\\.umu_skills
    macOS/Linux: ~/.umu_skills
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.resources as resources
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterator

# Windows 中文输出修复（必须在任何打印之前）
if sys.platform == "win32":
    try:
        import io

        if isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _get_kimi_code_home() -> Path:
    """返回 Kimi Code CLI 主目录."""
    env_home = os.getenv("KIMI_CODE_HOME")
    if env_home:
        return Path(env_home).expanduser()
    return Path.home() / ".kimi-code"


def _get_global_skills_root() -> Path:
    """返回 Kimi Code CLI 全局 skill 安装根目录."""
    return _get_kimi_code_home() / "skills"


def _get_global_skill_dir(skill_name: str = "umu") -> Path:
    """返回指定 skill 的全局安装目录."""
    return _get_global_skills_root() / skill_name


def _get_credential_dir() -> Path:
    """返回通用加密凭证目录（跨 AI 工具共享）."""
    return Path.home() / ".umu_skills"


def _get_old_credential_dir() -> Path:
    """返回旧版 Claude Code 专用凭证目录（用于兼容提示）."""
    return Path.home() / ".claude" / "skills" / "umu"


def _get_project_skills_root() -> Path:
    """返回项目中的 skill 源根目录（开发模式优先使用）."""
    return Path(__file__).resolve().parents[3] / ".kimi-code" / "skills"


@contextlib.contextmanager
def _get_bundled_skills_root() -> Iterator[Path]:
    """返回包内自带的 skill 源根目录上下文.

    当脚本从 PyPI 安装的包中运行时，项目目录下的 `.kimi-code/skills`
    不存在，此时从 wheel 内嵌的 bundled 资源中提取。
    """
    ref = resources.files("umu_sdk.skills.kimi.bundled")
    with resources.as_file(ref) as path:
        yield path


def _ensure_package_installed(upgrade: bool = False) -> None:
    """确保 umu-skills 包已安装."""
    is_installed = False
    try:
        import umu_sdk  # noqa: F401

        is_installed = True
        if not upgrade:
            print("umu-skills 已安装")
            return
    except ImportError:
        pass

    action = "升级" if is_installed else "安装"
    print(f"正在{action} umu-skills...")
    cmd = [sys.executable, "-m", "pip", "install"]
    if upgrade:
        cmd.append("--upgrade")
    cmd.append("umu-skills[mcp]")
    subprocess.run(cmd, check=True)
    print(f"umu-skills {action}完成")


def _copy_skill(source: Path, target: Path) -> None:
    """复制 skill 文件到全局目录."""
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)


def _get_mcp_servers_path() -> Path:
    """返回 Kimi Code CLI mcp.json 文件路径."""
    return _get_kimi_code_home() / "mcp.json"


def _load_mcp_servers() -> dict:
    """读取 mcp.json，不存在或损坏则返回空结构."""
    path = _get_mcp_servers_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"mcpServers": {}}


def _save_mcp_servers(settings: dict) -> None:
    """保存 mcp.json."""
    path = _get_mcp_servers_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"已更新: {path}")


def _configure_mcp_servers(settings: dict) -> dict:
    """在 mcp.json 中添加/更新 umu MCP server 配置."""
    mcp_servers = settings.setdefault("mcpServers", {})

    # Kimi mcp.json 不支持 ${VAR:-default} 语法，直接写入解析后的值
    base_url = os.getenv("UMU_BASE_URL", "https://www.umu.cn")
    log_level = os.getenv("MCP_LOG_LEVEL", "INFO")
    base_env = {
        "UMU_BASE_URL": base_url,
        "MCP_LOG_LEVEL": log_level,
        "UMU_SKILL_DIR": str(_get_credential_dir()),
    }

    python_cmd = sys.executable
    mcp_servers["umu-teacher"] = {
        "command": python_cmd,
        "args": ["-m", "umu_sdk.adapters.mcp.teacher"],
        "env": {**base_env},
    }
    mcp_servers["umu-student"] = {
        "command": python_cmd,
        "args": ["-m", "umu_sdk.adapters.mcp.student"],
        "env": {**base_env},
    }
    mcp_servers["umu-admin"] = {
        "command": python_cmd,
        "args": ["-m", "umu_sdk.adapters.mcp.admin"],
        "env": {**base_env},
    }

    return settings


def _init_credentials(creds_dir: Path) -> None:
    """初始化凭证文件目录，但不写入任何明文信息."""
    creds_dir.mkdir(parents=True, exist_ok=True)
    creds_path = creds_dir / "credentials.enc"
    if not creds_path.exists():
        print("凭证目录已准备就绪，首次使用 /umu 时会引导你录入账号")


def _get_skill_config_path(skill_dir: Path) -> Path:
    """返回 skill 配置 JSON 文件路径."""
    return skill_dir / "config.json"


def _load_skill_config(skill_dir: Path) -> dict:
    """读取 skill 目录下的 config.json，不存在或损坏则返回空字典."""
    config_path = _get_skill_config_path(skill_dir)
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_skill_config(skill_dir: Path, config: dict) -> None:
    """保存配置到 skill 目录下的 config.json."""
    config_path = _get_skill_config_path(skill_dir)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_aliases(skill_dir: Path) -> list[str]:
    """从 config.json 读取 aliases 列表，损坏或缺失返回空列表."""
    config = _load_skill_config(skill_dir)
    aliases = config.get("aliases", [])
    if not isinstance(aliases, list):
        return []
    return [str(a).strip() for a in aliases if str(a).strip()]


def _save_aliases(skill_dir: Path, aliases: list[str]) -> None:
    """保存别名列表到 config.json，保留其他字段."""
    config = _load_skill_config(skill_dir)
    config["aliases"] = aliases
    _save_skill_config(skill_dir, config)
```

- [ ] **Step 2: 写失败测试**

在 `tests/test_kimi_install.py` 中创建：

```python
"""Tests for skills.kimi.install."""

from __future__ import annotations

import sys
from pathlib import Path

from umu_sdk.skills.kimi.install import (
    _configure_mcp_servers,
    _copy_skill,
    _get_credential_dir,
    _get_global_skill_dir,
    _get_kimi_code_home,
    _get_mcp_servers_path,
    _load_mcp_servers,
    _save_mcp_servers,
)


class TestConfigureMcpServers:
    def test_adds_three_servers(self) -> None:
        settings: dict = {}
        result = _configure_mcp_servers(settings)

        assert "mcpServers" in result
        servers = result["mcpServers"]
        assert set(servers.keys()) == {"umu-teacher", "umu-student", "umu-admin"}
        assert servers["umu-teacher"]["command"] == sys.executable
        assert servers["umu-student"]["command"] == sys.executable
        assert servers["umu-admin"]["command"] == sys.executable
        assert servers["umu-teacher"]["args"] == ["-m", "umu_sdk.adapters.mcp.teacher"]
        assert servers["umu-student"]["args"] == ["-m", "umu_sdk.adapters.mcp.student"]
        assert servers["umu-admin"]["args"] == ["-m", "umu_sdk.adapters.mcp.admin"]

    def test_env_uses_resolved_values(self) -> None:
        result = _configure_mcp_servers({})
        env = result["mcpServers"]["umu-teacher"]["env"]

        assert env["UMU_BASE_URL"] == "https://www.umu.cn"
        assert env["MCP_LOG_LEVEL"] == "INFO"
        assert env["UMU_SKILL_DIR"] == str(_get_credential_dir())

    def test_preserves_existing_servers(self) -> None:
        settings = {"mcpServers": {"existing": {"command": "existing", "args": []}}}
        result = _configure_mcp_servers(settings)

        assert "existing" in result["mcpServers"]
        assert "umu-teacher" in result["mcpServers"]

    def test_overwrites_existing_umu_servers(self) -> None:
        settings = {
            "mcpServers": {
                "umu-teacher": {"command": "old", "args": ["-m", "old.module"]}
            }
        }
        result = _configure_mcp_servers(settings)

        assert result["mcpServers"]["umu-teacher"]["command"] == sys.executable


class TestInstallPaths:
    def test_kimi_code_home_default(self) -> None:
        home = _get_kimi_code_home()
        assert home.name == ".kimi-code"
        assert home.parent == Path.home()

    def test_global_skill_dir(self) -> None:
        skill_dir = _get_global_skill_dir()
        assert skill_dir.name == "umu"
        assert skill_dir.parent.name == "skills"
        assert skill_dir.parent.parent.name == ".kimi-code"

    def test_mcp_servers_path(self) -> None:
        path = _get_mcp_servers_path()
        assert path.name == "mcp.json"
        assert path.parent.name == ".kimi-code"

    def test_credential_dir_is_generic(self) -> None:
        creds_dir = _get_credential_dir()
        assert creds_dir.name == ".umu_skills"
        assert creds_dir.parent == Path.home()


class TestMcpServersPersistence:
    def test_load_missing_file_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path", lambda: tmp_path / "mcp.json"
        )
        result = _load_mcp_servers()
        assert result == {"mcpServers": {}}

    def test_load_corrupt_file_returns_empty(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "mcp.json"
        path.write_text("not json", encoding="utf-8")
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path", lambda: path
        )
        result = _load_mcp_servers()
        assert result == {"mcpServers": {}}

    def test_save_creates_parent_dirs(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path",
            lambda: tmp_path / "nested" / "mcp.json",
        )
        settings = {"mcpServers": {"umu-teacher": {"command": "python"}}}
        _save_mcp_servers(settings)

        path = tmp_path / "nested" / "mcp.json"
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["mcpServers"]["umu-teacher"]["command"] == "python"


class TestCopySkill:
    def test_copies_skill_files(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        (source / "SKILL.md").write_text("test skill", encoding="utf-8")
        subdir = source / "references"
        subdir.mkdir()
        (subdir / "tools.md").write_text("tools", encoding="utf-8")

        _copy_skill(source, target)

        assert (target / "SKILL.md").exists()
        assert (target / "references" / "tools.md").exists()
        assert (target / "SKILL.md").read_text(encoding="utf-8") == "test skill"

    def test_replaces_existing_target(self, tmp_path: Path) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()
        (source / "SKILL.md").write_text("new", encoding="utf-8")
        (target / "SKILL.md").write_text("old", encoding="utf-8")
        (target / "stale.txt").write_text("stale", encoding="utf-8")

        _copy_skill(source, target)

        assert (target / "SKILL.md").read_text(encoding="utf-8") == "new"
        assert not (target / "stale.txt").exists()
```

注意：需要在文件顶部添加 `import json`。

- [ ] **Step 3: 运行测试，确认失败**

Run:
```bash
pytest tests/test_kimi_install.py -v
```

Expected: 多个 `FAILED` 或 `ModuleNotFoundError`/`ImportError`，因为 `src/umu_sdk/skills/kimi/install.py` 尚未导入 `argparse`/`main` 等不影响的核心函数已存在，但测试文件已引用它们。具体失败点可能是 `_get_mcp_servers_path` 等尚未完全工作；确认失败信息包含函数/模块未找到或断言失败。

- [ ] **Step 4: 运行测试，确认通过**

Run:
```bash
pytest tests/test_kimi_install.py::TestConfigureMcpServers tests/test_kimi_install.py::TestInstallPaths tests/test_kimi_install.py::TestMcpServersPersistence tests/test_kimi_install.py::TestCopySkill -v
```

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add src/umu_sdk/skills/kimi/install.py tests/test_kimi_install.py
git commit -m "feat(kimi): add core installer helpers and tests"
```

---

## Task 5: 实现安装/检查/别名 CLI

**Files:**
- Modify: `src/umu_sdk/skills/kimi/install.py`

**Interfaces:**
- Consumes: helpers from Task 4
- Produces: `_perform_install`, `install`, `_check_installation`, alias management, `main`

- [ ] **Step 1: 追加安装/检查/别名函数到 install.py**

在 Task 4 写入的 `install.py` 末尾追加：

```python
# SKILL.md 模板中两套 description / 触发条件片段
_DESCRIPTION_EXPLICIT = """\
当用户输入 /umu 斜杠命令时触发。
自动识别意图属于 Teacher（讲师）、Student（学员）还是 Admin（管理员）角色，
并调用对应的 umu-teacher、umu-student、umu-admin MCP server 工具完成任务。
覆盖课程创建、资源管理、小节编辑、学员报名、学习进度查询、考试/问卷/签到、
账号管理、组织架构查询等全部 UMU 平台操作场景。
"""

_DESCRIPTION_SEMANTIC = """\
当用户输入 /umu 斜杠命令，或明确表达需要在 UMU 在线学习平台上完成具体操作时触发。
本 skill 用于操作 UMU 平台：课程创建、资源管理、小节编辑、学员报名、学习进度查询、考试/问卷/签到、账号管理、组织架构查询等。
只有在用户请求执行 UMU 平台能够完成的具体操作时，才调用本 skill。
不要仅因为用户提到通用教育词汇（如“课程”“学员”“考试”“签到”“部门”）就触发。
<!-- ALIASES_PLACEHOLDER -->
"""

_TRIGGER_EXPLICIT = """\
以下情况必须调用本 skill：

1. 用户输入 `/umu` 或 `/skill:umu`。

2. 用户明确请求与 UMU 平台相关的操作且包含 `/umu` 命令。
"""

_TRIGGER_SEMANTIC = """\
以下情况必须调用本 skill：

1. 用户输入 `/umu` 或 `/skill:umu`。
2. 用户明确请求在 UMU 平台上完成具体操作，例如：
   - "帮我在 UMU 上创建一个课程"
   - "把 SCORM 课件上传到 UMU"
   - "查询 UMU 上某学员的学习进度"
   - "在 UMU 里批量创建学员账号"
   - "帮我报名 UMU 课程 aet504"
   - "导出 UMU 平台的学习记录"
   - "禁用 UMU 上的某个账号"
3. 用户提到 `UMU` 且上下文表明需要操作 UMU 平台。
<!-- ALIASES_TRIGGER_PLACEHOLDER -->

以下情况不要调用本 skill：
- 用户只是讨论通用教育概念，如 "学员是什么意思"、"考试怎么准备"。
- 用户提到 "课程"、"学习"、"签到" 等词汇但没有 UMU 平台上下文。
- 用户请求设计课程大纲、制定学习计划等 UMU 平台无法直接完成的操作。
"""

MAX_ALIAS_LENGTH = 50
MAX_ALIASES = 10
_ALIAS_PATTERN = re.compile(r"^[\w\s一-鿿\-_.]+$")


def add_alias(skill_dir: Path, alias: str) -> tuple[bool, str]:
    """添加一个平台别名."""
    alias = alias.strip()
    if not alias:
        return False, "别名不能为空"
    if len(alias) > MAX_ALIAS_LENGTH:
        return False, f"别名长度不能超过 {MAX_ALIAS_LENGTH} 个字符"
    if not _ALIAS_PATTERN.match(alias):
        return False, "别名只能包含中文、英文、数字、空格、连字符、下划线和点号"

    existing = _load_aliases(skill_dir)
    if len(existing) >= MAX_ALIASES:
        return False, f"别名数量已达上限（最多 {MAX_ALIASES} 个）"
    if alias in existing:
        return False, f"别名 '{alias}' 已存在"

    existing.append(alias)
    _save_aliases(skill_dir, existing)
    return True, f"别名 '{alias}' 已添加"


def remove_alias(skill_dir: Path, alias: str) -> tuple[bool, str]:
    """删除一个平台别名."""
    alias = alias.strip()
    existing = _load_aliases(skill_dir)
    if alias not in existing:
        return False, f"别名 '{alias}' 不存在"
    existing.remove(alias)
    _save_aliases(skill_dir, existing)
    return True, f"别名 '{alias}' 已删除"


def list_aliases(skill_dir: Path) -> list[str]:
    """返回当前所有别名."""
    return _load_aliases(skill_dir)


def _render_skill_md(
    skill_dir: Path,
    semantic_trigger_enabled: bool,
    aliases: list[str] | None = None,
) -> None:
    """根据 semantic_trigger 开关和别名列表渲染 SKILL.md 文件."""
    if aliases is None:
        aliases = _load_aliases(skill_dir)

    skill_md_path = skill_dir / "SKILL.md"
    content = skill_md_path.read_text(encoding="utf-8")

    description = _DESCRIPTION_SEMANTIC if semantic_trigger_enabled else _DESCRIPTION_EXPLICIT
    trigger = _TRIGGER_SEMANTIC if semantic_trigger_enabled else _TRIGGER_EXPLICIT

    if semantic_trigger_enabled and aliases:
        alias_desc_text = (
            f"此外，用户也可以使用以下别名指代 UMU 平台：{', '.join(aliases)}。"
        )
        alias_trigger_text = (
            "4. 用户使用以下别名指代 UMU 平台且上下文表明需要操作平台："
            + "、".join(f"`{a}`" for a in aliases)
            + "。"
        )
    else:
        alias_desc_text = ""
        alias_trigger_text = ""

    description = description.replace(
        "<!-- ALIASES_PLACEHOLDER -->\n",
        alias_desc_text + "\n" if alias_desc_text else "",
    )
    trigger = trigger.replace(
        "<!-- ALIASES_TRIGGER_PLACEHOLDER -->\n",
        alias_trigger_text + "\n" if alias_trigger_text else "",
    )

    content = re.sub(
        r"description:\s*\|.*?<!-- END_DESCRIPTION -->",
        "description: |\n  <!-- BEGIN_DESCRIPTION -->\n  "
        + description.replace("\n", "\n  ")
        + "\n  <!-- END_DESCRIPTION -->",
        content,
        count=1,
        flags=re.DOTALL,
    )

    content = re.sub(
        r"<!-- BEGIN_TRIGGER -->.*?<!-- END_TRIGGER -->",
        f"<!-- BEGIN_TRIGGER -->\n{trigger}\n<!-- END_TRIGGER -->",
        content,
        count=1,
        flags=re.DOTALL,
    )

    skill_md_path.write_text(content, encoding="utf-8")


def _perform_install(source_root: Path, semantic_trigger: bool | None = None) -> list[str]:
    """执行 skill 复制、mcp.json 更新、凭证目录初始化和配置管理."""
    target_root = _get_global_skills_root()

    umu_target = _get_global_skill_dir("umu")
    existing_config = _load_skill_config(umu_target) if umu_target.exists() else {}

    final_semantic_trigger = semantic_trigger
    if final_semantic_trigger is None:
        final_semantic_trigger = existing_config.get("semantic_trigger_enabled", False)

    existing_aliases = existing_config.get("aliases", [])
    if not isinstance(existing_aliases, list):
        existing_aliases = []

    umu_config_to_save: dict = {
        "semantic_trigger_enabled": final_semantic_trigger,
        "aliases": existing_aliases,
    }
    for key, value in existing_config.items():
        if key not in umu_config_to_save:
            umu_config_to_save[key] = value

    installed_skills: list[str] = []
    for skill_dir in sorted(source_root.iterdir()):
        if not skill_dir.is_dir():
            continue
        if not (skill_dir / "SKILL.md").exists():
            continue
        skill_name = skill_dir.name
        target = target_root / skill_name
        _copy_skill(skill_dir, target)
        installed_skills.append(skill_name)

        if skill_name == "umu":
            _save_skill_config(target, umu_config_to_save)
            _render_skill_md(
                target,
                semantic_trigger_enabled=final_semantic_trigger,
                aliases=existing_aliases,
            )
        else:
            _save_skill_config(target, {})

    settings = _load_mcp_servers()
    settings = _configure_mcp_servers(settings)
    _save_mcp_servers(settings)

    _init_credentials(_get_credential_dir())
    return installed_skills


def _has_skill_sources(source_root: Path) -> bool:
    """判断目录下是否包含有效的 skill 源."""
    if not source_root.exists():
        return False
    return any(
        item.is_dir() and (item / "SKILL.md").exists()
        for item in source_root.iterdir()
    )


def install(upgrade: bool = False, semantic_trigger: bool | None = None) -> None:
    """执行完整安装流程."""
    print("=== UMU Skills Kimi Code CLI 安装程序 ===\n")

    _ensure_package_installed(upgrade=upgrade)

    project_source = _get_project_skills_root()
    if _has_skill_sources(project_source):
        print(f"使用项目 skill 源: {project_source}\n")
        installed = _perform_install(project_source, semantic_trigger=semantic_trigger)
    else:
        print("使用包内自带的 skill 文件\n")
        with _get_bundled_skills_root() as source:
            installed = _perform_install(source, semantic_trigger=semantic_trigger)

    print("\n=== 安装完成 ===")
    print(f"已安装 Skill: {', '.join(installed)}")
    print(f"Skill 根目录: {_get_global_skills_root()}")
    print(f"加密凭证目录: {_get_credential_dir()}")
    print(f"MCP 配置: {_get_mcp_servers_path()}")
    print("\n下一步：重启 Kimi Code CLI，然后输入 /umu /umua /umut /umus 触发对应 skill")


def _check_installation() -> int:
    """检查当前安装状态并报告."""
    print("=== UMU Skills Kimi Code CLI 安装状态检查 ===\n")

    ok = True

    try:
        import umu_sdk

        print(f"✓ umu-skills 包已安装 ({umu_sdk.__file__})")
    except ImportError:
        print("✗ umu-skills 包未安装")
        ok = False

    required_skills = ["umu", "umu-admin", "umu-teacher", "umu-student"]
    for skill_name in required_skills:
        skill_dir = _get_global_skill_dir(skill_name)
        if skill_dir.exists() and (skill_dir / "SKILL.md").exists():
            print(f"✓ Skill 目录存在: {skill_dir}")
        else:
            print(f"✗ Skill 目录缺失: {skill_dir}")
            ok = False

    mcp_path = _get_mcp_servers_path()
    if mcp_path.exists():
        try:
            settings = json.loads(mcp_path.read_text(encoding="utf-8"))
            servers = settings.get("mcpServers", {})
            required = {"umu-teacher", "umu-student", "umu-admin"}
            missing = required - set(servers.keys())
            if missing:
                print(f"✗ mcp.json 缺少 MCP server: {missing}")
                ok = False
            else:
                print("✓ mcp.json 已配置三个 MCP server")
                for name in required:
                    server = servers[name]
                    cmd = server.get("command", "")
                    args = server.get("args", [])
                    if args == ["-m", f"umu_sdk.adapters.mcp.{name.split('-')[1]}"]:
                        print(f"  ✓ {name} 使用 python -m 启动")
                    elif cmd.startswith("umu-skills-"):
                        print(f"  ⚠ {name} 仍使用 console script（可能受 PATH 影响）")
                    else:
                        print(f"  ? {name} 命令未知: {cmd} {args}")
        except Exception as e:
            print(f"✗ 读取 mcp.json 失败: {e}")
            ok = False
    else:
        print(f"✗ mcp.json 不存在: {mcp_path}")
        ok = False

    creds_dir = _get_credential_dir()
    creds_path = creds_dir / "credentials.enc"
    old_creds_path = _get_old_credential_dir() / "credentials.enc"
    if creds_path.exists():
        print(f"✓ 已存在加密凭证文件: {creds_path}")
    elif old_creds_path.exists():
        print(f"○ 发现旧路径加密凭证文件: {old_creds_path}")
        print("  首次保存 /umu 账号时会自动迁移到新版路径")
    else:
        print(f"○ 尚未保存加密凭证，首次 /umu 会引导录入（{creds_dir}）")

    umu_skill_dir = _get_global_skill_dir("umu")
    skill_config = _load_skill_config(umu_skill_dir)
    semantic_enabled = skill_config.get("semantic_trigger_enabled", False)
    status = "已开启" if semantic_enabled else "已关闭"
    print(f"○ 语义自动触发: {status}")

    aliases = list_aliases(umu_skill_dir)
    if aliases:
        print(f"○ 已配置别名: {', '.join(aliases)}")
    else:
        print("○ 暂无别名配置")

    print()
    if ok:
        print("状态正常，重启 Kimi Code CLI 后即可使用 /umu /umua /umut /umus")
        return 0
    print("状态异常，请运行: python -m umu_sdk.skills.kimi.install")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="安装 UMU Skills 到 Kimi Code CLI")
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help="强制升级 umu-skills 包",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="仅检查安装状态，不执行安装",
    )
    parser.add_argument(
        "--semantic-trigger",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="安装时是否启用语义自动触发（默认保留已有配置，首次安装为关闭）",
    )

    subparsers = parser.add_subparsers(dest="command", help="附加命令")
    alias_parser = subparsers.add_parser("alias", help="管理 UMU 平台别名")
    alias_sub = alias_parser.add_subparsers(dest="alias_action", required=True)

    add_parser = alias_sub.add_parser("add", help="添加别名")
    add_parser.add_argument("name", help="别名")

    remove_parser = alias_sub.add_parser("remove", help="删除别名")
    remove_parser.add_argument("name", help="别名")

    alias_sub.add_parser("list", help="列出别名")

    args = parser.parse_args()

    try:
        if args.command == "alias":
            skill_dir = _get_global_skill_dir()
            if args.alias_action == "add":
                success, msg = add_alias(skill_dir, args.name)
            elif args.alias_action == "remove":
                success, msg = remove_alias(skill_dir, args.name)
            else:
                aliases = list_aliases(skill_dir)
                print("当前别名：" + ("、".join(aliases) if aliases else "无"))
                return 0

            print(msg)
            if success and args.alias_action in ("add", "remove"):
                config = _load_skill_config(skill_dir)
                semantic_enabled = config.get("semantic_trigger_enabled", False)
                _render_skill_md(skill_dir, semantic_trigger_enabled=semantic_enabled)
            return 0 if success else 1

        if args.check:
            return _check_installation()
        install(upgrade=args.upgrade, semantic_trigger=args.semantic_trigger)
        return 0
    except subprocess.CalledProcessError as e:
        print(f"安装失败: {e}")
        return 1
    except Exception as e:
        print(f"安装失败: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 追加 CLI 测试到 tests/test_kimi_install.py**

在 `tests/test_kimi_install.py` 末尾追加：

```python
import subprocess
from unittest import mock

from umu_sdk.skills.kimi.install import (
    _check_installation,
    _perform_install,
    add_alias,
    list_aliases,
    remove_alias,
)


class TestAliasManagement:
    def test_add_alias_success(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        success, msg = add_alias(skill_dir, "敏学社")

        assert success is True
        assert "敏学社" in msg
        assert list_aliases(skill_dir) == ["敏学社"]

    def test_add_alias_duplicate(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        add_alias(skill_dir, "敏学社")

        success, msg = add_alias(skill_dir, "敏学社")

        assert success is False
        assert "已存在" in msg

    def test_add_alias_empty(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        assert add_alias(skill_dir, "")[0] is False
        assert add_alias(skill_dir, "   ")[0] is False

    def test_add_alias_too_long(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        long_alias = "a" * 51

        success, msg = add_alias(skill_dir, long_alias)

        assert success is False
        assert "长度不能超过" in msg

    def test_add_alias_invalid_characters(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        success, msg = add_alias(skill_dir, "敏学社!")

        assert success is False
        assert "只能包含" in msg

    def test_remove_alias_success(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        add_alias(skill_dir, "敏学社")

        success, msg = remove_alias(skill_dir, "敏学社")

        assert success is True
        assert list_aliases(skill_dir) == []

    def test_remove_alias_not_found(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "umu"
        success, msg = remove_alias(skill_dir, "不存在")

        assert success is False
        assert "不存在" in msg


class TestPerformInstall:
    def test_installs_all_skills(self, tmp_path: Path, monkeypatch) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        for name in ("umu", "umu-teacher", "umu-student", "umu-admin"):
            (source / name).mkdir()
            (source / name / "SKILL.md").write_text(
                "---\nname: " + name + "\ndescription: test\n---\n", encoding="utf-8"
            )

        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_global_skills_root", lambda: target
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path",
            lambda: tmp_path / "mcp.json",
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_credential_dir", lambda: tmp_path / ".umu_skills"
        )

        installed = _perform_install(source)

        assert set(installed) == {"umu", "umu-admin", "umu-teacher", "umu-student"}
        assert (target / "umu" / "SKILL.md").exists()
        assert (target / "umu-teacher" / "SKILL.md").exists()
        assert (tmp_path / "mcp.json").exists()

    def test_preserves_semantic_config_on_reinstall(self, tmp_path: Path, monkeypatch) -> None:
        source = tmp_path / "source"
        target = tmp_path / "target"
        source.mkdir()
        target.mkdir()

        (source / "umu").mkdir()
        (source / "umu" / "SKILL.md").write_text(
            "---\nname: umu\ndescription: |\n  <!-- BEGIN_DESCRIPTION -->\n  desc\n  <!-- END_DESCRIPTION -->\n---\n"
            "<!-- BEGIN_TRIGGER -->\ntrigger\n<!-- END_TRIGGER -->",
            encoding="utf-8",
        )

        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_global_skills_root", lambda: target
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_mcp_servers_path",
            lambda: tmp_path / "mcp.json",
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_credential_dir", lambda: tmp_path / ".umu_skills"
        )

        # 先安装一次并开启语义触发
        _perform_install(source, semantic_trigger=True)
        config = json.loads((target / "umu" / "config.json").read_text(encoding="utf-8"))
        assert config["semantic_trigger_enabled"] is True

        # 再次安装不传参数，应保留开启状态
        _perform_install(source)
        config = json.loads((target / "umu" / "config.json").read_text(encoding="utf-8"))
        assert config["semantic_trigger_enabled"] is True


class TestCheckInstallation:
    def test_check_reports_missing(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_kimi_code_home", lambda: tmp_path
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_credential_dir", lambda: tmp_path / ".umu_skills"
        )

        code = _check_installation()
        captured = capsys.readouterr()

        assert code == 1
        assert "mcp.json 不存在" in captured.out

    def test_check_reports_ok(self, tmp_path: Path, monkeypatch, capsys) -> None:
        home = tmp_path
        skills = home / "skills"
        for name in ("umu", "umu-teacher", "umu-student", "umu-admin"):
            (skills / name).mkdir(parents=True)
            (skills / name / "SKILL.md").write_text("---\nname: " + name + "\n---\n", encoding="utf-8")

        mcp = {"mcpServers": {}}
        mcp_path = home / "mcp.json"
        mcp_path.write_text(json.dumps(mcp), encoding="utf-8")

        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_kimi_code_home", lambda: home
        )
        monkeypatch.setattr(
            "umu_sdk.skills.kimi.install._get_credential_dir", lambda: tmp_path / ".umu_skills"
        )

        code = _check_installation()
        captured = capsys.readouterr()

        assert code == 0
        assert "状态正常" in captured.out
```

- [ ] **Step 3: 运行完整 Kimi 安装器测试**

Run:
```bash
pytest tests/test_kimi_install.py -v
```

Expected: 全部通过（约 20+ tests）。

- [ ] **Step 4: Commit**

```bash
git add src/umu_sdk/skills/kimi/install.py tests/test_kimi_install.py
git commit -m "feat(kimi): add install/check/alias CLI and tests"
```

---

## Task 6: 更新 pyproject.toml

**Files:**
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `src/umu_sdk/skills/kimi/install.py:main`
- Produces: new console script and wheel include

- [ ] **Step 1: 添加 console script**

在 `[project.scripts]` 区域 `umu-skills-install-workbuddy` 之后插入：

```toml
umu-skills-install-kimi = "umu_sdk.skills.kimi.install:main"
```

- [ ] **Step 2: 添加 wheel include**

在 `[tool.hatch.build.targets.wheel]` 的 `include` 列表末尾添加：

```toml
"src/umu_sdk/skills/kimi/bundled/**/*",
```

- [ ] **Step 3: 验证 pyproject.toml 语法**

Run:
```bash
python -c "import tomllib; tomllib.load(open('pyproject.toml', 'rb'))"
```

Expected: 无输出（成功退出）。

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore(kimi): register installer console script and wheel includes"
```

---

## Task 7: 更新 README.md

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: design spec section 4.2
- Produces: documented Kimi Code CLI install/use section

- [ ] **Step 1: 在 Claude Code 章节之后插入 Kimi Code CLI 章节**

在 README.md 中找到 WorkBuddy 章节的开头，在其前面插入：

```markdown
## 在 Kimi Code CLI 中使用

Kimi Code CLI 安装后，可以通过 `/umu` 斜杠命令操作 UMU 平台。

### 安装

```bash
python -m umu_sdk.skills.kimi.install
```

或使用 PyPI 安装后的命令：

```bash
umu-skills-install-kimi
```

安装脚本会：

1. 检查并安装 `umu-skills[mcp]` 包。
2. 将 4 个 skill 复制到 `~/.kimi-code/skills/`。
3. 在 `~/.kimi-code/mcp.json` 注册 `umu-teacher`、`umu-student`、`umu-admin` 三个 MCP server。
4. 初始化加密凭证目录 `~/.umu_skills/`。

### 启用语义自动触发（可选）

默认只有输入 `/umu` 时才触发。如果你想让 Kimi 在识别到 UMU 操作意图时自动调用，可运行：

```bash
python -m umu_sdk.skills.kimi.install --semantic-trigger
```

关闭：

```bash
python -m umu_sdk.skills.kimi.install --no-semantic-trigger
```

### 重启 Kimi Code CLI

安装完成后**必须重启 Kimi Code CLI**，新 skill 和 MCP server 才会生效。

### 配置账号

首次触发 `/umu` 时，会引导你录入至少一个角色的账号和密码。账号信息加密保存在 `~/.umu_skills/credentials.enc`。

### 使用示例

```
/umu 帮我创建一个课程
/umu-teacher 上传 SCORM 课件
/umu-student 帮我报名课程 aet504
/umu-admin 查询最近的学习记录
```

### 故障排查

运行检查命令：

```bash
python -m umu_sdk.skills.kimi.install --check
```

如需强制更新：

```bash
python -m umu_sdk.skills.kimi.install --upgrade
```
```

- [ ] **Step 2: 更新总览中的客户端列表**

在 README.md 开头找到提及 "Claude Code / WorkBuddy / Python SDK" 的位置，更新为 "Claude Code / WorkBuddy / Kimi Code CLI / Python SDK"。

- [ ] **Step 3: 验证 README 渲染**

Run:
```bash
python -c "from pathlib import Path; text = Path('README.md').read_text(encoding='utf-8'); assert '在 Kimi Code CLI 中使用' in text; assert 'umu-skills-install-kimi' in text; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): add Kimi Code CLI install and usage section"
```

---

## Task 8: 更新 AGENTS.md

**Files:**
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: design spec section 4.3
- Produces: updated usage forms and install commands

- [ ] **Step 1: 更新三种使用形态**

找到 AGENTS.md 中 "三种使用形态" 表格/列表，更新为：

```markdown
### 三种使用形态

1. **Claude Code / Claude Desktop**：通过 `/umu` 斜杠命令对话式使用。
2. **腾讯 WorkBuddy**：通过 `umu-skills` orchestrator 接入。
3. **Kimi Code CLI**：通过 `/umu` 斜杠命令对话式使用。
4. **Python SDK**：直接 `from umu_sdk import UMUClient` 调用。
```

- [ ] **Step 2: 在快速参考或一键安装命令列表中添加 Kimi**

在 AGENTS.md 的 "一键安装" 区域（如第 11 节）添加：

```markdown
```bash
# 安装到 Kimi Code CLI
python -m umu_sdk.skills.kimi.install
python -m umu_sdk.skills.kimi.install --check
python -m umu_sdk.skills.kimi.install --upgrade
```
```

- [ ] **Step 3: 验证 AGENTS.md 包含 Kimi**

Run:
```bash
python -c "from pathlib import Path; text = Path('AGENTS.md').read_text(encoding='utf-8'); assert 'Kimi Code CLI' in text; assert 'umu_sdk.skills.kimi.install' in text; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add AGENTS.md
git commit -m "docs(agents): include Kimi Code CLI in usage and install guide"
```

---

## Task 9: 全量质量门

**Files:**
- All modified files

**Interfaces:**
- Consumes: all previous tasks
- Produces: passing quality gates

- [ ] **Step 1: 运行相关测试**

Run:
```bash
pytest tests/test_kimi_install.py -v
```

Expected: 全部通过。

- [ ] **Step 2: 运行 Ruff lint**

Run:
```bash
ruff check src/umu_sdk/skills/kimi/ tests/test_kimi_install.py
```

Expected: 无错误。

- [ ] **Step 3: 运行 mypy（可选，项目已有禁用码）**

Run:
```bash
mypy src/umu_sdk/skills/kimi/install.py
```

Expected: 无新增错误。

- [ ] **Step 4: 验证打包包含 bundled 资源**

Run:
```bash
python -m build
```

Expected: 构建成功。然后检查 wheel：

```bash
python -c "
import zipfile, sys
whl = [p for p in __import__('pathlib').Path('dist').glob('umu_skills-*.whl')][0]
with zipfile.ZipFile(whl) as z:
    files = [n for n in z.namelist() if 'kimi/bundled' in n]
    assert any('umu/SKILL.md' in n for n in files), 'missing umu skill'
    print('OK', len(files), 'kimi bundled files')
"
```

Expected: `OK 4 kimi bundled files`（或更多，取决于 references 等）。

- [ ] **Step 5: Commit（如质量门全部通过）**

```bash
git add -f dist/*
git reset HEAD dist/*
# 不提交 dist，仅确认构建成功
```

---

## Self-Review

- [ ] **Spec coverage:**
  - 新增 Kimi 安装模块 ✅ Task 4-5
  - 4 个 Skill 文件 ✅ Task 2-3
  - `~/.kimi-code/mcp.json` 注册 3 个 server ✅ Task 4-5
  - 用户可通过 `/umu` 等调用 ✅ Task 2-3 SKILL.md 内容
  - 更新 pyproject.toml ✅ Task 6
  - 更新 README.md ✅ Task 7
  - 更新 AGENTS.md ✅ Task 8
  - 新增测试 ✅ Task 4-5

- [ ] **Placeholder scan:** 计划中无 "TBD"、"TODO"、"稍后实现"、"适当处理" 等模糊表述；所有代码块均为可执行内容。

- [ ] **Type consistency:**
  - `_configure_mcp_servers(settings: dict) -> dict` 在 Task 4 与 Task 5 中一致。
  - `_get_global_skill_dir(skill_name: str = "umu") -> Path` 在 Task 4 与测试、alias 中一致。
  - `install(upgrade: bool = False, semantic_trigger: bool | None = None) -> None` 在 Task 5 与 main 中一致。

- [ ] **No cross-task "similar to" references:** 每个任务均给出完整代码或明确的全局替换表。
