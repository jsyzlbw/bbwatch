from __future__ import annotations

from urllib.parse import urljoin

from .errors import TransportError
from .models import Course, Me
from .transport import Transport

BB = "https://bb.cuhk.edu.cn"
API = "/learn/api/public/v1"


class BbClient:
    def __init__(self, transport: Transport):
        self._t = transport

    def _get_json(self, path: str) -> dict:
        url = path if path.startswith("http") else BB + path
        r = self._t.request("GET", url, headers={"Accept": "application/json"})
        if r.status != 200:
            raise TransportError(f"GET {path} -> {r.status}")
        j = r.json()
        if j is None:
            raise TransportError(f"GET {path} 非 JSON 响应")
        return j

    def _paginate(self, first_path: str) -> list[dict]:
        """跟随 paging.nextPage 取全集；任一页失败上抛。带不前进/runaway 守卫。"""
        results: list[dict] = []
        path = first_path
        seen_urls: set[str] = set()
        guard = 0
        while path:
            if path in seen_urls or guard > 10000:
                raise TransportError(f"分页异常(自指或过长): {path}")
            seen_urls.add(path)
            guard += 1
            j = self._get_json(path)
            results.extend(j.get("results", []))
            nxt = (j.get("paging") or {}).get("nextPage")
            if not nxt or nxt == path:
                break
            path = nxt if nxt.startswith("http") else urljoin(BB, nxt)
        return results

    def get_me(self) -> Me:
        j = self._get_json(f"{API}/users/me")
        return Me(
            id=j["id"],
            user_name=j.get("userName", ""),
            given_name=(j.get("name") or {}).get("given"),
        )

    def list_terms(self) -> dict:
        rows = self._paginate(f"{API}/terms?limit=100")
        return {t["id"]: t.get("name") for t in rows}

    def list_courses(self, uid: str) -> list[Course]:
        rows = self._paginate(f"{API}/users/{uid}/courses?expand=course&limit=100")
        out: list[Course] = []
        for it in rows:
            c = it.get("course") or {}
            out.append(
                Course(
                    id=c.get("id") or it.get("courseId"),
                    course_id=c.get("courseId") or "",
                    name=c.get("name") or "",
                    term_id=c.get("termId"),
                    role=it.get("courseRoleId") or "",
                    availability=(c.get("availability") or {}).get("available") or "",
                    ultra_status=c.get("ultraStatus") or "",
                )
            )
        return out
