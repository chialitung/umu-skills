"""RateLimiter 与 UMUClient 请求频率限制测试."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from umu_sdk.core.auth import AuthManager
from umu_sdk.core.client import UMUClient
from umu_sdk.core.rate_limiter import RateLimiter


class TestRateLimiter:
    def test_first_call_does_not_wait(self) -> None:
        limiter = RateLimiter(min_interval=0.5)
        wait_time = limiter.wait_if_needed()
        assert wait_time == 0.0

    def test_wait_when_interval_not_elapsed(self) -> None:
        limiter = RateLimiter(min_interval=0.2)
        limiter.wait_if_needed()
        wait_time = limiter.wait_if_needed()
        assert wait_time > 0.0
        assert wait_time <= 0.2

    def test_no_wait_when_interval_elapsed(self) -> None:
        limiter = RateLimiter(min_interval=0.1)
        limiter.wait_if_needed()
        time.sleep(0.15)
        wait_time = limiter.wait_if_needed()
        assert wait_time == 0.0

    def test_negative_interval_raises(self) -> None:
        with pytest.raises(ValueError, match="min_interval 不能为负数"):
            RateLimiter(min_interval=-0.5)

    def test_zero_interval_no_wait(self) -> None:
        limiter = RateLimiter(min_interval=0.0)
        limiter.wait_if_needed()
        wait_time = limiter.wait_if_needed()
        assert wait_time == 0.0

    def test_thread_safety(self) -> None:
        import threading

        limiter = RateLimiter(min_interval=0.05)
        results: list[float] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                results.append(limiter.wait_if_needed())
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        start = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.monotonic() - start

        assert not errors
        # 5 个线程串行通过 0.05s 间隔，至少等待 4 * 0.05 = 0.2s
        assert elapsed >= 0.18


class TestUMUClientRateLimiting:
    @patch("umu_sdk.core.client.httpx.Client")
    @patch("umu_sdk.core.client.AuthManager")
    def test_request_calls_rate_limiter(
        self, mock_auth_cls: MagicMock, mock_http_cls: MagicMock
    ) -> None:
        http = mock_http_cls.return_value
        http.request.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})

        client = UMUClient(base_url="https://www.umu.cn", min_request_interval=0.1)
        limiter = client._rate_limiter

        with patch.object(limiter, "wait_if_needed") as mock_wait:
            client.get("https://www.umu.cn/api/test")
            mock_wait.assert_called_once()

    @patch("umu_sdk.core.client.httpx.Client")
    @patch("umu_sdk.core.client.AuthManager")
    def test_request_enforces_interval(
        self, mock_auth_cls: MagicMock, mock_http_cls: MagicMock
    ) -> None:
        http = mock_http_cls.return_value
        http.request.return_value = MagicMock(status_code=200, json=lambda: {"ok": True})

        client = UMUClient(base_url="https://www.umu.cn", min_request_interval=0.15)

        client.get("https://www.umu.cn/api/test")
        start = time.monotonic()
        client.get("https://www.umu.cn/api/test")
        elapsed = time.monotonic() - start

        assert elapsed >= 0.12


class TestAuthManagerRateLimiting:
    def test_login_uses_rate_limiter(self) -> None:
        limiter = RateLimiter(min_interval=0.0)
        http = MagicMock(spec=httpx.Client)
        http.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": True, "errno": 0},
            cookies={"estuidtoken": "token-123"},
        )

        auth = AuthManager(http, rate_limiter=limiter)

        with patch.object(limiter, "wait_if_needed") as mock_wait:
            token = auth.login("user@example.com", "password")
            mock_wait.assert_called_once()
            assert token == "token-123"

    def test_login_without_rate_limiter_does_not_wait(self) -> None:
        http = MagicMock(spec=httpx.Client)
        http.post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"status": True, "errno": 0},
            cookies={"estuidtoken": "token-123"},
        )

        auth = AuthManager(http)
        token = auth.login("user@example.com", "password")
        assert token == "token-123"
