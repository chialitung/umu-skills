"""UMU API 调用频率限制器.

用于控制客户端对 UMU 服务器的请求频率，避免高频调用导致服务器性能风险.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("umu.sdk.rate_limiter")


class RateLimiter:
    """基于最小调用间隔的频率限制器.

    保证任意两次请求之间至少间隔 ``min_interval`` 秒.
    线程安全，可在多线程环境下共享.
    """

    def __init__(self, min_interval: float = 0.5) -> None:
        """初始化频率限制器.

        Args:
            min_interval: 两次请求之间的最小时间间隔（秒），必须 >= 0.
        """
        if min_interval < 0:
            raise ValueError("min_interval 不能为负数")

        self.min_interval = min_interval
        self._last_request_time: float = 0.0
        self._lock = threading.Lock()

    def wait_if_needed(self) -> float:
        """根据上次请求时间决定是否需要等待，并返回实际等待时长.

        若距离上次请求不足 ``min_interval`` 秒，则阻塞当前线程直到满足间隔.
        首次调用或间隔已满足时立即返回 0.

        Returns:
            实际等待的秒数.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            wait_time = max(0.0, self.min_interval - elapsed)

            if wait_time > 0:
                logger.debug(
                    "请求频率限制：等待 %.3fs 以满足 %.3fs 的最小调用间隔",
                    wait_time,
                    self.min_interval,
                )
                time.sleep(wait_time)

            self._last_request_time = time.monotonic()
            return wait_time
