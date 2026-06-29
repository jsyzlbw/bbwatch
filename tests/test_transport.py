from bbwatch.transport import FakeTransport, Response


def test_fake_transport_serves_scripted_responses():
    t = FakeTransport(
        routes={
            ("GET", "https://x/api"): Response(
                200, {"Content-Type": "application/json"}, '{"a":1}', "https://x/api"
            ),
        }
    )
    r = t.request("GET", "https://x/api")
    assert r.status == 200
    assert r.json() == {"a": 1}


def test_response_json_none_on_nonjson():
    r = Response(200, {"Content-Type": "text/html"}, "<html>", "u")
    assert r.json() is None
