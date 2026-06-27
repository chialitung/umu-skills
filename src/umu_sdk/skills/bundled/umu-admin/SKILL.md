---
name: umu-admin
description: |
  当用户输入 /umua 或 /umuadmin 斜杠命令时触发。
  固定以 Admin（管理员）角色执行 UMU 平台操作。
  覆盖账号管理、组织架构（部门/分组/班级）、学习记录、学习项目、企业课程等全部管理员场景。
---

# /umua — UMU 管理员操作助手

本 skill 指导 Claude 以管理员身份通过 MCP 工具操作 UMU（优幕）学习平台。

## 触发条件

以下情况必须调用本 skill：

1. 用户输入 `/umua` 或 `/umuadmin`。
2. 用户明确请求以管理员身份在 UMU 平台上执行操作。

## 前置条件

本 skill 依赖以下 MCP server 已在 Claude Code 中配置：

- `umu-admin` — 管理员角色工具

如果该 server 不可用，提示用户运行：

```bash
python -m umu_sdk.skills.install --check
```

根据检查结果，必要时重新安装：

```bash
python -m umu_sdk.skills.install --upgrade
```

然后重启 Claude Code。

## 账号配置

使用 UMU 管理员功能前，需要配置管理员登录账号。

账号信息会加密保存在本地：

- 凭证文件位置：`~/.umu_skills/credentials.enc`
- Skill 文件位置：`~/.claude/skills/umu-admin/`
- 加密方式：Fernet 对称加密
- 密钥保护：操作系统 keyring（Windows DPAPI / macOS Keychain / Linux Secret Service）

**安全约束（必须遵守）**：
- **只能通过 `credential_manager.set_role_credentials('admin', username, password)` 保存账号到加密的 `credentials.enc`。**
- **绝不能把账号密码写入 `.env` 文件、文本文件或任何其他明文位置。**
- **不要在对话中重复展示用户的明文密码。**

### 首次配置流程

当用户触发 `/umua` 但缺少管理员账号时：

1. 告诉用户需要配置管理员账号，且这些信息将加密保存。
2. 逐个询问账号和密码：
   - "请提供管理员账号（用户名/邮箱/手机号）"
   - "请提供管理员密码"
3. 调用 `umu_sdk.skills.credential_manager.set_role_credentials('admin', username, password)` 保存。
4. 提示用户**保存后必须重启 Claude Code**，MCP server 才能读取新凭证并开始执行 UMU 操作。

### 随时新增/修改账号

用户可以随时通过 `/umua` 以对话方式管理账号：

- "修改管理员账号"
- "更新管理员密码"
- "删除 admin 的账号信息"

处理流程：

1. 识别要新增/修改/删除的角色。
2. 询问新的用户名和密码（删除除外）。
3. 调用 `set_role_credentials('admin', username, password)` 保存，或调用 `delete_role_credentials('admin')` 删除。
4. 提示用户重启 Claude Code。

> **注意**：保存或删除账号后，必须重启 Claude Code 才能让 MCP server 重新读取凭证。

## 执行流程

每次收到 `/umua` 请求时：

1. **固定使用 admin 角色**：无论用户说什么，都把请求交给 `umu-admin` MCP server 处理。
2. **检查账号凭证**：调用 `credential_manager.has_role_credentials('admin')` 检查；缺少则进入账号配置流程。
3. **选择具体 MCP 工具**：根据需求选择最匹配的管理员工具（`adm_` 开头）。
4. **收集必需参数**：如果用户已提供则直接使用；缺失则一次只问最必要的信息。
5. **调用工具并处理结果**：按标准 JSON 信封处理 success/error/next_action。

## 错误处理

- **认证失败**：提示用户检查管理员账号信息，必要时引导重新录入。
- **缺少权限**：说明该操作需要管理员角色。
- **资源不存在**：询问用户是否要先创建资源。
- **分页数据**：如果工具支持 `fetch_all` 或分页，按需求决定返回第一页还是全部获取。

## 安全与注意事项

- 不要在对话中展示用户的明文密码。
- 批量操作前向用户确认影响的记录数。
- 删除、禁用等破坏性操作需要用户明确确认。
- 如果用户请求模糊，先复述你的理解，得到确认后再执行。

## 参考文件

- `~/.claude/skills/umu/references/tools.md` — 完整 MCP 工具列表，按角色分类。
