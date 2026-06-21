# 课程协同 API 发现

来源：`dev-tools/tracer/outputs/session-live-1781529046.jsonl`

## 核心端点

| 操作 | Method | URL | Content-Type |
|------|--------|-----|--------------|
| 查询协同者列表 | GET | `/api/cooperation/getall` | - |
| 搜索可协同账号 | POST | `/api/manage/accessaccountmatchv2` | `application/x-www-form-urlencoded` |
| 添加/调整协同权限 | POST | `/api/cooperation/addcooperators` | `application/x-www-form-urlencoded` |
| 删除协同权限 | POST | `/api/cooperation/del` | `application/x-www-form-urlencoded` |
| 转让课程拥有者 | POST | `/uapi/v1/cooperation/permission-transfer` | `application/x-www-form-urlencoded` |

## 查询协同者列表

```
GET /api/cooperation/getall?t={timestamp}&append_manage_role=1&obj_id={group_id}&obj_type=group&page=1&size=20
```

响应关键字段：

```json
{
  "status": true,
  "errno": 0,
  "error_code": 0,
  "data": {
    "total": 2,
    "list": [
      {
        "cooperation_info_id": "9448472",
        "teacher_id": "17578115",
        "role_type": "cooperator",
        "cooperator_type": "teacher",
        "is_user": 1,
        "teacher_email": "example@example.com",
        "umu_id": "17580402",
        "teacher_name": "...",
        "enterprise_id": 25105,
        "is_manager": true,
        "manager_role_type": "1"
      }
    ],
    "creator_info": { "teacher_id": "20438403", "role_type": "creator", ... },
    "page_info": { "list_total_num": 2, "total_page_num": 1, ... }
  },
  "success": false  // 注意：业务字段 success=false，但 status=true 且 error_code=0 表示成功
}
```

`role_type` 映射：

| role_type | 中文权限 |
|-----------|----------|
| `cooperator` | 编辑者 |
| `operator` | 运营者 |
| `viewer` | 查看者 |

## 搜索可协同账号

```
POST /api/manage/accessaccountmatchv2
Content-Type: application/x-www-form-urlencoded

accounts={keyword}&search_source=add_cooperator&is_suggestion=1&group_id={group_id}&is_sug=1
```

响应示例：

```json
{
  "data": [
    {
      "account": "example@example.com",
      "account_type": "user",
      "email": "example@example.com",
      "phone": "",
      "login_name": "",
      "id": "17580402",
      "umu_id": "c0369669784884c02e6be3acfbef6b92",
      "student_id": "38900914",
      "user_name": "...",
      "is_exist": 1,
      "sort_index": 0
    }
  ]
}
```

> 仅返回角色为讲师/学习负责人/子管理员/管理员的账号，学员角色不会被查询到。

## 添加/调整协同权限

```
POST /api/cooperation/addcooperators
Content-Type: application/x-www-form-urlencoded

obj_id={group_id}&obj_type=group&accounts={urlencoded_json_array}
```

accounts 数组项：

```json
{
  "type": 1,
  "role_type": "cooperator",
  "account": "example@example.com",
  "account_type": "user",
  "umu_id": "17580402"
}
```

- `role_type` 可取 `cooperator` / `operator` / `viewer`
- 对已经存在的协同者再次调用，可变更其权限（例如从 `operator` 改为 `cooperator`）
- 可用 `account` 为邮箱、手机号、用户名等；`account_type` 固定为 `"user"`

## 删除协同权限

```
POST /api/cooperation/del
Content-Type: application/x-www-form-urlencoded

cooperation_info_ids={cooperation_info_id}
```

响应：

```json
{ "data": { "result": 1 } }
```

> `cooperation_info_id` 从 `getall` 列表中获取。

## 转让课程拥有者

```
POST /uapi/v1/cooperation/permission-transfer
Content-Type: application/x-www-form-urlencoded

obj_id={group_id}&obj_type=group&transferred_teacher_id={teacher_id}
```

响应：

```json
{ "error_code": 0, "error_message": "", "data": { "status": 1 } }
```

> `transferred_teacher_id` 是目标用户的 `teacher_id`（从搜索结果中获取，注意不是 `umu_id` 字符串，而是 `id` 字段）。

## 实现注意

1. 协同者账号搜索和添加是**两个步骤**：先 `accessaccountmatchv2` 获得 `id` / `umu_id` / `account`，再调用 `addcooperators`。
2. 删除需要 `cooperation_info_id`，因此删除前必须先 `getall`。
3. 转让需要目标用户的 `teacher_id`，同样依赖搜索。
4. UMU 响应中 `status=true` / `error_code=0` 即表示业务成功，但顶层可能仍有 `success=false`，判断时不要依赖 `success` 字段。
5. 所有 POST 均为 `application/x-www-form-urlencoded`。
