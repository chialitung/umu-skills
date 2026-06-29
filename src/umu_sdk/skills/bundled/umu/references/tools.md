# UMU MCP 工具参考

本文件列出 `umu-skills` 项目暴露给 Claude 的所有 MCP 工具，按角色分类。

> **说明**：本表仅供 `/umu` skill 参考，不需要向用户完整展示。Skill 应根据用户需求自动选择合适的工具。

## 角色与工具总览

| 角色 | MCP Server | 主要职责 |
|------|-----------|---------|
| Teacher | `umu-teacher` | 课程创建、资源管理、小节编辑、课程设置 |
| Student | `umu-student` | 课程学习、报名、进度查询、考试/问卷/签到 |
| Admin | `umu-admin` | 账号管理、组织架构、学习数据查询、批量启用/禁用账号 |

## Teacher 工具

### 认证与会话
- `tch_login` — 讲师登录
- `tch_check_auth` — 检查认证状态
- `tch_create_session` — 创建会话
- `tch_list_sessions` — 列出会话
- `tch_destroy_session` — 销毁会话

### 资源上传与管理
- `tch_upload_scorm` — 上传 SCORM 包
- `tch_list_resources` — 列出资源
- `tch_rename_resource` — 重命名资源
- `tch_delete_resource` — 删除资源
- `tch_upload_document` — 上传文档
- `tch_list_documents` — 列出文档
- `tch_rename_document` — 重命名文档
- `tch_delete_document` — 删除文档
- `tch_delete_documents_batch` — 批量删除文档
- `tch_upload_documents_batch` — 批量上传文档
- `tch_upload_audio_video` — 上传音视频
- `tch_list_audio_videos` — 列出音视频
- `tch_rename_audio_video` — 重命名音视频
- `tch_delete_audio_video` — 删除音视频

### 课程管理
- `tch_create_course` — 创建空课程
- `tch_get_course` / `tch_get_course_detail` — 获取课程信息
- `tch_update_course` / `tch_update_course_basic` — 更新课程
- `tch_update_course_type` — 更新课程类型
- `tch_update_course_category` — 更新课程分类
- `tch_update_course_schedule` — 更新课程时间
- `tch_update_course_images` — 更新课程封面/图片
- `tch_get_categories` — 获取课程分类

### 小节管理
- `tch_create_scorm_section` — 添加 SCORM 小节
- `tch_update_scorm_section` — 修改 SCORM 小节
- `tch_create_video_section` — 添加视频小节
- `tch_update_video_section` — 修改视频小节
- `tch_create_article_section` — 添加文章小节
- `tch_update_article_section` — 修改文章小节
- `tch_create_infographic_section` — 添加图文小节
- `tch_get_infographic_content` — 获取图文内容
- `tch_update_infographic_section` — 修改图文小节
- `tch_create_document_section` — 添加文档小节
- `tch_update_document_section` — 修改文档小节
- `tch_create_survey_section` — 添加问卷小节
- `tch_update_survey_section` — 修改问卷小节
- `tch_create_exam_section` — 添加考试小节
- `tch_update_exam_section` — 修改考试小节
- `tch_create_signin_section` — 添加签到小节
- `tch_update_signin_section` — 修改签到小节
- `tch_list_sections` — 列出课程小节
- `tch_get_section` — 获取小节详情
- `tch_toggle_section_visibility` — 切换小节可见性
- `tch_delete_section` — 删除小节

## Student 工具

### 认证与会话
- `stu_login` — 学员登录
- `stu_check_auth` — 检查认证状态
- `stu_create_session` / `stu_list_sessions` / `stu_destroy_session` — 会话管理

### 课程与学习
- `stu_resolve_course_url` — 解析课程标识
- `stu_get_course_structure` — 获取课程结构
- `stu_get_learning_progress` — 获取学习进度
- `stu_list_participated_courses` — 列出当前用户已参与学习的课程
- `stu_enroll_course` — 报名课程

### 小节完成
- `stu_browse_lesson` — 浏览/完成视频或文章小节
- `stu_get_questionnaire_questions` — 获取问卷题目
- `stu_submit_questionnaire` — 提交问卷
- `stu_submit_questionnaire_with_config` — 按配置提交问卷
- `stu_check_in` — 签到
- `stu_check_in_with_rating` — 评分签到
- `stu_start_exam` — 开始考试
- `stu_submit_exam` — 提交考试
- `stu_submit_exam_with_config` — 按配置提交考试
- `stu_get_lesson_status` — 获取小节状态
- `stu_complete_course` — 完成课程

### 批量操作
- `stu_batch_import_accounts` — 批量导入账号
- `stu_batch_complete_course` — 批量完成课程

## Admin 工具

### 认证与会话
- `adm_login` — 管理员登录
- `adm_check_auth` — 检查认证状态
- `adm_create_session` / `adm_list_sessions` / `adm_destroy_session` — 会话管理

### 账号管理
- `adm_create_account` — 创建账号
- `adm_list_accounts` — 列出账号
- `adm_get_user_info` — 获取用户信息
- `adm_disable_account` — 禁用账号
- `adm_enable_account` — 启用账号
- `adm_batch_disable_accounts` — 批量禁用账号
- `adm_batch_enable_accounts` — 批量启用账号
- `adm_get_scheduled_disables` — 获取计划禁用列表

### 组织架构
- `adm_list_departments` — 列出部门
- `adm_list_groups` — 列出群组

### 学习数据
- `adm_list_learning_records` — 查询企业账号的课程学习明细
  - 支持按最后学习时间范围、学员关键词、课程名称、部门、分组、班级筛选
  - `student_keywords` 会自动解析为学员 uids 进行精确筛选
  - `class_names` 会自动查询班级列表并解析为班级 IDs 进行精确筛选
  - 支持 `fetch_all=True` 自动获取全量数据
- `adm_list_classes` — 查询企业班级列表（含班级名称、访问码、创建者等信息）

## 工具命名规律

- `tch_` 开头 = Teacher MCP
- `stu_` 开头 = Student MCP
- `adm_` 开头 = Admin MCP

当用户需求涉及多个角色时，按顺序调用相应工具，并把上一步的输出作为下一步的输入。
