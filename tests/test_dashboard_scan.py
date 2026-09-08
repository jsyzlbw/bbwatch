"""Dashboard scan attempts expose truthful outcomes without real subprocesses."""

import subprocess
from datetime import UTC, datetime, timedelta

import pytest

from bbwatch.dashboard.server import DashboardState, serve
from bbwatch.store import Store

NOW = "2026-09-08T01:00:00.000Z"
COMMAND = ["test-python", "-m", "bbwatch.cli", "scan"]


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0

    def __call__(self):
        return self.elapsed

    def now(self):
        instant = datetime(2026, 9, 8, 1, tzinfo=UTC)
        return (instant + timedelta(seconds=self.elapsed)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class FakeProcess:
    """Only simulate an owned child's observable lifecycle; never create a process."""

    def __init__(self, output, *, terminate_stops=True):
        self.output = output
        self.returncode = None
        self.terminate_stops = terminate_stops
        self.events = []
        self.pid = 4321

    def finish(self, returncode, output=""):
        if output:
            try:
                self.output.write(output.encode("utf-8"))
            except TypeError:
                self.output.write(output)
            self.output.flush()
        self.returncode = returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.events.append("terminate")
        if self.terminate_stops:
            self.returncode = -15

    def kill(self):
        self.events.append("kill")
        self.returncode = -9

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))
        if self.returncode is None:
            raise subprocess.TimeoutExpired(COMMAND, timeout)
        return self.returncode


class FakePopen:
    def __init__(self):
        self.calls = []
        self.processes = []
        self.error = None
        self.terminate_stops = True

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.error:
            raise self.error
        process = FakeProcess(kwargs["stdout"], terminate_stops=self.terminate_stops)
        self.processes.append(process)
        return process


@pytest.fixture
def job_harness():
    from bbwatch.dashboard.scan_job import ScanJob

    clock = FakeClock()
    popen = FakePopen()
    job = ScanJob(COMMAND, now_fn=clock.now, timeout=30, popen=popen, clock=clock)
    try:
        yield job, popen, clock
    finally:
        try:
            job.close()
        finally:
            # A deliberately broken cleanup implementation must not leak test files.
            for process in popen.processes:
                if not process.output.closed:
                    process.output.close()


def test_dashboard_without_scan_runner_reports_unavailable():
    state = DashboardState(store_factory=lambda: None, now_fn=lambda: NOW)

    result = state.trigger_scan()

    assert result["ok"] is False
    assert result["scan"]["state"] == "failed"
    assert result["scan"]["message"]


def test_new_scan_job_is_idle_without_spawning(job_harness):
    job, popen, _ = job_harness

    snapshot = job.snapshot()

    assert snapshot["state"] == "idle"
    assert snapshot["id"] is None
    assert snapshot["started_at"] is None
    assert snapshot["finished_at"] is None
    assert isinstance(snapshot["message"], str)
    assert popen.calls == []


def test_repeated_start_reuses_active_attempt_and_nonblocking_output(job_harness):
    job, popen, _ = job_harness

    first = job.start()
    second = job.start()

    assert first["state"] == second["state"] == "running"
    assert isinstance(first["id"], str) and first["id"]
    assert second["id"] == first["id"]
    assert first["started_at"] == NOW
    assert first["finished_at"] is None
    assert len(popen.calls) == 1
    command, options = popen.calls[0]
    assert command == COMMAND
    assert options["stdout"] != subprocess.PIPE
    assert options.get("stderr") != subprocess.PIPE
    assert hasattr(options["stdout"], "fileno")


def test_success_finishes_same_attempt_even_without_task_changes(job_harness):
    job, popen, clock = job_harness
    started = job.start()
    popen.processes[0].finish(0, "扫描完成：0 门在读课，新事件 0，已推送 0。\n")
    clock.elapsed = 12

    finished = job.snapshot()
    clock.elapsed = 20

    assert finished["state"] == "succeeded"
    assert finished["id"] == started["id"]
    assert finished["started_at"] == NOW
    assert finished["finished_at"] == "2026-09-08T01:00:12.000Z"
    assert job.snapshot() == finished
    assert popen.processes[0].output.closed


def test_partial_fetch_is_distinct_from_full_success(job_harness):
    job, popen, _ = job_harness
    job.start()
    popen.processes[0].finish(0, "扫描完成：2 门在读课。\n⚠ 部分维度失败 1 处：C1/columns\n")

    finished = job.snapshot()

    assert finished["state"] == "partial"
    assert finished["message"]
    assert finished["finished_at"] is not None


def test_failed_child_exposes_reason_and_allows_a_new_attempt(job_harness):
    job, popen, _ = job_harness
    started = job.start()
    popen.processes[0].finish(1, "错误：认证连续失败已熔断，请重新 bbwatch setup\n")

    failed = job.snapshot()
    retried = job.start()

    assert failed["state"] == "failed"
    assert failed["id"] == started["id"]
    assert "认证" in failed["message"]
    assert retried["state"] == "running"
    assert retried["id"] != failed["id"]
    assert len(popen.calls) == 2
    assert popen.processes[0].output.closed


def test_spawn_failure_has_terminal_status_and_releases_output(job_harness):
    job, popen, _ = job_harness
    popen.error = OSError("test runner unavailable")

    failed = job.start()

    assert failed["state"] == "failed"
    assert failed["id"]
    assert failed["finished_at"] is not None
    assert failed["message"]
    assert popen.calls[0][1]["stdout"].closed


def test_large_failed_output_produces_a_bounded_message(job_harness):
    job, popen, _ = job_harness
    job.start()
    popen.processes[0].finish(1, "诊断信息\n" * 30000)

    snapshot = job.snapshot()

    assert snapshot["state"] == "failed"
    assert 0 < len(snapshot["message"]) <= 8192


def test_unreadable_output_reports_failure_and_releases_finished_child(job_harness, monkeypatch):
    job, popen, _ = job_harness
    started = job.start()
    process = popen.processes[0]
    process.finish(0, "扫描完成。\n")

    def unreadable_result(*_args, **_kwargs):
        raise OSError("test output is unreadable")

    monkeypatch.setattr(process.output, "read", unreadable_result)

    finished = job.snapshot()

    assert finished["state"] == "failed"
    assert finished["id"] == started["id"]
    assert finished["finished_at"] is not None
    assert finished["message"]
    assert process.output.closed
    assert ("wait", 2) in process.events
    assert job.start()["state"] == "running"
    assert len(popen.calls) == 2


def test_timeout_terminates_then_kills_and_reaps_unresponsive_child(job_harness):
    job, popen, clock = job_harness
    popen.terminate_stops = False
    started = job.start()
    clock.elapsed = 29
    assert job.snapshot()["state"] == "running"
    clock.elapsed = 31

    timed_out = job.snapshot()

    assert timed_out["state"] == "failed"
    assert timed_out["id"] == started["id"]
    assert timed_out["message"]
    assert popen.processes[0].events == ["terminate", ("wait", 2), "kill", ("wait", 2)]
    assert popen.processes[0].output.closed
    events = list(popen.processes[0].events)
    job.snapshot()
    assert popen.processes[0].events == events


def test_closing_idle_job_is_idempotent_and_prevents_future_starts(job_harness):
    job, popen, _ = job_harness

    job.close()
    job.close()
    snapshot = job.start()

    assert snapshot["state"] != "running"
    assert popen.calls == []


def test_closing_active_job_stops_owned_child_and_prevents_restart(job_harness):
    job, popen, _ = job_harness
    job.start()

    job.close()
    job.close()
    closed = job.snapshot()
    job.start()

    assert closed["state"] == "failed"
    assert closed["finished_at"] is not None
    assert popen.processes[0].events == ["terminate", ("wait", 2)]
    assert popen.processes[0].output.closed
    assert len(popen.calls) == 1


def test_dashboard_status_reports_pre_scan_failure_without_refreshing_data(tmp_path, job_harness):
    job, popen, clock = job_harness
    db = tmp_path / "dashboard.db"
    store = Store(db)
    try:
        scan_id = store.start_scan("2026-08-23T01:00:00.000Z")
        store.finish_scan(scan_id, "ok", "2026-08-23T01:00:00.000Z")
    finally:
        store.close()
    state = DashboardState(store_factory=lambda: Store(db), now_fn=clock.now, scan_job=job)

    accepted = state.trigger_scan()
    active = state.tasks_payload()
    popen.processes[0].finish(1, "错误：账号或密码错误\n")
    finished = state.tasks_payload()

    assert accepted["ok"] is True
    assert active["scan"]["state"] == "running"
    assert active["scan"]["id"] == accepted["scan"]["id"]
    assert finished["scan"]["state"] == "failed"
    assert "账号" in finished["scan"]["message"]
    assert finished["last_scan"] == active["last_scan"] == "2026-08-23T01:00:00.000Z"
    assert finished["tasks"] == active["tasks"] == []


def test_dashboard_spawn_failure_is_not_acknowledged_as_started(job_harness):
    job, popen, clock = job_harness
    popen.error = OSError("test spawn failure")
    state = DashboardState(store_factory=lambda: None, now_fn=clock.now, scan_job=job)

    result = state.trigger_scan()

    assert result["ok"] is False
    assert result["scan"]["state"] == "failed"


def test_server_idle_actions_enforce_timeout_without_browser_requests(job_harness):
    job, popen, clock = job_harness
    state = DashboardState(store_factory=lambda: None, now_fn=clock.now, scan_job=job)
    httpd, _port = serve(state, port=0)
    try:
        accepted = state.trigger_scan()
        clock.elapsed = 31

        httpd.service_actions()

        # Check the child before snapshot(), which can itself enforce the deadline.
        assert popen.processes[0].events == ["terminate", ("wait", 2)]
        assert popen.processes[0].output.closed
        finished = job.snapshot()
        assert finished["state"] == "failed"
        assert finished["id"] == accepted["scan"]["id"]
    finally:
        httpd.server_close()


def inject_stop_failure(monkeypatch, process, failure):
    """Keep the fake child alive until the test releases the injected OS fault."""
    if failure == "terminate":
        def failed_terminate():
            process.events.append("terminate")
            raise OSError("test could not signal the child")

        monkeypatch.setattr(process, "terminate", failed_terminate)
        return

    monkeypatch.setattr(process, "terminate_stops", False)
    monkeypatch.setattr(process, "kill", lambda: process.events.append("kill"))
    if failure == "second_wait_oserror":
        original_wait = process.wait

        def failed_final_wait(timeout=None):
            if "kill" in process.events:
                process.events.append(("wait", timeout))
                raise OSError("test could not reap the child")
            return original_wait(timeout=timeout)

        monkeypatch.setattr(process, "wait", failed_final_wait)


@pytest.mark.parametrize("failure", ["terminate", "second_wait_timeout", "second_wait_oserror"])
def test_timeout_cleanup_failure_keeps_server_alive_and_prevents_duplicate(
    job_harness, monkeypatch, failure,
):
    job, popen, clock = job_harness
    state = DashboardState(store_factory=lambda: None, now_fn=clock.now, scan_job=job)
    httpd, _port = serve(state, port=0)
    try:
        accepted = state.trigger_scan()
        process = popen.processes[0]
        clock.elapsed = 31
        with monkeypatch.context() as fault:
            inject_stop_failure(fault, process, failure)

            httpd.service_actions()
            failed = job.snapshot()
            duplicate = state.trigger_scan()

            assert failed["state"] == "failed"
            assert failed["id"] == accepted["scan"]["id"]
            assert str(process.pid) in failed["message"]
            assert duplicate["ok"] is False
            assert duplicate["scan"]["id"] == accepted["scan"]["id"]
            assert len(popen.calls) == 1
            assert process.returncode is None
            assert not process.output.closed
    finally:
        httpd.server_close()


@pytest.mark.parametrize("failure", ["terminate", "second_wait_timeout", "second_wait_oserror"])
def test_failed_close_is_visible_and_can_retry_cleanup(job_harness, monkeypatch, failure):
    job, popen, _ = job_harness
    job.start()
    process = popen.processes[0]

    with monkeypatch.context() as fault:
        inject_stop_failure(fault, process, failure)

        with pytest.raises(RuntimeError, match=str(process.pid)):
            job.close()

        assert not process.output.closed
        assert process.returncode is None
        assert job.snapshot()["state"] == "failed"
        assert job.start()["state"] == "failed"
        assert len(popen.calls) == 1

    job.close()
    job.close()

    assert process.output.closed
    assert process.returncode is not None
    assert job.snapshot()["state"] == "failed"
    assert job.start()["state"] == "failed"
    assert len(popen.calls) == 1
