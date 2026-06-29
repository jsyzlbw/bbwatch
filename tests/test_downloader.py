from pathlib import Path

from bbwatch.downloader import mirror
from bbwatch.models import Attachment, Content, Course
from bbwatch.store import Store

NOW = "2026-06-28T00:00:00.000Z"
COURSE = Course("_c1", "MAT3007:Opt", "Opt", "_t1", "Student", "Yes", "Classic")


class FakeClient:
    def __init__(self, tree, atts):
        self.tree = tree  # list[(ancestors, Content)]
        self.atts = atts  # {content_id: [Attachment]}

    def walk_contents(self, cid):
        return iter(self.tree)

    def list_attachments(self, cid, content_id):
        return self.atts.get(content_id, [])

    def download_attachment(self, cid, content_id, att_id, path):
        data = f"data-{att_id}".encode()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
        return len(data)


def test_need_download_logic():
    s = Store(":memory:")
    assert s.need_download("att:1", "m1", 10) is True
    s.record_download("att:1", "_c", "/p", "m1", 10, NOW)
    assert s.need_download("att:1", "m1", 10) is False
    assert s.need_download("att:1", "m2", 10) is True  # modified 变
    assert s.need_download("att:1", "m1", 20) is True  # size 变


def test_mirror_incremental(tmp_path):
    s = Store(":memory:")
    c = Content("_d1", "Slides 1", "resource/x-bb-document", modified="2026-06-01T00:00:00.000Z")
    cli = FakeClient(tree=[(["Content"], c)], atts={"_d1": [Attachment("_a1", "slides1.pdf")]})

    r1 = mirror(cli, s, COURSE, tmp_path, now=NOW)
    assert r1.downloaded == 1 and r1.skipped == 0
    f = tmp_path / "MAT3007_Opt" / "Content" / "slides1.pdf"
    assert f.exists() and f.read_bytes() == b"data-_a1"

    r2 = mirror(cli, s, COURSE, tmp_path, now=NOW)
    assert r2.downloaded == 0 and r2.skipped == 1  # 增量：不重下

    c2 = Content("_d1", "Slides 1", "resource/x-bb-document", modified="2026-06-09T00:00:00.000Z")
    cli.tree = [(["Content"], c2)]
    r3 = mirror(cli, s, COURSE, tmp_path, now=NOW)
    assert r3.downloaded == 1  # modified 变 → 重下


def test_filename_collision_suffixed(tmp_path):
    s = Store(":memory:")
    c1 = Content("_d1", "Doc1", "resource/x-bb-document", modified="m")
    c2 = Content("_d2", "Doc2", "resource/x-bb-document", modified="m")
    cli = FakeClient(
        tree=[([], c1), ([], c2)],
        atts={"_d1": [Attachment("_a1", "hw.pdf")], "_d2": [Attachment("_a2", "hw.pdf")]},
    )
    r = mirror(cli, s, COURSE, tmp_path, now=NOW)
    assert r.downloaded == 2
    names = sorted(p.name for p in (tmp_path / "MAT3007_Opt").glob("*.pdf"))
    assert names == ["hw.pdf", "hw_a2.pdf"]  # 第二个同名加 id 后缀
