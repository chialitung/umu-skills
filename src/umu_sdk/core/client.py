# umu-skills: unofficial UMU platform automation helpers
# This file is part of an independent, third-party project and is not
# affiliated with UMU. Use at your own risk.

"""UMU HTTP 客户端和主客户端.

基于 httpx 构建，提供同步/异步请求支持、认证拦截、错误处理、环境验证.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("umu.sdk.client")

from .auth import AuthManager  # noqa: E402
from .errors import (  # noqa: E402
    AuthenticationError,
    NetworkError,
    RateLimitError,
    ServerError,
    UMUError,
    ValidationError,
)
from .models import LoginCredentials  # noqa: E402
from .rate_limiter import RateLimiter  # noqa: E402




class UMUClient:
    """UMU API 客户端.

    Usage:
        client = UMUClient(base_url="https://www.umu.cn")
        client.login("username", "password")

        courses = client.get("/api/courses")
        print(courses)

    """

    def __init__(
        self,
        base_url: str,
        auth: LoginCredentials | None = None,
        timeout: float = 30.0,
        retries: int = 3,
        follow_redirects: bool = True,
        endpoint_overrides: dict[str, str] | None = None,
        min_request_interval: float = 0.5,
    ):
        """初始化 UMU 客户端.

        Args:
            base_url: UMU 基础 URL，如 https://www.umu.cn
            auth: 可选的登录凭据
            timeout: 请求超时（秒）
            retries: 重试次数
            follow_redirects: 是否跟随重定向
            endpoint_overrides: 接口路径覆盖（用于极少数接口路径不同的情况）
            min_request_interval: 两次 UMU 接口调用之间的最小时间间隔（秒），默认 0.5.
        """
        if not base_url:
            raise ValueError("UMUClient 初始化失败: base_url 不能为空")

        # 验证 URL 格式
        try:
            parsed = urlparse(base_url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError
        except Exception:
            raise ValueError(f"UMUClient 初始化失败: base_url '{base_url}' 不是有效的 URL")

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.endpoint_overrides = endpoint_overrides or {}
        self._rate_limiter = RateLimiter(min_interval=min_request_interval)

        # 从 base_url 推断 desktop_domain 和 mobile_domain
        hostname = parsed.hostname or ""
        self.desktop_domain = hostname
        if hostname.startswith("www."):
            self.mobile_domain = "m." + hostname[4:]
        else:
            self.mobile_domain = "m." + hostname

        # 初始化 HTTP 客户端
        self.http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=follow_redirects,
            headers={
                "Accept": "application/json",
                "User-Agent": "umu-sdk-python/0.1.0",
            },
        )

        # 初始化认证管理器
        self.auth = AuthManager(self.http, auth, self.base_url, rate_limiter=self._rate_limiter)

        logger.info("UMUClient 初始化完成，目标: %s", self.base_url)
        logger.debug(
            "desktop_domain: %s, mobile_domain: %s",
            self.desktop_domain,
            self.mobile_domain,
        )

    def desktop_url(self, path: str) -> str:
        """构建桌面端完整 URL.

        Args:
            path: URL 路径，如 "/uapi/v1/element/list"

        Returns:
            完整 URL，如 "https://www.umu.cn/uapi/v1/element/list"
        """
        path = path if path.startswith("/") else "/" + path
        return f"https://{self.desktop_domain}{path}"

    def mobile_url(self, path: str) -> str:
        """构建移动端完整 URL.

        Args:
            path: URL 路径，如 "/api/session/makeweikestatus"

        Returns:
            完整 URL，如 "https://m.umu.cn/api/session/makeweikestatus"
        """
        path = path if path.startswith("/") else "/" + path
        return f"https://{self.mobile_domain}{path}"

    def endpoint(self, name: str, default_path: str, domain: str = "desktop") -> str:
        """获取接口 URL，支持环境特定的路径覆盖.

        用于极少数情况下，不同环境的接口路径不同（不只是 domain 不同）。
        大部分接口只需用 desktop_url() / mobile_url()。

        Args:
            name: 接口名称（用于查找覆盖配置）
            default_path: 默认路径
            domain: "desktop" 或 "mobile"

        Returns:
            完整 URL
        """
        path = self.endpoint_overrides.get(name, default_path)
        if domain == "mobile":
            return self.mobile_url(path)
        return self.desktop_url(path)

    def login(self, username: str | None = None, password: str | None = None) -> str:
        """登录并获取 Token.

        Args:
            username: 用户名/邮箱/手机号
            password: 明文密码

        Returns:
            认证 Token
        """
        token = self.auth.login(username, password)

        # 登录成功后，将 Cookie 同步到 httpx 客户端
        # httpx 会自动管理 cookies
        return token

    def _request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        """发送 HTTP 请求（带重试和错误处理）."""
        # 附加认证信息
        auth_headers = self.auth.get_auth_headers()
        headers = kwargs.pop("headers", {})
        headers.update(auth_headers)

        last_error: Exception | None = None

        for attempt in range(self.retries):
            try:
                self._rate_limiter.wait_if_needed()
                logger.debug("HTTP %s %s", method.upper(), url)
                response = self.http.request(method, url, headers=headers, **kwargs)
                response.raise_for_status()
                logger.debug("HTTP %d %s", response.status_code, url)
                return response

            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                last_error = e

                # 可重试错误：先判断是否需要重试，重试耗尽后再转换为 UMUError
                if status in (429, 502, 503, 504) and attempt < self.retries - 1:
                    import time

                    wait_time = 2**attempt  # 指数退避
                    logger.warning(
                        "HTTP 请求失败，%ds 后重试 (%d/%d)",
                        wait_time,
                        attempt + 1,
                        self.retries,
                    )
                    time.sleep(wait_time)
                    continue

                self._handle_http_error(status, e.response)
                break

            except (httpx.RequestError, OSError) as e:
                last_error = e
                if attempt < self.retries - 1:
                    import time

                    logger.warning("HTTP 请求异常，1s 后重试: %s", e)
                    time.sleep(1)
                    continue
                break

        # 所有重试失败
        if isinstance(last_error, UMUError):
            raise last_error
        raise NetworkError(f"请求失败: {last_error}")

    def _handle_http_error(self, status: int, response: httpx.Response) -> None:
        """处理 HTTP 错误状态码."""
        try:
            data = response.json()
        except Exception:
            data = {"message": response.text}

        message = data.get("message") or data.get("error") or data.get("error_message") or "请求失败"

        if status == 401:
            raise AuthenticationError(message)
        elif status == 403:
            raise UMUError(message, code="FORBIDDEN", status=403)
        elif status == 404:
            raise UMUError(message, code="NOT_FOUND", status=404)
        elif status == 422:
            raise ValidationError(message, details=data.get("errors", data))
        elif status == 429:
            raise RateLimitError(message)
        elif status >= 500:
            raise ServerError(message, status=status)
        else:
            raise UMUError(message, code=f"HTTP_{status}", status=status)

    def get(self, url: str, params: dict | None = None, **kwargs: Any) -> Any:
        """GET 请求."""
        response = self._request("GET", url, params=params, **kwargs)
        return response.json()

    def post(self, url: str, data: Any = None, json: Any = None, **kwargs: Any) -> Any:
        """POST 请求."""
        response = self._request("POST", url, data=data, json=json, **kwargs)
        return response.json()

    def put(self, url: str, data: Any = None, json: Any = None, **kwargs: Any) -> Any:
        """PUT 请求."""
        response = self._request("PUT", url, data=data, json=json, **kwargs)
        return response.json()

    def patch(self, url: str, data: Any = None, json: Any = None, **kwargs: Any) -> Any:
        """PATCH 请求."""
        response = self._request("PATCH", url, data=data, json=json, **kwargs)
        return response.json()

    def delete(self, url: str, **kwargs: Any) -> Any:
        """DELETE 请求."""
        response = self._request("DELETE", url, **kwargs)
        if response.status_code == 204:
            return None
        return response.json()

    def close(self) -> None:
        """关闭客户端，释放资源."""
        self.http.close()
        logger.debug("UMUClient 已关闭")

    def __enter__(self) -> "UMUClient":
        """上下文管理器入口."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """上下文管理器退出."""
        self.close()
