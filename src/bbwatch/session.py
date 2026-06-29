"""会话缓存 + 熔断（附录 C.3）。cookie 等价登录态：0600 原子写；复用前验证；
失效则(查熔断后)重登并缓存；连续凭据失败熔断，防学校账号锁定。"""
from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

from .auth import login as adfs_login
from .errors import AuthCircuitOpenError, CredentialError
from .secrets import Credentials


def save_session(transport, path) -> None:
    data = json.dumps(transport.export_cookies())
    tmp = str(path) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # 创建即受限
    try:
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)  # 原子


def load_session(transport, path) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    try:
        transport.import_cookies(json.loads(p.read_text()))
        return True
    except Exception:  # noqa: BLE001  损坏的缓存视为无
        return False


def ensure_session(
    transport,
    store,
    creds: Credentials,
    session_path,
    *,
    now: str,
    verify: Callable[[object], bool],
) -> None:
    """确保 transport 持有有效 BB 会话。优先复用缓存；失效则查熔断后重登并缓存。"""
    if load_session(transport, session_path):
        try:
            if verify(transport):
                return
        except Exception:  # noqa: BLE001  验证失败 → 当作需重登
            pass
    if store.auth_circuit_open(now):
        raise AuthCircuitOpenError("认证连续失败已熔断，请稍后重试或重新 bbwatch setup")
    try:
        adfs_login(transport, creds)
    except CredentialError:
        if store.record_auth_failure(now):
            raise AuthCircuitOpenError(
                "认证连续失败已熔断（疑似密码已变），请 bbwatch setup 重新录入"
            ) from None
        raise
    store.reset_auth_failures()
    save_session(transport, session_path)
