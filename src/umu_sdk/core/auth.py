"""UMU 认证管理器.

处理登录、Token 管理.
"""

from __future__ import annotations

import logging
import time

import httpx

from .encrypt import encrypt_password
from .errors import AuthenticationError
from .models import LoginCredentials

logger = logging.getLogger("umu.sdk.auth")


class AuthManager:
    """认证管理器."""

    def __init__(
        self,
        http: httpx.Client,
        credentials: LoginCredentials | None = None,
        base_url: str = "",
    ):
        self.http = http
        self.credentials = credentials
        self.base_url = base_url or (credentials.base_url if credentials else "")

        self._token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float = 0.0

    def login(self, username: str | None = None, password: str | None = None) -> str:
        """使用用户名密码登录。

        UMU 实际登录端点: POST /passport/ajax/account/login
        请求体: application/x-www-form-urlencoded
        格式: username=xxx&passwd=AES加密后的Base64密码

        Args:
            username: 用户名/邮箱/手机号
            password: 明文密码

        Returns:
            认证 Token (estuidtoken Cookie 值)

        Raises:
            AuthenticationError: 登录失败
        """
        user = username or (self.credentials.username if self.credentials else None)
        pwd = password or (self.credentials.password if self.credentials else None)

        if not user or not pwd:
            raise AuthenticationError("缺少登录凭据")

        logger.info("正在登录: %s", user)
        if self.base_url:
            logger.debug("目标环境: %s", self.base_url)

        # 加密密码
        encrypted_password = encrypt_password(pwd)

        # UMU 登录端点
        endpoint = "/passport/ajax/account/login"

        try:
            response = self.http.post(
                endpoint,
                data={
                    "username": user,
                    "passwd": encrypted_password,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
            response.raise_for_status()

            data = response.json()

            # UMU 响应格式: {status: bool, errno: int, error: str, data: {...}}
            if data.get("status") is True or data.get("errno") == 0 or data.get("error_code") == 0:
                # 从 Cookie 中提取 estuidtoken
                cookies = response.cookies
                token = cookies.get("estuidtoken")

                if token:
                    self._token = token
                    self._token_expires_at = time.time() + 3600 * 24  # 假设 24 小时过期
                    logger.info("登录成功")
                    return token

            # 登录失败
            error_msg = data.get("error") or data.get("message") or data.get("error_message") or "登录失败"
            raise AuthenticationError(error_msg)

        except AuthenticationError:
            raise
        except httpx.HTTPStatusError as e:
            raise AuthenticationError(f"HTTP {e.response.status_code}: {e.response.text}")
        except Exception as e:
            raise AuthenticationError(f"登录请求失败: {e}")

    def set_token(self, token: str) -> None:
        """设置已有 Token."""
        self._token = token

    def get_token(self) -> str | None:
        """获取当前 Token."""
        if self._token and time.time() >= self._token_expires_at - 60:
            logger.warning("Token 即将过期")
        return self._token

    def is_authenticated(self) -> bool:
        """检查是否已认证."""
        return bool(self._token) and time.time() < self._token_expires_at

    def logout(self) -> None:
        """登出."""
        self._token = None
        self._refresh_token = None
        self._token_expires_at = 0
        logger.info("已登出")

    def get_auth_headers(self) -> dict[str, str]:
        """获取认证请求头."""
        token = self.get_token()
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}
