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

    def download_to(self, url: str, path: str) -> int:
        """跟随重定向流式下载到 path，返回写入字节数。"""
        ...


class FakeTransport:
    """测试用：按 (method,url) 路由返回脚本化响应；downloads 路由提供二进制内容。"""

    def __init__(self, routes: dict | None = None, downloads: dict | None = None):
        self.routes = routes or {}
        self.downloads = downloads or {}  # {url: bytes}
        self.calls: list[tuple[str, str]] = []
        self._cookies: list[dict] = []

    def export_cookies(self) -> list[dict]:
        return list(self._cookies)

    def import_cookies(self, cookies: list[dict]) -> None:
        self._cookies = list(cookies)

    def request(self, method, url, *, data=None, headers=None, allow_redirects=True) -> Response:
        self.calls.append((method, url))
        key = (method, url)
        if key not in self.routes:
            raise TransportError(f"FakeTransport 未配置路由: {key}")
        return self.routes[key]

    def download_to(self, url: str, path: str) -> int:
        self.calls.append(("DL", url))
        if url not in self.downloads:
            raise TransportError(f"FakeTransport 未配置下载: {url}")
        data = self.downloads[url]
        with open(path, "wb") as f:
            f.write(data)
        return len(data)


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

    def export_cookies(self) -> list[dict]:
        return [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in self._sess.cookies.jar
        ]

    def import_cookies(self, cookies: list[dict]) -> None:
        for c in cookies:
            self._sess.cookies.set(
                c["name"], c["value"], domain=c.get("domain") or "", path=c.get("path") or "/"
            )

    def download_to(self, url: str, path: str) -> int:
        tmp = str(path) + ".part"
        written = 0
        try:
            r = self._sess.get(url, stream=True, allow_redirects=True, timeout=self._timeout)
            if r.status_code != 200:
                raise TransportError(f"download {url} -> {r.status_code}")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
        except TransportError:
            raise
        except Exception as e:  # noqa: BLE001
            raise TransportError(f"download {url} 失败: {type(e).__name__}") from e
        import os

        os.replace(tmp, path)  # 原子落盘
        return written
