from bbwatch.dedup import make_dedup_key


def test_same_type_entity_no_variant_is_stable():
    a = make_dedup_key("new_assignment", "col:_c1:_h1")
    b = make_dedup_key("new_assignment", "col:_c1:_h1")
    assert a == b


def test_different_event_types_differ():
    assert make_dedup_key("new_assignment", "col:_c1:_h1") != make_dedup_key(
        "graded", "grade:_c1:_h1"
    )


def test_deadline_changed_variant_distinguishes_reschedules():
    first = make_dedup_key("deadline_changed", "col:_c1:_h1", variant="2026-06-30T15:59:00Z")
    second = make_dedup_key("deadline_changed", "col:_c1:_h1", variant="2026-07-03T15:59:00Z")
    assert first != second  # 改两次期 = 两次合法提醒


def test_same_variant_dedups():
    a = make_dedup_key("deadline_changed", "col:_c1:_h1", variant="2026-07-03T15:59:00Z")
    b = make_dedup_key("deadline_changed", "col:_c1:_h1", variant="2026-07-03T15:59:00Z")
    assert a == b
