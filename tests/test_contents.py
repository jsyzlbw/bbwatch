from bbwatch.bbclient import API, BB, BbClient
from bbwatch.transport import FakeTransport, Response

C = BB + API + "/courses/_c1"


def J(text):
    return Response(200, {"Content-Type": "application/json"}, text, "u")


def test_walk_contents_recurses():
    t = FakeTransport(
        routes={
            ("GET", C + "/contents?limit=100"): J(
                '{"results":[{"id":"_f1","title":"Content",'
                '"contentHandler":{"id":"resource/x-bb-folder"},"hasChildren":true},'
                '{"id":"_d1","title":"Syllabus","contentHandler":{"id":"resource/x-bb-document"}}]}'
            ),
            ("GET", C + "/contents/_f1/children?limit=100"): J(
                '{"results":[{"id":"_d2","title":"Slides 1",'
                '"contentHandler":{"id":"resource/x-bb-document"},'
                '"modified":"2026-06-01T00:00:00.000Z"}]}'
            ),
        }
    )
    items = [(anc, c.title) for anc, c in BbClient(t).walk_contents("_c1")]
    # 深度优先：先进 Content 文件夹再回到同级 Syllabus
    assert items == [([], "Content"), (["Content"], "Slides 1"), ([], "Syllabus")]


def test_list_attachments_and_download(tmp_path):
    dl = BB + API + "/courses/_c1/contents/_d1/attachments/_a1/download"
    t = FakeTransport(
        routes={
            ("GET", C + "/contents/_d1/attachments?limit=100"): J(
                '{"results":[{"id":"_a1","fileName":"Syllabus.pdf","mimeType":"application/pdf"}]}'
            )
        },
        downloads={dl: b"%PDF-1.4 fake-bytes"},
    )
    cli = BbClient(t)
    atts = cli.list_attachments("_c1", "_d1")
    assert atts[0].file_name == "Syllabus.pdf" and atts[0].mime_type == "application/pdf"
    out = tmp_path / "out.pdf"
    n = cli.download_attachment("_c1", "_d1", "_a1", str(out))
    assert n == len(b"%PDF-1.4 fake-bytes")
    assert out.read_bytes() == b"%PDF-1.4 fake-bytes"
