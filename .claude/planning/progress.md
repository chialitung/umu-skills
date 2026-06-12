# UMU Skills 规划会话日志

## 2026-06-12

### 已完成

- 用户提出 Skill 开发与发布两阶段需求
- 创建 `task_plan.md`、`findings.md`、`progress.md` 三个规划文件

### 讨论要点

用户希望实现：

1. **开发阶段**：项目目录内持续开发新功能时，已安装 Skill 自动获得新功能，便于调试。账号信息放在项目 `.env` 中，优先读取。
2. **发布阶段**：GitHub 发布后，其他用户可自动化安装 Skill。缺少账号信息时交互式引导录入，信息存放在 Skills 目录中并加密保护。用户可通过自然语言指令修改账号信息。

### 下一步

引导用户确认以下问题：

1. Skill 自动更新机制偏好：
   - A. 开发时仅使用项目内 `.claude/skills/umu/`（Claude Code 自动加载）
   - B. 使用符号链接把项目 skill 链接到全局 skills 目录
   - C. 提供自动打包/复制脚本，保存时自动同步

2. 用户端凭证加密方案：
   - A. Fernet 对称加密（`cryptography` 库）
   - B. 操作系统 keyring（`keyring` 库）
   - C. 其他方案

3. MCP server 读取加密凭证的方式：
   - A. Skill 启动 MCP server 前解密并写入临时环境变量
   - B. MCP server 自身增加读取加密凭证文件的能力
   - C. 通过 Claude Code settings.json 的 env 字段动态更新

4. 自动化安装脚本入口：
   - A. `python -m umu_sdk.skills.install`
   - B. 独立的 `install.py` / `install.ps1` / `install.sh`
   - C. 发布为 PyPI 包的命令行工具

### 已确认决策

| 问题 | 用户选择 |
|------|---------|
| 开发阶段 Skill 自动更新 | 使用项目内 `.claude/skills/umu/` 目录 |
| 用户端凭证加密 | Fernet 对称加密 |
| MCP server 读取凭证方式 | MCP server 自己读取 skills 目录下的加密文件 |
| 自动化安装入口 | `python -m umu_sdk.skills.install` |

### 新增关键问题

用户选择 "MCP server 自己读取加密文件" 后，需要解决一个核心问题：

> **Fernet 解密密钥如何安全地传递给 MCP server？**

已确认方案：**机器级密钥保护**（Windows DPAPI / macOS Keychain / Linux Secret Service）。

### 技术方案概要

#### 开发阶段

- 开发者在项目根目录使用 `.claude/skills/umu/`
- Claude Code 在项目目录下启动时加载项目级 skill
- 账号信息优先从项目根目录 `.env` 读取
- 修改 SKILL.md 或 references/tools.md 后重启 Claude Code 即可生效

#### 发布阶段

1. **安装**：用户运行 `python -m umu_sdk.skills.install`
   - 安装/升级 `umu-skills` PyPI 包
   - 复制 `.claude/skills/umu/` 到用户 Claude Code 全局 skills 目录
   - 创建/更新 `.claude/settings.json` 中的 MCP server 配置
   - 初始化空的加密凭证文件和机器级保护的 Fernet 密钥

2. **首次使用**：用户触发 `/umu`
   - Skill 检测缺少账号信息
   - 通过对话询问用户讲师/学员/管理员账号密码
   - 用 Fernet 加密后保存到 skills 目录的 `credentials.enc`

3. **日常使用**：MCP server 启动时
   - 从机器级密钥存储获取 Fernet 密钥
   - 读取 `credentials.enc` 并解密
   - 使用解密后的账号自动登录

4. **修改账号**：用户说"修改我的讲师密码"
   - Skill 识别意图，询问新密码
   - 重新加密并保存
   - 提示用户重启 Claude Code 使 MCP server 重新读取

### 待办

- [x] 确认核心决策
- [x] 确认 Fernet 密钥传递方案
- [x] 完成技术方案概要
- [x] 实现 credential_manager 模块
- [x] 实现 install 模块
- [x] 改造 MCP server 读取加密凭证
- [x] 更新 /umu skill 支持交互式配置
- [x] ruff 检查通过
- [x] credential_manager 基本功能验证通过
- [x] 解决系统级 umu_sdk 路径冲突问题
- [x] 编写完整单元测试
- [x] 更新 README/CHANGELOG
- [ ] 发布前在干净环境验证 `python -m umu_sdk.skills.install`
