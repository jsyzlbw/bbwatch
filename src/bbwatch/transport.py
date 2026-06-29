from __future__ import annotations

import json as _json
from dataclasses import dataclass
from typing import Protocol

from .errors import TransportError


@dataclass
class Response:
    status: int
    headers: dict
    text: str
    url: str  # 最终(重定向后)URL

    def json(self):
        ct = self.headers.get("Content-Type", "") or self.headers.get("content-type", "")
        if "json" not in ct:
            return None
        try:
            return _json.loads(self.text)
        except ValueError:
            return None


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        data: dict | None = None,
        headers: dict | None = None,
        allow_redirects: bool = True,
    ) -> Response: ...


class FakeTransport:
    """测试用：按 (method,url) 路由返回脚本化响应。"""

    def __init__(self, routes: dict | None = None):
        self.routes = routes or {}
        self.calls: list[tuple[str, str]] = []

    def request(self, method, url, *, data=None, headers=None, allow_redirects=True) -> Response:
        self.calls.append((method, url))
        key = (method, url)
        if key not in self.routes:
            raise TransportError(f"FakeTransport 未配置路由: {key}")
        return self.routes[key]


class CurlCffiTransport:
    """真实传输：curl_cffi + 浏览器 TLS 指纹 + 环境代理 + 持久会话(cookie)。"""

    UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    def __init__(self, timeout: float = 30.0):
        from curl_cffi import requests as creq  # 延迟导入，便于无网测试

        self._sess = creq.Session(impersonate="chrome124")
        self._sess.headers["User-Agent"] = self.UA
        self._timeout = timeout

    def request(self, method, url, *, data=None, headers=None, allow_redirects=True) -> Response:
        try:
            r = self._sess.request(
                method,
                url,
                data=data,
                headers=headers,
                allow_redirects=allow_redirects,
                timeout=self._timeout,
            )
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"{method} {url} 失败: {type(e).__name__}") from e
        return Response(
            status=r.status_code, headers=dict(r.headers), text=r.text, url=str(r.url)
        )
