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
type: prompt
whenToUse: 当用户需要操作 UMU 平台（优幕）进行课程、小节、学员、组织架构等教务管理时
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

## 批量查询与导出指引

当用户要求查询或导出大量数据时，按以下原则处理，避免手动分页和重复调用：

1. **优先使用批量/全量参数**：列表类工具通常支持 `fetch_all=true`。先尝试使用该参数一次性获取全部数据。
2. **处理大结果集截断**：如果 `fetch_all=true` 返回结果被截断（单行 JSON 过长），改用分页参数（`page` + `page_size`）分多次获取，或用 Python 直接读取工具返回的原始输出文件。
3. **批量查询明细**：对于"查询所有课程的访问权限""查询所有学员的学习记录""查询所有账号的部门/分组"等需要对每条记录再调用的任务，不要逐个调用原子工具。优先派遣子代理批量处理，子代理可直接使用 `umu_sdk` 的 `UMUClient` 底层接口高效拉取。
4. **导出 Excel/CSV**：用户要求"导出到文件"时，直接派遣子代理完成：子代理拉取全量数据后，用 Python（pandas/openpyxl）生成 Excel/CSV，保存到用户指定路径（未指定则默认桌面）。
5. **避免父代理做中间解析**：列表 + 逐条查询 + 汇总/导出的任务，不要在父代理层手动分页解析，应直接交给子代理。

## 安全与注意事项

- 不要在对话中展示用户的明文密码。
- 批量操作前向用户确认影响的记录数。
- 删除、禁用等破坏性操作需要用户明确确认。
- 如果用户请求模糊，先复述你的理解，得到确认后再执行。

## 参考文件

- `references/tools.md` — 完整 MCP 工具列表，按角色分类。
