"""Student MCP SCORM 小节完成能力测试."""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from umu_sdk.adapters.mcp.student import (
    _format_scorm_total_time,
    _parse_scorm_launch_url,
)


def test_format_scorm_total_time() -> None:
    assert _format_scorm_total_time(0) == "0000:00:00.00"
    assert _format_scorm_total_time(3661) == "0001:01:01.00"
    assert _format_scorm_total_time(360000) == "0100:00:00.00"


def test_parse_scorm_launch_url_success() -> None:
    url = (
        "https://vfua3ytp5.m.umu.cn/scorm/324/launch/456/course/789/element/abc"
        "?sesskey=XYZ&attempt=2"
    )
    params = _parse_scorm_launch_url(url)
    assert params["subdomain"] == "vfua3ytp5"
    assert params["a"] == "324"
    assert params["scoid"] == "456"
    assert params["course"] == "789"
    assert params["sesskey"] == "XYZ"
    assert params["attempt"] == "2"


def test_parse_scorm_launch_url_missing_params() -> None:
    with pytest.raises(ValueError, match="缺少必要参数"):
        _parse_scorm_launch_url("https://vfua3ytp5.m.umu.cn/scorm/324/launch/456")
