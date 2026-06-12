---
name: umu
description: |
  当用户输入 /umu 斜杠命令，或请求操作 UMU（优幕）在线学习平台时触发。
  自动识别意图属于 Teacher（讲师）、Student（学员）还是 Admin（管理员）角色，
  并调用对应的 umu-teacher、umu-student、umu-admin MCP server 工具完成任务。
  覆盖课程创建、资源管理、小节编辑、学员报名、学习进度查询、考试/问卷/签到、
  账号管理、组织架构查询等全部 UMU 平台操作场景。
  如果用户提到 UMU、优幕、课程、学员、讲师、账号、报名、进度、考试、问卷、签到、
  SCORM、部门、群组等关键词，即使他们没有说 "/umu"，也要使用本 skill。
---

# /umu — UMU 平台操作助手

本 skill 指导 Claude 通过 MCP 工具操作 UMU（优幕）学习平台。

## 触发条件

以下情况必须调用本 skill：

1. 用户输入 `/umu`。
2. 用户请求与 UMU 平台相关的任何操作，例如：
   - "帮我创建一个课程"
   - "获取平台用户清单"
   - "学员报名课程"
   - "查看学习进度"
   - "批量创建账号"
   - "上传 SCORM 课件"
   - "禁用某个账号"
   - "查询部门列表"
3. 用户提到 UMU、优幕、课程、学员、讲师、账号、报名、进度、考试、问卷、签到、SCORM、部门、群组等关键词。

## 前置条件

本 skill 依赖以下 MCP server 已在 Claude Code 中配置：

- `umu-teacher` — 讲师角色工具
- `umu-student` — 学员角色工具
- `umu-admin` — 管理员角色工具

如果这些 server 不可用，提示用户运行：

```bash
python -m umu_sdk.skills.install --check
```

根据检查结果，必要时重新安装：

```bash
python -m umu_sdk.skills.install --upgrade
```

然后重启 Claude Code。

## 账号配置

使用 UMU 平台功能前，需要配置至少一个角色的登录账号：

- **Teacher（讲师）**：创建课程、上传资源、管理小节
- **Student（学员）**：报名课程、学习、查看进度
- **Admin（管理员）**：账号管理、组织架构、数据查询

账号信息会加密保存在本地：

- 文件位置：`~/.claude/skills/umu/credentials.enc`
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
4. 提示用户**保存后必须重启 Claude Code**，MCP server 才能读取新凭证并开始执行 UMU 操作。

示例对话：

> 用户：/umu 帮我创建一个课程
> Claude：要使用 UMU 平台功能，还需要配置讲师账号和密码。账号信息将加密保存在本地。请提供讲师登录用户名。
> 用户：teacher@example.com
> Claude：请提供讲师登录密码。
> 用户：mypassword
> Claude：讲师账号已加密保存。请重启 Claude Code，之后就可以开始执行 UMU 操作了。

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
4. 提示用户重启 Claude Code。

> **注意**：保存或删除账号后，必须重启 Claude Code 才能让 MCP server 重新读取凭证。

## 执行流程

每次收到 UMU 相关请求时，按以下步骤执行：

### 1. 识别用户意图与角色

根据用户自然语言描述，判断涉及的角色：

| 角色 | 典型请求 |
|------|---------|
| Teacher | 创建课程、上传资源、添加/修改小节、设置课程信息 |
| Student | 报名课程、学习、完成小节、查看进度、考试/问卷/签到 |
| Admin | 创建/禁用/启用账号、查询账号、管理部门/群组 |

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
  "data": {...},
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
