# Admin MCP 账号清单数据字典

> 适用范围：`adm_list_accounts`、`adm_get_scheduled_disables` 等返回企业账号列表的 Admin MCP Tool。
> 数据来源：
> - UMU 桌面端接口 `GET /ajax/enterprise/getUserList`
> - Admin MCP 实际调用结果（`umu_id=17580402` 等企业）

---

## 1. 接口说明

| 项目 | 值 |
|------|-----|
| 接口地址 | `https://www.umu.cn/ajax/enterprise/getUserList` |
| 请求方式 | GET |
| 鉴权方式 | Cookie（`estuidtoken`） |
| MCP Tool | `adm_list_accounts` |
| 代码位置 | `src/umu_sdk/adapters/mcp/admin.py:1039` |

---

## 2. 请求参数

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|------|--------|------|
| `is_manager` | `str` | 是 | `0` | `0`=返回全部账号（不限制角色），`1`=仅返回管理视角账号（包含系统管理员、子管理员、学习负责人三类角色） |
| `page` | `str` | 是 | `1` | 页码 |
| `size` | `str` | 是 | `500` | 每页数量，最大 `500` |
| `group_operator` | `str` | 是 | `intersection` | 多分组关系：`intersection`=交集，`union`=并集 |
| `keywords` | `str` | 否 | — | 按姓名、邮箱、手机号、用户名模糊搜索 |
| `group_ids` | `str` | 否 | — | 分组 ID 列表，逗号分隔，如 `177124,177125` |
| `role_type` | `str` | 否 | — | 角色筛选：`1`=学员，`2`=讲师，`3`=学习负责人，`4`=系统管理员，`5`=子管理员 |
| `account_status` | `str` | 否 | — | 状态筛选：`0`=待加入，`1`=已启用，`2`=定时禁用，`3`=已禁用（以实际平台为准） |

---

## 3. UMU 原始响应结构

```json
{
  "status": true,
  "errno": 0,
  "error_code": 0,
  "error": "success",
  "data": {
    "page_info": {
      "list_total_num": 13,
      "total_page_num": 1,
      "current_page": 1,
      "size": 20
    },
    "list": [
      { /* 账号对象，见第 4 节 */ }
    ],
    "total": 13,
    "page": 1,
    "size": 20
  },
  "token": "...",
  "page_token": "...",
  "version": "...",
  "config": {},
  "success": true
}
```

### 3.1 外层分页字段

| 字段路径 | 类型 | 示例值 | 说明 |
|----------|------|--------|------|
| `data.page_info.list_total_num` | `int` | `13` | 符合条件的账号总数 |
| `data.page_info.total_page_num` | `int` | `1` | 总页数 |
| `data.page_info.current_page` | `int` | `1` | 当前页码 |
| `data.page_info.size` | `int` | `20` | 当前页大小 |
| `data.total` | `int` | `13` | 总数（与 `list_total_num` 一致） |
| `data.page` | `int` | `1` | 当前页码 |
| `data.size` | `int` | `20` | 当前页大小 |

---

## 4. 账号对象字段（原始 API）

以下字段直接来自 UMU `/ajax/enterprise/getUserList` 响应中的单个账号对象。

| 字段名 | 类型 | 示例值 | 是否可为空 | 说明 |
|--------|------|--------|------------|------|
| `umu_id` | `str` | `"17580402"` | 否 | UMU 用户唯一标识，后续禁用/启用账号均使用该字段 |
| `is_active` | `str` | `"1"` | 否 | 是否活跃。`"1"`=活跃，`"0"`=不活跃 |
| `role_type` | `str` | `"4"` | 否 | 角色类型码，见第 6 节角色映射 |
| `status` | `str` | `"1"` | 否 | 账号状态码（字符串形式），与 `account_status` 含义相同 |
| `number` | `str` | `"10000001"` | 是 | 员工编号/工号 |
| `platform_permission` | `str` | `"1"` | 否 | 平台权限标识。`"1"`=有权限 |
| `user_name` | `str` | `"友邦人寿保险有限公司"` | 否 | 用户姓名/企业名称 |
| `user_name_letter` | `str` | `"youbangrenshoubaoxianyouxiangongsi"` | 否 | 用户姓名的拼音（全小写、无空格），用于排序或搜索 |
| `area_code` | `str` | `"86"` | 是 | 手机号国际区号 |
| `phone` | `str` | `"13800138000"` | 是 | 手机号 |
| `email` | `str` | `"160534520@qq.com"` | 是 | 邮箱地址，可作为账号唯一标识使用 |
| `login_name` | `str` | `"zhangsan"` | 是 | 登录用户名 |
| `account_joining_time` | `int` | `1739340186` | 否 | 账号加入时间，Unix 时间戳（秒） |
| `first_login_time` | `int` | `1739345012` | 否 | 首次登录时间，Unix 时间戳（秒） |
| `last_login_time` | `int` | `1781186189` | 否 | 最后一次登录时间，Unix 时间戳（秒） |
| `invite_url` | `str` | `""` | 是 | 邀请链接，待加入账号可能非空 |
| `account_status` | `int` | `1` | 否 | 账号状态码（数值形式），见第 6 节状态映射 |
| `effective_time` | `int` | `0` | 否 | 定时禁用生效时间。`0`=无定时禁用，否则为 Unix 时间戳 |
| `departments` | `str` | `"A"` / `"-"` | 否 | 所属部门名称，多个部门可能以逗号分隔；`-` 表示未分配部门 |

### 4.1 字段类型一致性注意

- `role_type`、`status`、`is_active`、`platform_permission` 在原始响应中为字符串类型，但在 MCP Tool 内部会按需转换为整数。
- `account_status` 在原始响应中为整数类型。
- 所有时间戳字段均为秒级 Unix 时间戳。

---

## 5. MCP Tool 标准化字段

`adm_list_accounts` 对原始字段做了裁剪、转换和补充，形成以下标准化输出（位于 `data.accounts[]` 中）。

| 字段名 | 来源字段 | 类型 | 示例值 | 说明 |
|--------|----------|------|--------|------|
| `umu_id` | `umu_id` | `str` | `"17580402"` | UMU 用户唯一标识 |
| `user_name` | `user_name` | `str` | `"友邦人寿保险有限公司"` | 用户姓名 |
| `email` | `email` | `str` | `"160534520@qq.com"` | 邮箱地址 |
| `phone` | `phone` | `str` | `"13800138000"` | 手机号 |
| `login_name` | `login_name` | `str` | `"zhangsan"` | 登录用户名 |
| `number` | `number` | `str` | `"10000001"` | 员工编号 |
| `account_status` | `account_status` | `int` | `1` | 账号状态码（数值） |
| `status_text` | 计算字段 | `str` | `"已启用"` | 状态人读文本，基于 `_STATUS_TEXT_MAP` |
| `is_active` | `is_active` | `str` | `"1"` | 是否活跃 |
| `role_type` | `role_type` | `int` | `4` | 角色类型码（数值） |
| `role_name` | 计算字段 | `str` | `"系统管理员"` | 角色人读文本，基于 `_ROLE_TYPE_MAP` |
| `departments` | `departments` | `str` | `"A"` | 所属部门 |
| `account_joining_time` | `account_joining_time` | `int` | `1739340186` | 账号加入时间戳 |
| `account_joining_time_readable` | 计算字段 | `str` | `"2025-02-12 14:03:06"` | 账号加入时间，北京时间字符串 |
| `first_login_time` | `first_login_time` | `int` | `1739345012` | 首次登录时间戳 |
| `first_login_time_readable` | 计算字段 | `str` | `"2025-02-12 15:23:32"` | 首次登录时间，北京时间字符串 |
| `last_login_time` | `last_login_time` | `int` | `1781186189` | 最后登录时间戳 |
| `last_login_time_readable` | 计算字段 | `str` | `"2026-06-11 21:56:29"` | 最后登录时间，北京时间字符串 |

### 5.1 被 MCP 过滤的原始字段

以下字段在原始响应中存在，但当前 `adm_list_accounts` 未纳入标准化输出：

| 原始字段 | 未纳入原因/建议 |
|----------|----------------|
| `user_name_letter` | 拼音辅助字段，用于排序，可在批量导出时补充 |
| `area_code` | 手机号区号，与 `phone` 合并处理更合适 |
| `platform_permission` | 平台权限标识，当前业务暂未使用 |
| `invite_url` | 邀请链接，主要用于待加入账号 |
| `effective_time` | 定时禁用时间，主要用于 `adm_get_scheduled_disables` |
| `status` | 与 `account_status` 重复，MCP 使用数值型 `account_status` |

---

## 6. 枚举值映射

### 6.1 角色类型（`role_type`）

代码位置：`src/umu_sdk/adapters/mcp/admin.py:489`

| 码值 | 含义 | 说明 |
|------|------|------|
| `1` | 学员 | 普通学习账号 |
| `2` | 讲师 | 可创建课程、管理内容 |
| `3` | 学习负责人 | 可管理学习数据和学员 |
| `4` | 系统管理员 | 拥有最高管理权限 |
| `5` | 子管理员 | 具备部分管理权限的子级管理员 |

### 6.2 `is_manager` 与 `role_type` 的关系

根据实际数据观察，`is_manager` 参数用于切换"全量账号"与"管理视角账号"两个查询范围：

- **`is_manager=0`（默认）**：返回企业中的**全部账号**，不限制 `role_type`。实际数据中主要包含 `role_type=1`（学员）和 `role_type=2`（讲师），但也可能包含少量 `role_type=3/4/5` 的管理类角色账号。
- **`is_manager=1`（管理视角）**：仅返回具有管理权限的账号，范围限定为 **`role_type=3`（学习负责人）、`role_type=4`（系统管理员）、`role_type=5`（子管理员）**。

> **实践建议**：
> - 如果要完整导出企业全部账号，直接调用 `is_manager=0`（默认）即可。
> - 如果只想导出管理类账号，调用 `is_manager=1`。
> - 不建议将两次结果简单合并去重，因为 `is_manager=0` 的结果理论上已经包含全部账号。

### 6.3 账号状态（`account_status`）

代码位置：`src/umu_sdk/adapters/mcp/admin.py:494`

| 码值 | 含义 | 说明 |
|------|------|------|
| `0` | 待加入 | 账号已创建但用户未激活 |
| `1` | 已启用 | 正常可用状态 |
| `2` | 定时禁用 | 已设置未来某个时间自动禁用 |
| `3` | 已禁用 | 立即禁用状态 |

> ⚠️ **重要提示**：不同企业的 UMU 平台状态码映射可能存在差异。代码注释明确说明 `"请以 adm_list_accounts 的 account_status 筛选结果为准确认实际映射"`。在跨企业数据处理时，务必先通过筛选接口校验状态码含义。

### 6.4 是否活跃（`is_active`）

| 码值 | 含义 |
|------|------|
| `"1"` | 活跃 |
| `"0"` | 不活跃 |

---

## 7. 完整示例

### 7.1 请求示例

```bash
GET https://www.umu.cn/ajax/enterprise/getUserList?
    t=1781186193576
    &is_manager=0
    &page=1
    &size=20
    &group_operator=intersection
```

### 7.2 MCP 调用示例

```python
result = await adm_list_accounts(
    keywords="",
    group_ids=None,
    role_type=None,
    account_status=None,
    is_manager=0,
    page=1,
    page_size=20,
    fetch_all=False,
)
```

### 7.3 响应示例

```json
{
  "success": true,
  "data": {
    "accounts": [
      {
        "umu_id": "17580402",
        "user_name": "友邦人寿保险有限公司",
        "email": "160534520@qq.com",
        "phone": "",
        "login_name": "",
        "number": "",
        "account_status": 1,
        "status_text": "已启用",
        "is_active": "1",
        "role_type": 4,
        "role_name": "系统管理员",
        "departments": "A",
        "account_joining_time": 1739340186,
        "account_joining_time_readable": "2025-02-12 14:03:06",
        "first_login_time": 1739345012,
        "first_login_time_readable": "2025-02-12 15:23:32",
        "last_login_time": 1781186189,
        "last_login_time_readable": "2026-06-11 21:56:29"
      }
    ],
    "total": 1,
    "pagination": {
      "total_all": 13,
      "current_page": 1,
      "page_size": 20
    }
  },
  "error_code": "",
  "error_message": "",
  "suggested_action": "使用 umu_id 或 email 调用 adm_disable_account / adm_enable_account",
  "next_action": "proceed"
}
```

---

## 8. 后续数据处理建议

基于本数据字典，可开展的复杂数据处理场景：

1. **批量账号导出**：以 `umu_id` 为主键，合并 `user_name`、`email`、`phone`、`departments`、`number` 等字段。
2. **账号生命周期分析**：结合 `account_joining_time`、`first_login_time`、`last_login_time` 计算激活率、活跃度。
3. **权限审计**：按 `role_type` / `role_name` 统计各类角色分布。
4. **状态变更监控**：定期拉取 `account_status`，对比历史数据识别新增禁用/启用账号。
5. **定时禁用管理**：结合 `effective_time` 实现定时禁用提醒和批量启用策略。

---

## 9. 版本与维护

- 文档版本：`v1.0.0`
- 基于 Admin MCP 版本：`0.2.0`
- 最后更新：2026-06-12
- 维护者：开发团队

> 当 UMU 接口返回字段发生变更时，应同步更新本文档以及 `src/umu_sdk/core/admin_models.py`（如已创建）。
