from bbwatch.store import Store, now_utc, parse_utc


def test_memory_store_schema_version():
    s = Store(":memory:")
    assert s.schema_version() == 1
    s.close()


def test_file_store_persists_and_no_rebuild(tmp_path):
    db = tmp_path / "state.db"
    s = Store(db)
    s.establish_baseline("_c1", "columns", now_utc())
    s.close()
    s2 = Store(db)
    assert s2.schema_version() == 1
    assert s2.baseline_established("_c1", "columns") is True
    s2.close()


def test_wal_mode(tmp_path):
    s = Store(tmp_path / "state.db")
    mode = s._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    s.close()


def test_parse_utc_handles_zulu():
    dt = parse_utc("2026-06-30T15:59:00.000Z")
    assert dt.year == 2026 and dt.tzinfo is not None
