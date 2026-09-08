import sqlite3

import pytest

from bbwatch import scanner
from bbwatch.models import Column, ColumnStatus, Course
from bbwatch.store import Store

PREVIOUS = "2026-09-07T04:00:00.000Z"
STARTED = "2026-09-08T04:00:00.000Z"
FINISHED = "2026-09-08T04:03:00.000Z"
UID = "fictional_user"


def course(cid="fictional_course"):
    return Course(
        id=cid, course_id="DEMO101", name="示例课程", term_id="fictional_term",
        role="Student", availability="Yes", ultra_status="Classic",
    )


class FixtureClient:
    def __init__(self, *, courses=None, columns=None, failure=None):
        self.courses = [course()] if courses is None else courses
        self.columns = [] if columns is None else columns
        self.failure = failure

    def list_courses(self, uid):
        if self.failure == "listing":
            raise RuntimeError("fictional listing failure")
        return self.courses

    def list_columns(self, cid):
        if self.failure == "columns":
            raise RuntimeError("fictional columns failure")
        return self.columns

    def get_column_status(self, cid, colid, uid):
        return ColumnStatus("None")

    def list_announcements(self, cid):
        return []

    def walk_contents(self, cid):
        return iter(())


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "fictional.db")
    try:
        yield value
    finally:
        value.close()


def latest_run(store):
    return dict(store._conn.execute("SELECT * FROM scan_run ORDER BY id DESC LIMIT 1").fetchone())


@pytest.mark.parametrize("failure", ["listing", "clone", "write"])
def test_scan_failure_is_finalized_and_reraised(store, failure):
    client = FixtureClient(
        courses=[course(), course("fictional_other")],
        columns=[Column("fictional_column", "示例作业", FINISHED)],
        failure=failure,
    )
    options = {}
    error = RuntimeError
    if failure == "clone":
        def fail_clone():
            raise RuntimeError("fictional clone failure")
        options.update(fetch_workers=2, client_factory=fail_clone)
    elif failure == "write":
        store._conn.execute(
            "CREATE TRIGGER reject_fixture BEFORE INSERT ON seen_entity "
            "BEGIN SELECT RAISE(ABORT, 'fictional write failure'); END"
        )
        error = sqlite3.IntegrityError

    with pytest.raises(error, match=f"fictional {failure} failure"):
        scanner.scan(client, store, UID, now=STARTED, **options)

    row = latest_run(store)
    assert row["status"] == "failed"
    assert row["started_at"] == STARTED
    assert row["finished_at"] is not None
    assert store.last_scan_time() is None


def test_scan_completion_clock_does_not_retime_events_or_snapshots(store):
    store.establish_baseline("fictional_course", "columns", PREVIOUS)
    client = FixtureClient(columns=[Column("fictional_column", "示例作业", FINISHED)])

    result = scanner.scan(
        client, store, UID, now=STARTED, finished_at_fn=lambda: FINISHED,
    )

    row = latest_run(store)
    assert (row["started_at"], row["finished_at"], row["status"]) == (
        STARTED, FINISHED, "ok",
    )
    assert result.new_events == 1
    assert store.claim_pending_events(FINISHED)[0]["created_at"] == STARTED
    assert next(iter(store.known_entities("fictional_course", "column").values()))[
        "created_at"
    ] == STARTED
    assert store._conn.execute(
        "SELECT established_at FROM course_baseline WHERE dimension='announcements'"
    ).fetchone()["established_at"] == STARTED
    assert store.last_scan_time() == FINISHED


def test_default_clock_is_read_after_scan_work(store, monkeypatch):
    clock_calls = []

    def completion_time():
        assert store.baseline_established("fictional_course", "columns")
        clock_calls.append(True)
        return FINISHED

    monkeypatch.setattr(scanner, "now_utc", completion_time, raising=False)
    scanner.scan(FixtureClient(), store, UID, now=STARTED)

    assert latest_run(store)["finished_at"] == FINISHED
    assert clock_calls == [True]


def test_failure_records_its_completion_time(store):
    with pytest.raises(RuntimeError, match="fictional listing failure"):
        scanner.scan(
            FixtureClient(failure="listing"), store, UID,
            now=STARTED, finished_at_fn=lambda: FINISHED,
        )

    row = latest_run(store)
    assert (row["started_at"], row["finished_at"], row["status"]) == (
        STARTED, FINISHED, "failed",
    )


@pytest.mark.parametrize("courses", [[], [course()]])
def test_scan_without_changes_still_refreshes_completion_time(store, courses):
    previous_id = store.start_scan(PREVIOUS)
    store.finish_scan(previous_id, "ok", PREVIOUS)

    result = scanner.scan(
        FixtureClient(courses=courses), store, UID,
        now=STARTED, finished_at_fn=lambda: FINISHED,
    )

    assert result.new_events == 0
    assert result.courses_scanned == len(courses)
    assert latest_run(store)["status"] == "ok"
    assert store.last_scan_time() == FINISHED


def test_partial_scan_keeps_successful_dimensions_and_refreshes_time(store):
    result = scanner.scan(
        FixtureClient(failure="columns"), store, UID,
        now=STARTED, finished_at_fn=lambda: FINISHED,
    )

    assert result.failures == ["DEMO101/columns: RuntimeError"]
    assert latest_run(store)["status"] == "partial"
    assert not store.baseline_established("fictional_course", "columns")
    assert store.baseline_established("fictional_course", "announcements")
    assert store.last_scan_time() == FINISHED


@pytest.mark.parametrize("status", ["ok", "partial", "success"])
def test_last_scan_ignores_newer_failed_and_running_attempts(store, status):
    completed_id = store.start_scan(PREVIOUS)
    store.finish_scan(completed_id, status, PREVIOUS)
    failed_id = store.start_scan(STARTED)
    store.finish_scan(failed_id, "failed", FINISHED)
    store.start_scan(FINISHED)

    assert store.last_scan_time() == PREVIOUS


def test_last_scan_is_absent_when_only_failed_or_running_attempts_exist(store):
    failed_id = store.start_scan(STARTED)
    store.finish_scan(failed_id, "failed", FINISHED)
    store.start_scan(FINISHED)

    assert store.last_scan_time() is None
