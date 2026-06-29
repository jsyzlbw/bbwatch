from pathlib import Path

import pytest

from bbwatch.cli import format_courses, pick_course, run_download
from bbwatch.models import Attachment, Content, Course
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"


def _course(code):
    return Course(f"_{code}", code, code, "_t1", "Student", "Yes", "Classic")


def test_format_courses_numbered():
    out = format_courses([_course("MAT3007:Opt"), _course("CSC3001:Discrete")])
    assert out.splitlines()[0] == "[1] MAT3007:Opt"
    assert out.splitlines()[1] == "[2] CSC3001:Discrete"


def test_pick_course_by_index_and_code():
    cs = [_course("MAT3007:Opt"), _course("CSC3001:Discrete")]
    assert pick_course(cs, "1").course_id == "MAT3007:Opt"
    assert pick_course(cs, "csc3001").course_id == "CSC3001:Discrete"
    with pytest.raises(ValueError):
        pick_course(cs, "9")
    with pytest.raises(ValueError):
        pick_course(cs, "ZZZ")


class DLClient:
    def walk_contents(self, cid):
        return iter([(["Content"], Content("_d1", "Slides", "resource/x-bb-document", modified="m"))])

    def list_attachments(self, cid, content_id):
        return [Attachment("_a1", "slides1.pdf")]

    def download_attachment(self, cid, content_id, att_id, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"x")
        return 1


def test_run_download_summary(tmp_path):
    s = Store(":memory:")
    out = run_download(DLClient(), s, _course("MAT3007:Opt"), tmp_path, now=NOW)
    assert "新下载 1" in out
    assert (tmp_path / "MAT3007_Opt" / "Content" / "slides1.pdf").exists()
