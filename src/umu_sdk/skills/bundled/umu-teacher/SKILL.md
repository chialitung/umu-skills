---
name: umu-teacher
description: |
  当用户输入 /umut 或 /umuteacher 斜杠命令时触发。
  固定以 Teacher（讲师）角色执行 UMU 平台操作。
  覆盖课程创建、资源管理、小节编辑、课程设置等全部讲师场景。
---

# /umut — UMU 讲师操作助手

本 skill 指导 Claude 以讲师身份通过 MCP 工具操作 UMU（优幕）学习平台。

## 触发条件

以下情况必须调用本 skill：

1. 用户输入 `/umut` 或 `/umuteacher`。
2. 用户明确请求以讲师身份在 UMU 平台上执行操作。

## 前置条件

本 skill 依赖以下 MCP server 已在 Claude Code 中配置：

- `umu-teacher` — 讲师角色工具

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

使用 UMU 讲师功能前，需要配置讲师登录账号。

账号信息会加密保存在本地：

- 凭证文件位置：`~/.umu_skills/credentials.enc`
- Skill 文件位置：`~/.claude/skills/umu-teacher/`
- 加密方式：Fernet 对称加密
- 密钥保护：操作系统 keyring（Windows DPAPI / macOS Keychain / Linux Secret Service）

**安全约束（必须遵守）**：
- **只能通过 `credential_manager.set_role_credentials('teacher', username, password)` 保存账号到加密的 `credentials.enc`。**
- **绝不能把账号密码写入 `.env` 文件、文本文件或任何其他明文位置。**
- **不要在对话中重复展示用户的明文密码。**

### 首次配置流程

当用户触发 `/umut` 但缺少讲师账号时：

1. 告诉用户需要配置讲师账号，且这些信息将加密保存。
2. 逐个询问账号和密码：
   - "请提供讲师账号（用户名/邮箱/手机号）"
   - "请提供讲师密码"
3. 调用 `umu_sdk.skills.credential_manager.set_role_credentials('teacher', username, password)` 保存。
4. 提示用户**保存后必须重启 Claude Code**，MCP server 才能读取新凭证并开始执行 UMU 操作。

### 随时新增/修改账号

用户可以随时通过 `/umut` 以对话方式管理账号：

- "修改讲师账号"
- "更新讲师密码"
- "删除 teacher 的账号信息"

处理流程：

1. 识别要新增/修改/删除的角色。
2. 询问新的用户名和密码（删除除外）。
3. 调用 `set_role_credentials('teacher', username, password)` 保存，或调用 `delete_role_credentials('teacher')` 删除。
4. 提示用户重启 Claude Code。

> **注意**：保存或删除账号后，必须重启 Claude Code 才能让 MCP server 重新读取凭证。

## 执行流程

每次收到 `/umut` 请求时：

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

## 批量查询与导出指引

当用户要求查询或导出大量数据时，按以下原则处理，避免手动分页和重复调用：

1. **优先使用批量/全量参数**：列表类工具通常支持 `fetch_all=true`。先尝试使用该参数一次性获取全部数据。
2. **处理大结果集截断**：如果 `fetch_all=true` 返回结果被截断（单行 JSON 过长），改用分页参数（`page` + `page_size`）分多次获取，或用 Python 直接读取工具返回的原始输出文件。
3. **批量查询明细**：对于"查询所有课程的访问权限""查询所有小节的学习时长""查询所有课程的学员名单"等需要对每个对象再调用的任务，不要逐个调用原子工具。优先派遣子代理批量处理，子代理可直接使用 `umu_sdk` 的 `UMUClient` 底层接口高效拉取。
4. **导出 Excel/CSV 优先使用专用导出工具**：用户要求"导出到文件"时，先检查是否存在对应的导出工具，例如：
   - 课程访问权限 → `tch_export_course_permissions`
   - 学习项目访问权限 → `tch_export_program_permissions`
   存在则直接调用，避免子代理重复造轮子。
5. **无专用工具时兜底导出**：如果不存在对应的导出工具，派遣子代理完成：子代理拉取全量数据后，优先使用 `ExportEngine.export_records()` / `tch_export_generic_records` 生成 Excel/CSV，最后才用 pandas/openpyxl 手动实现，保存到用户指定路径（未指定则默认桌面）。
6. **避免父代理做中间解析**：列表 + 逐条查询 + 汇总/导出的任务，不要在父代理层手动分页解析，应直接交给子代理或专用导出工具。

## 安全与注意事项

- 不要在对话中展示用户的明文密码。
- 批量操作前向用户确认影响的记录数。
- 删除课程、小节等破坏性操作需要用户明确确认。
- 如果用户请求模糊，先复述你的理解，得到确认后再执行。
- **设置课程为指定账户/班级/部门/分组可见时，必须显式调用 `tch_set_course_access_permission(group_id, access_permission=3)` 切换权限模式，再调用 `tch_add_course_access_accounts` 添加可见对象。`tch_add_course_access_accounts` 仅维护授权列表，不会自动变更课程的整体访问权限。**

## 参考文件

- `~/.claude/skills/umu/references/tools.md` — 完整 MCP 工具列表，按角色分类。
