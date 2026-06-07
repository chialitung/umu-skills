"""UMU Skills SDK Core.

SDK 核心层 — HTTP 客户端、认证、加密、数据模型、错误处理.
"""

from .client import UMUClient
from .auth import AuthManager
from .encrypt import encrypt_password, decrypt_password, verify_encryption
from .errors import (
    UMUError,
    EnvironmentMismatchError,
    AuthenticationError,
    ValidationError,
    RateLimitError,
    ServerError,
)
from .models import (
    Course,
    CreateCourseRequest,
    UpdateCourseRequest,
    ListCoursesParams,
    PaginatedResponse,
)

__all__ = [
    "UMUClient",
    "AuthManager",
    "encrypt_password",
    "decrypt_password",
    "verify_encryption",
    "UMUError",
    "EnvironmentMismatchError",
    "AuthenticationError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
    "Course",
    "CreateCourseRequest",
    "UpdateCourseRequest",
    "ListCoursesParams",
    "PaginatedResponse",
]
