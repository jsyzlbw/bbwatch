"""A dashboard-owned scan process, observed by the HTTP server's event loop."""
from __future__ import annotations

import subprocess
import tempfile
import threading
import time
import uuid

from ..logging_setup import redact


class ScanJob:
    def __init__(self, command, *, now_fn, timeout=900, popen=None, clock=time.monotonic, env=None):
        self._command = list(command)
        self._now = now_fn
        self._timeout = timeout
        self._popen = popen or subprocess.Popen
        self._clock = clock
        self._env = env
        self._lock = threading.Lock()
        self._process = None
        self._output = None
        self._started = None
        self._closed = False
        self._cancel_pending = None
        self._status = {
            "id": None, "state": "idle", "started_at": None,
            "finished_at": None, "message": "",
        }

    def start(self) -> dict:
        with self._lock:
            self._refresh()
            if self._closed:
                return {**self._status, "state": "failed", "message": "看板已关闭，请重新打开。"}
            if self._process is not None:
                return dict(self._status)
            self._status = {
                "id": uuid.uuid4().hex, "state": "running", "started_at": self._now(),
                "finished_at": None, "message": "正在扫描 Blackboard，课程较多时可能需要几分钟。",
            }
            self._started = self._clock()
            try:
                # A file avoids a full stdout pipe blocking a long-running child.
                self._output = tempfile.TemporaryFile()  # noqa: SIM115 — owned until _finish/close
                self._process = self._popen(
                    self._command, stdin=subprocess.DEVNULL,
                    stdout=self._output, stderr=subprocess.STDOUT, env=self._env,
                )
            except Exception as exc:  # noqa: BLE001
                self._finish("failed", f"无法启动扫描：{redact(str(exc))}")
            return dict(self._status)

    def snapshot(self) -> dict:
        with self._lock:
            self._refresh()
            return dict(self._status)

    def _read_output(self) -> str:
        if self._output is None:
            return ""
        self._output.seek(0, 2)
        size = self._output.tell()
        self._output.seek(max(0, size - 8192))
        return redact(self._output.read(8192).decode("utf-8", errors="replace").strip())

    def _finish(self, state: str, message: str) -> None:
        self._status.update(state=state, finished_at=self._now(), message=message[:4000])
        self._process = None
        self._cancel_pending = None
        if self._output is not None:
            self._output.close()
            self._output = None

    def _stop(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def _cancel(self, message: str) -> bool:
        try:
            self._stop()
        except (OSError, subprocess.TimeoutExpired):
            # Keep ownership and block new scans until this child is reaped.
            self._cancel_pending = message
            self._status.update(
                state="failed", finished_at=self._now(),
                message=f"无法确认扫描进程（PID {self._process.pid}）已退出，已暂停新扫描。请检查该进程。",
            )
            return False
        self._finish("failed", message)
        return True

    def _refresh(self) -> None:
        if self._process is None:
            return
        result = self._process.poll()
        if result is None:
            if self._cancel_pending is None and self._clock() - self._started >= self._timeout:
                self._cancel("扫描超时，已停止本次扫描。请检查网络后重试。")
            return
        if self._cancel_pending is not None:
            self._cancel(self._cancel_pending)
            return
        self._process.wait(timeout=2)
        try:
            output = self._read_output()
        except (OSError, ValueError):
            self._finish("failed", "扫描进程已结束，但无法读取结果。请重新扫描确认。")
            return
        if result != 0:
            self._finish("failed", output or f"扫描未完成（退出码 {result}），请检查登录与网络。")
        elif "部分维度失败" in output:
            self._finish("partial", output)
        else:
            self._finish("succeeded", output or "扫描完成，清单已更新。")

    def close(self) -> None:
        with self._lock:
            if self._closed and self._process is None:
                return
            self._closed = True
            self._refresh()
            if self._process is not None and not self._cancel("看板已关闭，本次扫描已停止。"):
                raise RuntimeError(self._status["message"])
