"""通知投递。成功→NOTIFIED（同条目不再 claim，去重）；失败→有限退避重投，
满 max_attempts→FAILED_NOTIFY 终态（附录 C M3）。"""
from __future__ import annotations

import json
import subprocess
from typing import Protocol


class Notifier(Protocol):
    def send(self, title: str, message: str) -> None:  # 失败抛异常
        ...


class MacNotifier:
    def send(self, title: str, message: str) -> None:
        script = (
            f"display notification {json.dumps(message)} with title {json.dumps(title)}"
        )
        subprocess.run(
            ["osascript", "-e", script], check=True, capture_output=True, timeout=10
        )


def deliver_pending(store, notifier: Notifier, now: str) -> int:
    sent = 0
    for ev in store.claim_pending_events(now):
        try:
            notifier.send(ev["title"], ev["detail"] or "")
            store.mark_notified(ev["id"], now)
            sent += 1
        except Exception:  # noqa: BLE001  失败→退避重投，不丢
            store.mark_failed(ev["id"], now, backoff_s=300)
    return sent
