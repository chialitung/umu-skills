---
name: umu-student
description: |
  当用户输入 /umu-student 或 /umus 斜杠命令时触发。
  固定以 Student（学员）角色执行 UMU 平台操作。
  覆盖学员场景：报名课程、浏览课程、完成小节、学习进度跟踪。
type: prompt
whenToUse: 当用户需要以学员身份操作 UMU 平台（优幕）进行报名、学习、考试、签到、进度查询时
---

# /umu-student — UMU 学员操作助手

本 skill 指导 Kimi Code CLI 以学员身份通过 MCP 工具操作 UMU（优幕）学习平台。

## 触发条件

以下情况必须调用本 skill：

1. 用户输入 `/umu-student` 或 `/umus`。
2. 用户明确请求以学员身份在 UMU 平台上执行操作。

## 前置条件

本 skill 依赖以下 MCP server 已在 Kimi Code CLI 中配置：

- `umu-student` — 学员角色工具

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

使用 UMU 学员功能前，需要配置学员登录账号。

账号信息会加密保存在本地：

- 凭证文件位置：`~/.umu_skills/credentials.enc`
- Skill 文件位置：`~/.kimi-code/skills/umu-student/`
- 加密方式：Fernet 对称加密
- 密钥保护：操作系统 keyring（Windows DPAPI / macOS Keychain / Linux Secret Service）

**安全约束（必须遵守）**：
- **只能通过 `credential_manager.set_role_credentials('student', username, password)` 保存账号到加密的 `credentials.enc`。**
- **绝不能把账号密码写入 `.env` 文件、文本文件或任何其他明文位置。**
- **不要在对话中重复展示用户的明文密码。**

### 首次配置流程

当用户触发 `/umu-student` 但缺少学员账号时：

1. 告诉用户需要配置学员账号，且这些信息将加密保存。
2. 逐个询问账号和密码：
   - "请提供学员账号（用户名/邮箱/手机号）"
   - "请提供学员密码"
3. 调用 `umu_sdk.skills.credential_manager.set_role_credentials('student', username, password)` 保存。
4. 提示用户**保存后必须重启 Kimi Code CLI**，MCP server 才能读取新凭证并开始执行 UMU 操作。

### 随时新增/修改账号

用户可以随时通过 `/umu-student` 以对话方式管理账号：

- "修改学员账号"
- "更新学员密码"
- "删除 student 的账号信息"

处理流程：

1. 识别要新增/修改/删除的角色。
2. 询问新的用户名和密码（删除除外）。
3. 调用 `set_role_credentials('student', username, password)` 保存，或调用 `delete_role_credentials('student')` 删除。
4. 提示用户重启 Kimi Code CLI。

> **注意**：保存或删除账号后，必须重启 Kimi Code CLI 才能让 MCP server 重新读取凭证。

## 执行流程

每次收到 `/umu-student` 请求时：

1. **固定使用 student 角色**：无论用户说什么，都把请求交给 `umu-student` MCP server 处理。
2. **检查账号凭证**：调用 `credential_manager.has_role_credentials('student')` 检查；缺少则进入账号配置流程。
3. **选择具体 MCP 工具**：根据需求选择最匹配的学员工具（`stu_` 开头）。
4. **收集必需参数**：如果用户已提供则直接使用；缺失则一次只问最必要的信息。
5. **调用工具并处理结果**：按标准 JSON 信封处理 success/error/next_action。

## 错误处理

- **认证失败**：提示用户检查学员账号信息，必要时引导重新录入。
- **缺少权限**：说明该操作需要学员角色。
- **需要报名**：如果 `next_action` 为 `needs_enrollment`，提示用户先报名课程。
- **分页数据**：如果工具支持 `fetch_all` 或分页，按需求决定返回第一页还是全部获取。

## 安全与注意事项

- 不要在对话中展示用户的明文密码。
- 批量操作前向用户确认影响的记录数。
- 删除课程、小节等破坏性操作需要用户明确确认。
- 如果用户请求模糊，先复述你的理解，得到确认后再执行。

## 参考文件

- `~/.kimi-code/skills/umu/references/tools.md` — 完整 MCP 工具列表，按角色分类。
