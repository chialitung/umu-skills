"""UMU SDK Pydantic 模型."""

from typing import Literal

from pydantic import BaseModel, Field


class Course(BaseModel):
    """课程模型."""

    id: str
    title: str
    description: str | None = None
    category_id: str | None = Field(None, alias="categoryId")
    category_name: str | None = Field(None, alias="categoryName")
    cover_image: str | None = Field(None, alias="coverImage")
    status: Literal["draft", "published", "archived"] = "draft"
    created_at: str = Field(..., alias="createdAt")
    updated_at: str = Field(..., alias="updatedAt")
    author_id: str | None = Field(None, alias="authorId")
    author_name: str | None = Field(None, alias="authorName")
    duration: int | None = None
    pass_score: int | None = Field(None, alias="passScore")
    mandatory: bool = False
    resource_count: int | None = Field(None, alias="resourceCount")
    student_count: int | None = Field(None, alias="studentCount")

    class Config:
        populate_by_name = True


class CreateCourseRequest(BaseModel):
    """创建课程请求."""

    title: str
    description: str | None = None
    category_id: str | None = Field(None, alias="categoryId")
    cover_image: str | None = Field(None, alias="coverImage")
    status: Literal["draft", "published"] = "draft"
    duration: int | None = None
    pass_score: int | None = Field(None, alias="passScore")
    mandatory: bool = False

    class Config:
        populate_by_name = True


class UpdateCourseRequest(BaseModel):
    """更新课程请求."""

    title: str | None = None
    description: str | None = None
    category_id: str | None = Field(None, alias="categoryId")
    cover_image: str | None = Field(None, alias="coverImage")
    status: Literal["draft", "published", "archived"] | None = None
    duration: int | None = None
    pass_score: int | None = Field(None, alias="passScore")
    mandatory: bool | None = None

    class Config:
        populate_by_name = True


class CourseRule(BaseModel):
    """课程规则."""

    course_id: str = Field(..., alias="courseId")
    mandatory: bool = False
    pass_score: int = Field(..., alias="passScore")
    time_limit: int | None = Field(None, alias="timeLimit")
    deadline: str | None = None
    attempts: int | None = None
    certificate_enabled: bool | None = Field(None, alias="certificateEnabled")

    class Config:
        populate_by_name = True


class ListCoursesParams(BaseModel):
    """课程列表查询参数."""

    page: int = 1
    page_size: int = Field(20, alias="pageSize")
    search: str | None = None
    category_id: str | None = Field(None, alias="categoryId")
    status: str | None = None
    sort_by: str | None = Field(None, alias="sortBy")
    sort_order: Literal["asc", "desc"] | None = Field(None, alias="sortOrder")

    class Config:
        populate_by_name = True


class PaginatedResponse(BaseModel):
    """分页响应."""

    data: list
    total: int
    page: int
    page_size: int = Field(..., alias="pageSize")
    total_pages: int = Field(..., alias="totalPages")

    class Config:
        populate_by_name = True


class LoginCredentials(BaseModel):
    """登录凭据."""

    username: str
    password: str
    base_url: str | None = Field(None, alias="baseURL")

    class Config:
        populate_by_name = True


class LoginResponse(BaseModel):
    """登录响应."""

    success: bool
    message: str | None = None
    token: str | None = None
    user_info: dict | None = Field(None, alias="userInfo")

    class Config:
        populate_by_name = True
