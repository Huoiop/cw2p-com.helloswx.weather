"""基于标准库的 JSON 请求封装，避免为插件引入额外依赖。"""

import gzip
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

USER_AGENT = "ClassWidgets2-Weather/1.0 (+https://github.com/xuanxuan1231/cw2p-com.helloswx.weather)"

_SSL_CONTEXT = ssl.create_default_context()


class RequestError(Exception):
    """网络请求失败。"""


def build_url(template: str, params: dict[str, Any]) -> str:
    """把 ``{name}`` 占位符替换成 URL 编码后的值。"""
    url = template
    for key, value in params.items():
        token = "{" + key + "}"
        if token in url:
            url = url.replace(token, urllib.parse.quote(str(value), safe=",.-"))
    return url


def fetch_json(
    url: str, timeout: float = 10.0, headers: dict[str, str] | None = None
) -> Any:
    """发起 GET 请求并解析 JSON，返回原始结构（可能是 dict 或 list）。"""
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    if headers:
        request_headers.update(headers)

    request = urllib.request.Request(url, headers=request_headers)
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=_SSL_CONTEXT
        ) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as error:
        raise RequestError(f"HTTP {error.code}") from error
    except urllib.error.URLError as error:
        raise RequestError(str(error.reason)) from error
    except OSError as error:  # 超时等
        raise RequestError(str(error)) from error

    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as error:
        raise RequestError("响应不是合法的 JSON") from error


def get_json(
    url: str, timeout: float = 10.0, headers: dict[str, str] | None = None
) -> dict[str, Any]:
    """发起 GET 请求并解析 JSON 对象；失败时抛出 :class:`RequestError`。"""
    payload = fetch_json(url, timeout=timeout, headers=headers)
    if not isinstance(payload, dict):
        raise RequestError("响应格式不正确")
    return payload
