from __future__ import annotations

import json as _json
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from .errors import TransportError
from .proxy import resolve_proxy


def _safe_endpoint(url: str) -> str:
    """Only display a host/port, never URL credentials, paths or authorization parameters."""
    try:
        parsed = urlsplit(url if "://" in url else "//" + url)
        host = parsed.hostname or ""
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,253}", host):
            return "目标服务器"
        host = f"[{host}]" if ":" in host else host
        return f"{host}:{parsed.port}" if parsed.port is not None else host
    except ValueError:
        return "目标服务器"


def _transport_error_message(url: str, error: Exception) -> str:
    """Retain safe curl diagnostics without exposing the exception's raw URLs or secrets."""
    code = getattr(error, "code", None)
    if not isinstance(code, int) or isinstance(code, bool) or code <= 0:
        code = None
    details = type(error).__name__
    if code is not None:
        details += f"，curl {code}"
    summaries = {
        5: "无法解析代理地址，请检查代理设置后重试",
        6: "无法解析服务器地址，请检查网络与 DNS 设置后重试",
        7: "无法建立网络连接，请检查网络或代理设置后重试",
        28: "连接超时，请检查网络后重试",
        35: "安全连接建立失败，请检查网络或代理的 HTTPS 配置后重试",
        52: "服务器连接提前关闭，请稍后重试",
        55: "网络连接中断，请检查网络后重试",
        56: "网络连接中断，请检查网络后重试",
        60: "证书验证失败，请检查网络或代理的证书配置",
    }
    summary = summaries.get(code, "网络请求失败，请检查网络或代理设置后重试")
    if code == 7:
        # Only blame the proxy when curl identifies it in the actual connection failure.
        # Merely having a proxy environment variable is not evidence of proxy failure.
        match = re.search(
            r"\bover proxy\s+(\S+)\s+after\s+[\d.]+\s+ms:\s*Could not connect",
            str(error), re.IGNORECASE,
        )
        if match:
            proxy = _safe_endpoint(match.group(1))
            summary = f"无法连接代理（{proxy}），请启动或检查代理服务后重试"
    return f"访问 {_safe_endpoint(url)} 失败：{summary}（{details}）"


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

    def clear_cookies(self) -> None:
        self._cookies = []

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
    """真实传输：curl_cffi + 浏览器 TLS 指纹 + 自动代理 + 持久会话(cookie)。"""

    UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    def __init__(self, timeout: float = 30.0):
        from curl_cffi import requests as creq  # 延迟导入，便于无网测试

        self._sess = creq.Session(impersonate="chrome124")
        self._sess.headers["User-Agent"] = self.UA
        self._timeout = timeout

    def _proxy_for(self, url: str) -> str | None:
        from curl_cffi import CurlOpt

        proxy = resolve_proxy(url)
        # curl_cffi applies curl_options last. Empty PROXY explicitly selects direct
        # access; proxy=None alone would still inherit libcurl's environment proxy.
        self._sess.curl_options[CurlOpt.PROXY] = proxy
        return proxy or None

    def request(self, method, url, *, data=None, headers=None, allow_redirects=True) -> Response:
        try:
            proxy = self._proxy_for(url)
            r = self._sess.request(
                method,
                url,
                data=data,
                headers=headers,
                allow_redirects=allow_redirects,
                timeout=self._timeout,
                proxy=proxy,
            )
        except Exception as e:
            raise TransportError(_transport_error_message(url, e)) from e
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

    def clear_cookies(self) -> None:
        self._sess.cookies.clear()

    def download_to(self, url: str, path: str) -> int:
        tmp = str(path) + ".part"
        written = 0
        try:
            proxy = self._proxy_for(url)
            r = self._sess.get(
                url, stream=True, allow_redirects=True, timeout=self._timeout, proxy=proxy,
            )
            if r.status_code != 200:
                raise TransportError(f"下载 {_safe_endpoint(url)} 失败：HTTP {r.status_code}")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
        except TransportError:
            raise
        except Exception as e:
            raise TransportError(_transport_error_message(url, e)) from e
        import os

        os.replace(tmp, path)  # 原子落盘
        return written
