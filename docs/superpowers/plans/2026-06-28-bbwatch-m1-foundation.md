# bbwatch M1（工程骨架 + 认证 + 最小客户端）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 打通"登录 → 调 BB REST API → 拿到本学期课程"的纵切片，交付一个能真实登录并 `bbwatch whoami` 打印用户与课程数的 CLI。

**Architecture:** Python `src/` 布局包 `bbwatch`。传输层用 `curl_cffi`（浏览器 TLS 指纹，绕开实测 ADFS TLS 坑、尊重代理）封装为可注入的 `Transport`，使 auth / bbclient 可用 fixture 离线单测。auth 走 ADFS OAuth2 授权码流拿会话 cookie；bbclient 在会话上调 `/learn/api/public/v1/...`。凭据存 macOS 钥匙串。

**Tech Stack:** Python 3.11+，curl_cffi，beautifulsoup4（解析 ADFS 表单），keyring（钥匙串），pytest + ruff。详尽设计见 [详细设计 §1/§3/§6/§7](../specs/2026-06-28-bbwatch-detailed-design.md) 与 [实测依据](../specs/2026-06-28-bbwatch-design.md)（附录 A 端点表）。

**约束（来自详细设计 附录 C）：** 只发 GET（无附件下载，M1 不涉及）；凭据/cookie/`code=` 绝不进日志（统一脱敏）；连续凭据失败熔断、不无限重试（防学校账号锁定）；会话 cookie 存 `~/.bbwatch/session` 权限 0600 原子写。

---

## 文件结构

```
pyproject.toml                 项目与依赖、入口点 bbwatch=bbwatch.cli:main
src/bbwatch/
  __init__.py                  版本号
  config.py                    路径(~/.bbwatch)、AppPaths、目录权限 0700
  logging_setup.py             统一日志 + 出站脱敏过滤器
  secrets.py                   keyring 读写 ADFS 凭据
  transport.py                 Transport 协议 + CurlCffiTransport(代理/重定向/超时)
  models.py                    dataclass: Term, Course, Me
  errors.py                    异常类型: AuthError, CredentialError, TransportError, AuthCircuitOpenError
  auth.py                      ADFS OAuth2 登录、会话缓存、熔断
  bbclient.py                  BbClient: me / terms / courses + 分页
  cli.py                       argparse: setup / whoami
tests/
  conftest.py                  fixtures 加载、FakeTransport
  fixtures/                    录制的脱敏 JSON/HTML 响应
  test_config.py
  test_logging_redaction.py
  test_transport.py
  test_auth.py
  test_bbclient.py
  test_cli.py
```

每个模块单一职责，auth/bbclient 通过注入 `Transport` 与 fixture 解耦真实网络。

---

## Task 1: 工程骨架

**Files:**
- Create: `pyproject.toml`, `src/bbwatch/__init__.py`, `tests/conftest.py`, `tests/test_smoke.py`

- [ ] **Step 1: 写 pyproject.toml**

```toml
[project]
name = "bbwatch"
version = "0.1.0"
description = "CUHK-SZ Blackboard 任务监控与课件下载 (Claude Code 插件引擎)"
requires-python = ">=3.11"
dependencies = [
    "curl_cffi>=0.7",
    "beautifulsoup4>=4.12",
    "keyring>=24",
]

[project.optional-dependencies]
dev = ["pytest>=8", "ruff>=0.5"]

[project.scripts]
bbwatch = "bbwatch.cli:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: 写 `src/bbwatch/__init__.py`**

```python
__version__ = "0.1.0"
```

- [ ] **Step 3: 写冒烟测试 `tests/test_smoke.py`**

```python
import bbwatch

def test_version():
    assert bbwatch.__version__ == "0.1.0"
```

- [ ] **Step 4: 建虚拟环境并安装（开发模式）**

Run:
```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```
Expected: 安装成功，`curl_cffi`/`keyring`/`pytest` 就位。

- [ ] **Step 5: 跑测试**

Run: `.venv/bin/pytest tests/test_smoke.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add pyproject.toml src/bbwatch/__init__.py tests/test_smoke.py
git commit -m "feat(m1): 工程骨架与冒烟测试"
```

---

## Task 2: 配置与路径

**Files:**
- Create: `src/bbwatch/config.py`, `tests/test_config.py`

- [ ] **Step 1: 写失败测试 `tests/test_config.py`**

```python
import os, stat
from bbwatch.config import AppPaths

def test_apppaths_uses_env_root(tmp_path):
    p = AppPaths(root=tmp_path / ".bbwatch")
    assert p.db_path.name == "state.db"
    assert p.session_path.name == "session"
    assert str(p.root) in str(p.db_path)

def test_ensure_dirs_sets_0700(tmp_path):
    p = AppPaths(root=tmp_path / ".bbwatch")
    p.ensure_dirs()
    mode = stat.S_IMODE(os.stat(p.root).st_mode)
    assert mode == 0o700
```

- [ ] **Step 2: 跑测试看失败**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL（`No module named bbwatch.config`）。

- [ ] **Step 3: 实现 `src/bbwatch/config.py`**

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import os

def default_root() -> Path:
    return Path(os.environ.get("BBWATCH_HOME", str(Path.home() / ".bbwatch")))

@dataclass
class AppPaths:
    root: Path = field(default_factory=default_root)

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    @property
    def db_path(self) -> Path:
        return self.root / "state.db"

    @property
    def session_path(self) -> Path:
        return self.root / "session"

    @property
    def log_path(self) -> Path:
        return self.root / "bbwatch.log"

    def ensure_dirs(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
```

- [ ] **Step 4: 跑测试看通过**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/bbwatch/config.py tests/test_config.py
git commit -m "feat(m1): 配置与路径(0700 目录)"
```

---

## Task 3: 日志与脱敏

**Files:**
- Create: `src/bbwatch/logging_setup.py`, `tests/test_logging_redaction.py`

- [ ] **Step 1: 写失败测试 `tests/test_logging_redaction.py`**

```python
from bbwatch.logging_setup import redact

def test_redacts_password_query():
    s = "POST .../authorize?code=abc123&UserName=120000000&Password=secret"
    out = redact(s)
    assert "secret" not in out
    assert "abc123" not in out
    assert "120000000" not in out

def test_redacts_cookie_and_token():
    s = "Cookie: JSESSIONID=ZZZ; s_session_id=YYY  Authorization: Bearer tok"
    out = redact(s)
    assert "ZZZ" not in out and "YYY" not in out and "tok" not in out
```

- [ ] **Step 2: 跑测试看失败**

Run: `.venv/bin/pytest tests/test_logging_redaction.py -v`
Expected: FAIL（无模块）。

- [ ] **Step 3: 实现 `src/bbwatch/logging_setup.py`**

```python
from __future__ import annotations
import logging, re
from pathlib import Path

_PATTERNS = [
    re.compile(r"(?i)(password=)[^&\s]+"),
    re.compile(r"(?i)(code=)[^&\s]+"),
    re.compile(r"(?i)(username=)[^&\s]+"),
    re.compile(r"(?i)(authorization:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(cookie:\s*)[^\n]+"),
    re.compile(r"(JSESSIONID=)[^;\s]+"),
    re.compile(r"(s_session_id=)[^;\s]+"),
    re.compile(r"\b1\d{8}\b"),            # 学号
]

def redact(text: str) -> str:
    out = text
    for p in _PATTERNS:
        if p.groups:
            out = p.sub(lambda m: m.group(1) + "***", out)
        else:
            out = p.sub("***", out)
    return out

class _RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True

def setup_logging(log_path: Path, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("bbwatch")
    logger.setLevel(level)
    if not logger.handlers:
        log_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fh = logging.FileHandler(log_path)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        fh.addFilter(_RedactFilter())
        logger.addHandler(fh)
    return logger
```

- [ ] **Step 4: 跑测试看通过**

Run: `.venv/bin/pytest tests/test_logging_redaction.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/bbwatch/logging_setup.py tests/test_logging_redaction.py
git commit -m "feat(m1): 日志与出站脱敏"
```

---

## Task 4: 异常类型与凭据存储

**Files:**
- Create: `src/bbwatch/errors.py`, `src/bbwatch/secrets.py`, `tests/test_secrets.py`

- [ ] **Step 1: 实现 `src/bbwatch/errors.py`（无测试，纯定义）**

```python
class BbwatchError(Exception): ...
class TransportError(BbwatchError): ...
class AuthError(BbwatchError): ...
class CredentialError(AuthError): ...            # 凭据无效/缺失
class SessionRefreshError(AuthError): ...        # 重登+重放仍失败
class AuthCircuitOpenError(AuthError): ...       # 熔断期
```

- [ ] **Step 2: 写失败测试 `tests/test_secrets.py`（用内存假 keyring）**

```python
import keyring
from keyring.backend import KeyringBackend
from bbwatch import secrets
from bbwatch.errors import CredentialError

class MemKeyring(KeyringBackend):
    priority = 1
    def __init__(self): self._d = {}
    def get_password(self, s, u): return self._d.get((s, u))
    def set_password(self, s, u, p): self._d[(s, u)] = p
    def delete_password(self, s, u): self._d.pop((s, u), None)

def setup_function():
    keyring.set_keyring(MemKeyring())

def test_store_and_load():
    secrets.store_credentials("120000000@link.cuhk.edu.cn", "pw")
    c = secrets.load_credentials()
    assert c.username.endswith("@link.cuhk.edu.cn")
    assert c.password == "pw"

def test_load_missing_raises():
    secrets.clear_credentials()
    try:
        secrets.load_credentials()
        assert False
    except CredentialError:
        pass
```

- [ ] **Step 3: 跑测试看失败**

Run: `.venv/bin/pytest tests/test_secrets.py -v`
Expected: FAIL（无模块）。

- [ ] **Step 4: 实现 `src/bbwatch/secrets.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
import keyring
from .errors import CredentialError

SERVICE = "bbwatch"
_USER_KEY = "__username__"

@dataclass
class Credentials:
    username: str
    password: str

def store_credentials(username: str, password: str) -> None:
    keyring.set_password(SERVICE, _USER_KEY, username)
    keyring.set_password(SERVICE, username, password)

def load_credentials() -> Credentials:
    username = keyring.get_password(SERVICE, _USER_KEY)
    if not username:
        raise CredentialError("未找到凭据，请先运行 bbwatch setup")
    password = keyring.get_password(SERVICE, username)
    if not password:
        raise CredentialError("凭据不完整，请重新运行 bbwatch setup")
    return Credentials(username=username, password=password)

def clear_credentials() -> None:
    username = keyring.get_password(SERVICE, _USER_KEY)
    if username:
        try: keyring.delete_password(SERVICE, username)
        except keyring.errors.PasswordDeleteError: pass
    try: keyring.delete_password(SERVICE, _USER_KEY)
    except keyring.errors.PasswordDeleteError: pass
```

- [ ] **Step 5: 跑测试看通过**

Run: `.venv/bin/pytest tests/test_secrets.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/bbwatch/errors.py src/bbwatch/secrets.py tests/test_secrets.py
git commit -m "feat(m1): 异常类型与钥匙串凭据存储"
```

---

## Task 5: 传输层（Transport 协议 + curl_cffi 实现）

**Files:**
- Create: `src/bbwatch/transport.py`, `tests/test_transport.py`

- [ ] **Step 1: 写失败测试 `tests/test_transport.py`（只测协议契约与 FakeTransport，不打网络）**

```python
from bbwatch.transport import Response, FakeTransport

def test_fake_transport_serves_scripted_responses():
    t = FakeTransport(routes={
        ("GET", "https://x/api"): Response(200, {"Content-Type": "application/json"}, '{"a":1}', "https://x/api"),
    })
    r = t.request("GET", "https://x/api")
    assert r.status == 200
    assert r.json() == {"a": 1}

def test_response_json_none_on_nonjson():
    r = Response(200, {"Content-Type": "text/html"}, "<html>", "u")
    assert r.json() is None
```

- [ ] **Step 2: 跑测试看失败**

Run: `.venv/bin/pytest tests/test_transport.py -v`
Expected: FAIL（无模块）。

- [ ] **Step 3: 实现 `src/bbwatch/transport.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
import json as _json
from .errors import TransportError

@dataclass
class Response:
    status: int
    headers: dict
    text: str
    url: str            # 最终(重定向后)URL
    def json(self):
        ct = self.headers.get("Content-Type", "") or self.headers.get("content-type", "")
        if "json" not in ct:
            return None
        try: return _json.loads(self.text)
        except ValueError: return None

class Transport(Protocol):
    def request(self, method: str, url: str, *, data: dict | None = None,
                headers: dict | None = None, allow_redirects: bool = True) -> Response: ...

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
    UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    def __init__(self, timeout: float = 30.0):
        from curl_cffi import requests as creq   # 延迟导入，便于无网测试
        self._sess = creq.Session(impersonate="chrome124")
        self._sess.headers["User-Agent"] = self.UA
        self._timeout = timeout
    def request(self, method, url, *, data=None, headers=None, allow_redirects=True) -> Response:
        try:
            r = self._sess.request(method, url, data=data, headers=headers,
                                   allow_redirects=allow_redirects, timeout=self._timeout)
        except Exception as e:                  # noqa: BLE001
            raise TransportError(f"{method} {url} 失败: {type(e).__name__}") from e
        return Response(status=r.status_code, headers=dict(r.headers),
                        text=r.text, url=str(r.url))
```

> 说明：`curl_cffi` 默认读取环境代理（`HTTP(S)_PROXY`），满足"尊重本机代理"。`impersonate="chrome124"` 提供浏览器 TLS 指纹，规避实测 ADFS 握手失败。子进程 curl 回退留到后续按需补（附录 C.3）。

- [ ] **Step 4: 跑测试看通过**

Run: `.venv/bin/pytest tests/test_transport.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/bbwatch/transport.py tests/test_transport.py
git commit -m "feat(m1): 传输层(Transport 协议 + curl_cffi 实现 + FakeTransport)"
```

---

## Task 6: 数据模型

**Files:**
- Create: `src/bbwatch/models.py`, `tests/test_models.py`

- [ ] **Step 1: 写失败测试 `tests/test_models.py`**

```python
from bbwatch.models import Course, Term, Me

def test_course_is_active():
    c = Course(id="_1_1", course_id="MAT", name="X", term_id="_t_1",
               role="Student", availability="Yes", ultra_status="Classic")
    assert c.is_active is True
    c2 = Course(id="_2_1", course_id="Y", name="Y", term_id="_t_1",
                role="Student", availability="No", ultra_status="Classic")
    assert c2.is_active is False
    c3 = Course(id="_3_1", course_id="Z", name="Z", term_id="_t_1",
                role="Instructor", availability="Yes", ultra_status="Classic")
    assert c3.is_active is False
```

- [ ] **Step 2: 跑测试看失败**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL。

- [ ] **Step 3: 实现 `src/bbwatch/models.py`**

```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class Me:
    id: str
    user_name: str
    given_name: str | None = None

@dataclass(frozen=True)
class Term:
    id: str
    name: str | None

@dataclass(frozen=True)
class Course:
    id: str               # 内部 id, 如 _17236_1
    course_id: str        # 人类可读, 如 MAT3007:Optimization_L01
    name: str
    term_id: str | None
    role: str             # courseRoleId
    availability: str     # Yes / No / Term
    ultra_status: str
    @property
    def is_active(self) -> bool:
        return self.role == "Student" and self.availability in ("Yes", "Term")
```

- [ ] **Step 4: 跑测试看通过**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/bbwatch/models.py tests/test_models.py
git commit -m "feat(m1): 数据模型 Course/Term/Me"
```

---

## Task 7: 认证（ADFS OAuth2 登录）

**Files:**
- Create: `src/bbwatch/auth.py`, `tests/fixtures/adfs_form.html`, `tests/test_auth.py`

- [ ] **Step 1: 准备 fixture `tests/fixtures/adfs_form.html`**

```html
<html><body>
<form method="post" action="/adfs/oauth2/authorize?client_id=x&response_type=code">
  <input type="text" name="UserName" value=""/>
  <input type="password" name="Password" value=""/>
  <input type="hidden" name="Kmsi" value="true"/>
</form></body></html>
```

- [ ] **Step 2: 写失败测试 `tests/test_auth.py`（用 FakeTransport 串脚本，验证流程与字段，不打网络）**

```python
from pathlib import Path
from bbwatch.transport import Response, FakeTransport
from bbwatch.auth import parse_adfs_form, build_login_post, AUTHORIZE_URL

FIX = Path(__file__).parent / "fixtures"

def test_parse_adfs_form_extracts_action_and_fields():
    html = (FIX / "adfs_form.html").read_text()
    action, fields = parse_adfs_form(html, base="https://sts.cuhk.edu.cn/")
    assert action.startswith("https://sts.cuhk.edu.cn/adfs/oauth2/authorize")
    assert "UserName" in fields and "Password" in fields and fields["Kmsi"] == "true"

def test_build_login_post_fills_credentials():
    fields = {"UserName": "", "Password": "", "Kmsi": "true"}
    data = build_login_post(fields, "u@link.cuhk.edu.cn", "pw")
    assert data["UserName"] == "u@link.cuhk.edu.cn"
    assert data["Password"] == "pw"
    assert data["Kmsi"] == "true"

def test_authorize_url_has_client_and_redirect():
    assert "oauth2/authorize" in AUTHORIZE_URL
    assert "client_id=4b71b947-7b0d-4611-b47e-0ec37aabfd5e" in AUTHORIZE_URL
    assert "getCode" in AUTHORIZE_URL
```

- [ ] **Step 3: 跑测试看失败**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: FAIL（无模块）。

- [ ] **Step 4: 实现 `src/bbwatch/auth.py`**

```python
from __future__ import annotations
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from .transport import Transport, Response
from .errors import AuthError, CredentialError
from .secrets import Credentials

CLIENT_ID = "4b71b947-7b0d-4611-b47e-0ec37aabfd5e"
REDIRECT_URI = "https://bb.cuhk.edu.cn/webapps/bb-SSOIntegrationOAuth2-BBLEARN/authValidate/getCode"
AUTHORIZE_URL = (
    "https://sts.cuhk.edu.cn/adfs/oauth2/authorize?response_type=code"
    f"&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"
)
BB_HOST = "bb.cuhk.edu.cn"

def parse_adfs_form(html: str, base: str) -> tuple[str, dict]:
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if not form:
        raise AuthError("ADFS 登录页未找到表单（页面结构可能已变）")
    action = urljoin(base, form.get("action") or "")
    fields = {i.get("name"): (i.get("value") or "")
              for i in form.find_all("input") if i.get("name")}
    return action, fields

def build_login_post(fields: dict, username: str, password: str) -> dict:
    data = dict(fields)
    data["UserName"] = username
    data["Password"] = password
    return data

def login(transport: Transport, creds: Credentials) -> None:
    """走完 ADFS OAuth2，使 transport 的会话持有 BB cookie。失败抛 AuthError。"""
    r1 = transport.request("GET", AUTHORIZE_URL)
    action, fields = parse_adfs_form(r1.text, base=r1.url or AUTHORIZE_URL)
    data = build_login_post(fields, creds.username, creds.password)
    r2 = transport.request("POST", action, data=data, allow_redirects=True)
    if urlparse(r2.url).netloc != BB_HOST:
        low = r2.text.lower()
        if any(k in low for k in ("incorrect", "invalid", "try again", "错误")):
            raise CredentialError("ADFS 拒绝：账号或密码错误")
        raise AuthError(f"登录未落到 BB（最终 URL host={urlparse(r2.url).netloc}）")
```

> 说明：会话缓存(写 0600 session 文件)、`SessionRefreshError`/熔断在 M2 与 store 一并接入（附录 C.3 已定决议）；M1 先打通"能登进去"。

- [ ] **Step 5: 跑测试看通过**

Run: `.venv/bin/pytest tests/test_auth.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/bbwatch/auth.py tests/fixtures/adfs_form.html tests/test_auth.py
git commit -m "feat(m1): ADFS OAuth2 登录(表单解析+流程)"
```

---

## Task 8: BB 客户端（me / terms / courses + 分页）

**Files:**
- Create: `src/bbwatch/bbclient.py`, `tests/fixtures/users_me.json`, `tests/fixtures/courses_p1.json`, `tests/fixtures/courses_p2.json`, `tests/test_bbclient.py`

- [ ] **Step 1: 准备 fixtures**

`tests/fixtures/users_me.json`:
```json
{"id":"_10000_1","userName":"120000000","name":{"given":"示例同学"}}
```
`tests/fixtures/courses_p1.json`（含 `paging.nextPage`，验证翻页）:
```json
{"results":[
  {"id":"_m1_1","userId":"_10000_1","courseId":"_17236_1","courseRoleId":"Student",
   "course":{"id":"_17236_1","courseId":"MAT3007:Optimization_L01","name":"MAT3007:Optimization_L01",
   "termId":"_424_1","ultraStatus":"Classic","availability":{"available":"Yes"}}}
],"paging":{"nextPage":"/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=1&offset=1"}}
```
`tests/fixtures/courses_p2.json`（末页，无 paging）:
```json
{"results":[
  {"id":"_m2_1","userId":"_10000_1","courseId":"_9_1","courseRoleId":"Student",
   "course":{"id":"_9_1","courseId":"PED1201:Badminton","name":"PED1201:Badminton",
   "termId":"_424_1","ultraStatus":"Classic","availability":{"available":"No"}}}
]}
```

- [ ] **Step 2: 写失败测试 `tests/test_bbclient.py`**

```python
import json
from pathlib import Path
from bbwatch.transport import Response, FakeTransport
from bbwatch.bbclient import BbClient, BB

FIX = Path(__file__).parent / "fixtures"
def _resp(name):
    return Response(200, {"Content-Type": "application/json"}, (FIX/name).read_text(), "u")

def test_get_me():
    t = FakeTransport({("GET", BB + "/learn/api/public/v1/users/me"): _resp("users_me.json")})
    me = BbClient(t).get_me()
    assert me.id == "_10000_1" and me.given_name == "示例同学"

def test_list_courses_follows_pagination():
    base = BB + "/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=100"
    nxt = BB + "/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=1&offset=1"
    t = FakeTransport({("GET", base): _resp("courses_p1.json"),
                       ("GET", nxt): _resp("courses_p2.json")})
    courses = BbClient(t).list_courses("_10000_1")
    assert len(courses) == 2
    assert {c.course_id for c in courses} == {"MAT3007:Optimization_L01", "PED1201:Badminton"}
    active = [c for c in courses if c.is_active]
    assert len(active) == 1 and active[0].course_id == "MAT3007:Optimization_L01"
```

- [ ] **Step 3: 跑测试看失败**

Run: `.venv/bin/pytest tests/test_bbclient.py -v`
Expected: FAIL（无模块）。

- [ ] **Step 4: 实现 `src/bbwatch/bbclient.py`**

```python
from __future__ import annotations
from urllib.parse import urljoin
from .transport import Transport
from .models import Me, Term, Course
from .errors import TransportError

BB = "https://bb.cuhk.edu.cn"
API = "/learn/api/public/v1"

class BbClient:
    def __init__(self, transport: Transport):
        self._t = transport

    def _get_json(self, path: str) -> dict:
        url = path if path.startswith("http") else BB + path
        r = self._t.request("GET", url, headers={"Accept": "application/json"})
        if r.status != 200:
            raise TransportError(f"GET {path} -> {r.status}")
        j = r.json()
        if j is None:
            raise TransportError(f"GET {path} 非 JSON 响应")
        return j

    def _paginate(self, first_path: str) -> list[dict]:
        """跟随 paging.nextPage 取全集；任一页失败上抛。带不前进/runaway 守卫。"""
        results: list[dict] = []
        path = first_path
        seen_urls: set[str] = set()
        guard = 0
        while path:
            if path in seen_urls or guard > 10000:
                raise TransportError(f"分页异常(自指或过长): {path}")
            seen_urls.add(path); guard += 1
            j = self._get_json(path)
            results.extend(j.get("results", []))
            nxt = (j.get("paging") or {}).get("nextPage")
            if not nxt or nxt == path:
                break
            path = nxt if nxt.startswith("http") else urljoin(BB, nxt)
        return results

    def get_me(self) -> Me:
        j = self._get_json(f"{API}/users/me")
        return Me(id=j["id"], user_name=j.get("userName", ""),
                  given_name=(j.get("name") or {}).get("given"))

    def list_terms(self) -> dict:
        rows = self._paginate(f"{API}/terms?limit=100")
        return {t["id"]: t.get("name") for t in rows}

    def list_courses(self, uid: str) -> list[Course]:
        rows = self._paginate(f"{API}/users/{uid}/courses?expand=course&limit=100")
        out: list[Course] = []
        for it in rows:
            c = it.get("course") or {}
            out.append(Course(
                id=c.get("id") or it.get("courseId"),
                course_id=c.get("courseId") or "",
                name=c.get("name") or "",
                term_id=c.get("termId"),
                role=it.get("courseRoleId") or "",
                availability=(c.get("availability") or {}).get("available") or "",
                ultra_status=c.get("ultraStatus") or "",
            ))
        return out
```

- [ ] **Step 5: 跑测试看通过**

Run: `.venv/bin/pytest tests/test_bbclient.py -v`
Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/bbwatch/bbclient.py tests/fixtures/users_me.json tests/fixtures/courses_p1.json tests/fixtures/courses_p2.json tests/test_bbclient.py
git commit -m "feat(m1): BB 客户端(me/terms/courses + 分页)"
```

---

## Task 9: CLI（setup / whoami）

**Files:**
- Create: `src/bbwatch/cli.py`, `tests/test_cli.py`

- [ ] **Step 1: 写失败测试 `tests/test_cli.py`（注入 FakeTransport + 假凭据，验证 whoami 装配逻辑）**

```python
from bbwatch.transport import Response, FakeTransport
from bbwatch.bbclient import BB
from bbwatch.cli import run_whoami
from bbwatch.secrets import Credentials
from pathlib import Path
import json

FIX = Path(__file__).parent / "fixtures"
def _r(name): return Response(200, {"Content-Type":"application/json"}, (FIX/name).read_text(), "u")

def test_run_whoami_composes_summary(monkeypatch):
    # 不真正登录：login 注入为 no-op；transport 提供 me + courses
    base = BB + "/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=100"
    nxt = BB + "/learn/api/public/v1/users/_10000_1/courses?expand=course&limit=1&offset=1"
    t = FakeTransport({
        ("GET", BB + "/learn/api/public/v1/users/me"): _r("users_me.json"),
        ("GET", base): _r("courses_p1.json"),
        ("GET", nxt): _r("courses_p2.json"),
    })
    summary = run_whoami(transport=t, creds=Credentials("u@link.cuhk.edu.cn","pw"),
                         login_fn=lambda tr, c: None)
    assert "示例同学" in summary
    assert "_10000_1" in summary
    assert "在读 1" in summary    # 2 门里 1 门在读
```

- [ ] **Step 2: 跑测试看失败**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL（无模块）。

- [ ] **Step 3: 实现 `src/bbwatch/cli.py`**

```python
from __future__ import annotations
import argparse, getpass, sys
from .transport import Transport, CurlCffiTransport
from .secrets import Credentials, store_credentials, load_credentials
from .auth import login as adfs_login
from .bbclient import BbClient

def run_whoami(transport: Transport, creds: Credentials, login_fn=adfs_login) -> str:
    login_fn(transport, creds)
    client = BbClient(transport)
    me = client.get_me()
    courses = client.list_courses(me.id)
    active = [c for c in courses if c.is_active]
    name = me.given_name or me.user_name
    return (f"已登录：{name}（uid={me.id}）\n"
            f"课程：共 {len(courses)} 门，在读 {len(active)} 门")

def cmd_setup(_args) -> int:
    username = input("学校账号(形如 学号@link.cuhk.edu.cn): ").strip()
    password = getpass.getpass("密码（输入不回显）: ")
    store_credentials(username, password)
    print("已存入 macOS 钥匙串。可运行 bbwatch whoami 验证。")
    return 0

def cmd_whoami(_args) -> int:
    creds = load_credentials()
    print(run_whoami(CurlCffiTransport(), creds))
    return 0

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="bbwatch")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="录入并保存学校账号密码到钥匙串").set_defaults(fn=cmd_setup)
    sub.add_parser("whoami", help="登录并打印身份与课程数").set_defaults(fn=cmd_whoami)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:                       # noqa: BLE001
        print(f"错误：{e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试看通过**

Run: `.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS。

- [ ] **Step 5: 全量测试 + lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check src tests`
Expected: 全部 PASS，ruff 无错。

- [ ] **Step 6: 提交**

```bash
git add src/bbwatch/cli.py tests/test_cli.py
git commit -m "feat(m1): CLI(setup/whoami)"
```

---

## Task 10: 真实端到端验证（人工，一次）

> 这一步用真实账号验证整条链路，确认 M1 达成。**仅本人本机执行，凭据只走钥匙串。**

- [ ] **Step 1: 录入凭据**

Run: `.venv/bin/bbwatch setup`（按提示输入 学号@link.cuhk.edu.cn 与密码）

- [ ] **Step 2: 真实登录并打印**

Run: `.venv/bin/bbwatch whoami`
Expected: 输出 `已登录：示例同学（uid=_10000_1）` 与 `课程：共 19 门，在读 17 门`（数字以实际为准）。

- [ ] **Step 3: 确认日志无敏感信息**

Run: `grep -nE "Lbw|Password|JSESSIONID|code=" ~/.bbwatch/bbwatch.log || echo OK-无敏感`
Expected: `OK-无敏感`。

- [ ] **Step 4: 标记里程碑完成（合并/PR 由 finishing-a-development-branch 决定）**

---

## Self-Review（计划自查）

- **Spec 覆盖**：M1 对应详细设计 §3(结构)/§6(认证)/§7(客户端 me/terms/courses)/附录 C.3(凭据安全、脱敏)。store/diff/scanner/notifier/dashboard/下载/插件外壳属 M2+，本计划不含（范围正确）。
- **占位符**：无 TBD；每个代码步骤含完整可运行代码。
- **类型一致性**：`Transport.request` 签名（`method,url,data,headers,allow_redirects`）在 auth/bbclient/cli 一致；`Response.json()` 全程统一；`Course.is_active` 在 models 定义并在 bbclient/cli 使用；`Credentials(username,password)` 全程一致；`login(transport, creds)` 签名与 cli `login_fn` 注入一致。
- **TDD/提交**：每任务先红后绿、独立提交。
- **已知后延（M2 接入，附录 C 已有决议）**：会话 cookie 落盘缓存与复用、`SessionRefreshError`/请求级重放、认证熔断持久化、子进程 curl 回退、429 处理。M1 的 `auth.login` 仅保证"能登入"，未做缓存与熔断——M2 第一项即补齐，不影响 M1 验收。
