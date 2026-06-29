from bbwatch.models import Course


def test_course_is_active():
    c = Course(
        id="_1_1",
        course_id="MAT",
        name="X",
        term_id="_t_1",
        role="Student",
        availability="Yes",
        ultra_status="Classic",
    )
    assert c.is_active is True
    c2 = Course(
        id="_2_1",
        course_id="Y",
        name="Y",
        term_id="_t_1",
        role="Student",
        availability="No",
        ultra_status="Classic",
    )
    assert c2.is_active is False
    c3 = Course(
        id="_3_1",
        course_id="Z",
        name="Z",
        term_id="_t_1",
        role="Instructor",
        availability="Yes",
        ultra_status="Classic",
    )
    assert c3.is_active is False
