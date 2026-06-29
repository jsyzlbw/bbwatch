"""运维：健康自检(doctor)与卸载清理(uninstall)。核心可单测(不依赖网络)。"""
from __future__ import annotations

import os
import shutil


def run_doctor(paths) -> str:
    checks: list[tuple[str, bool, str]] = []

    try:
        from .secrets import load_credentials

        load_credentials()
        checks.append(("钥匙串凭据", True, "已配置"))
    except Exception:  # noqa: BLE001
        checks.append(("钥匙串凭据", False, "未配置 → 运行 bbwatch setup"))

    checks.append(("会话缓存", paths.session_path.exists(), str(paths.session_path)))

    try:
        from .store import Store

        s = Store(paths.db_path)
        ver = s.schema_version()
        s.close()
        checks.append(("数据库可用", True, f"{paths.db_path} (schema v{ver})"))
    except Exception as e:  # noqa: BLE001
        checks.append(("数据库可用", False, type(e).__name__))

    checks.append(("osascript(桌面通知)", shutil.which("osascript") is not None, ""))

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    checks.append(("代理 HTTPS_PROXY", True, proxy or "未设置(直连)"))

    from .dashboard import find_port

    port = find_port(8765)
    checks.append(("清单端口可用", port != 0, str(port)))

    lines = [("✓" if ok else "✗") + f" {name}" + (f"：{detail}" if detail else "")
             for name, ok, detail in checks]
    healthy = all(ok for _, ok, _ in checks)
    lines.append("—— 一切就绪 ✓" if healthy else "—— 有项目需处理(见上 ✗)")
    return "\n".join(lines)


def run_uninstall(paths, *, purge_db: bool = False, purge_downloads_dir: str | None = None) -> str:
    from .secrets import clear_credentials

    done: list[str] = []
    clear_credentials()
    done.append("已清除钥匙串中的学校凭据")
    if paths.session_path.exists():
        paths.session_path.unlink()
        done.append("已删除会话缓存(cookie)")
    if purge_db and paths.db_path.exists():
        paths.db_path.unlink()
        for suffix in ("-wal", "-shm"):
            p = paths.db_path.with_name(paths.db_path.name + suffix)
            if p.exists():
                p.unlink()
        done.append("已删除本地数据库(任务/下载记录)")
    if purge_downloads_dir:
        import shutil as _sh
        from pathlib import Path

        d = Path(purge_downloads_dir).expanduser()
        if d.exists():
            _sh.rmtree(d, ignore_errors=True)
            done.append(f"已删除下载目录 {d}")
    return "\n".join(done)
