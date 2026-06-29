from __future__ import annotations

from urllib.parse import urljoin

from collections.abc import Iterator

from .errors import SessionRefreshError, TransportError
from .models import Announcement, Attachment, Column, ColumnStatus, Content, Course, Me
from .transport import Transport

BB = "https://bb.cuhk.edu.cn"
API = "/learn/api/public/v1"


class BbClient:
    def __init__(self, transport: Transport, relogin=None):
        self._t = transport
        self._relogin = relogin  # 会话失效(401)时重登的回调

    def _raw_get(self, url: str, accept: str = "application/json"):
        r = self._t.request("GET", url, headers={"Accept": accept})
        if r.status == 401 and self._relogin is not None:
            self._relogin()  # 请求级失效 → 重登一次 + 重放原请求
            r = self._t.request("GET", url, headers={"Accept": accept})
            if r.status == 401:
                raise SessionRefreshError(f"会话刷新后仍 401: {url}")
        return r

    def _get_json(self, path: str, tolerate_missing: bool = False) -> dict | None:
        url = path if path.startswith("http") else BB + path
        r = self._raw_get(url)
        # 可选子资源(如某些内容无 attachments)在不同 BB 版本上表现为 400/403/404
        if r.status in (400, 403, 404) and tolerate_missing:
            return None
        if r.status != 200:
            raise TransportError(f"GET {path} -> {r.status}")
        j = r.json()
        if j is None:
            raise TransportError(f"GET {path} 非 JSON 响应")
        return j

    def _paginate(self, first_path: str, tolerate_missing: bool = False) -> list[dict]:
        """跟随 paging.nextPage 取全集；任一页失败上抛。带不前进/runaway 守卫。
        tolerate_missing=True 时首页 4xx 视为空集(可选子资源缺失，如文件夹无附件)。"""
        results: list[dict] = []
        path = first_path
        seen_urls: set[str] = set()
        guard = 0
        while path:
            if path in seen_urls or guard > 10000:
                raise TransportError(f"分页异常(自指或过长): {path}")
            seen_urls.add(path)
            guard += 1
            j = self._get_json(path, tolerate_missing=tolerate_missing)
            if j is None:  # 4xx 容忍 → 空
                break
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

    def list_columns(self, cid: str) -> list[Column]:
        """成绩册栏目；只返回带 grading.due 的列（过滤 Total/Weighted Total 汇总列）。"""
        rows = self._paginate(f"{API}/courses/{cid}/gradebook/columns?limit=100")
        out: list[Column] = []
        for c in rows:
            due = (c.get("grading") or {}).get("due")
            if not due:
                continue
            out.append(
                Column(
                    id=c["id"],
                    name=c.get("name") or "",
                    due_utc=due,
                    content_id=c.get("contentId"),
                    score_possible=(c.get("score") or {}).get("possible"),
                )
            )
        return out

    def get_column_status(self, cid: str, colid: str, uid: str) -> ColumnStatus:
        """per-user 成绩状态。仅此端点：404 语义化为'未提交'（附录 A.3.7）。"""
        url = f"{BB}{API}/courses/{cid}/gradebook/columns/{colid}/users/{uid}"
        r = self._raw_get(url)
        if r.status == 404:
            return ColumnStatus(status="None", score=None)
        if r.status != 200:
            raise TransportError(f"GET column status {colid} -> {r.status}")
        j = r.json() or {}
        return ColumnStatus(status=j.get("status") or "None", score=j.get("score"))

    def _to_content(self, c: dict) -> Content:
        return Content(
            id=c["id"],
            title=c.get("title") or "",
            handler=(c.get("contentHandler") or {}).get("id"),
            has_children=bool(c.get("hasChildren")),
            created=c.get("created"),
            modified=c.get("modified"),
        )

    def list_contents(self, cid: str) -> list[Content]:
        rows = self._paginate(f"{API}/courses/{cid}/contents?limit=100")
        return [self._to_content(c) for c in rows]

    def list_content_children(self, cid: str, content_id: str) -> list[Content]:
        rows = self._paginate(f"{API}/courses/{cid}/contents/{content_id}/children?limit=100")
        return [self._to_content(c) for c in rows]

    def walk_contents(self, cid: str, max_depth: int = 12) -> Iterator[tuple[list[str], Content]]:
        """递归整棵内容树，产出 (祖先文件夹标题列表, Content)。带深度/环守卫。"""
        seen: set[str] = set()

        def rec(items: list[Content], ancestors: list[str], depth: int):
            if depth > max_depth:
                return
            for c in items:
                if c.id in seen:
                    continue
                seen.add(c.id)
                yield ancestors, c
                if c.has_children:
                    children = self.list_content_children(cid, c.id)
                    yield from rec(children, ancestors + [c.title], depth + 1)

        yield from rec(self.list_contents(cid), [], 0)

    def list_attachments(self, cid: str, content_id: str) -> list[Attachment]:
        # 文件夹/链接等内容无 attachments 子资源(4xx)→ 视为空
        rows = self._paginate(
            f"{API}/courses/{cid}/contents/{content_id}/attachments?limit=100",
            tolerate_missing=True,
        )
        return [
            Attachment(id=a["id"], file_name=a.get("fileName") or a["id"],
                       mime_type=a.get("mimeType"))
            for a in rows
        ]

    def download_attachment(self, cid: str, content_id: str, att_id: str, path: str) -> int:
        url = f"{BB}{API}/courses/{cid}/contents/{content_id}/attachments/{att_id}/download"
        return self._t.download_to(url, path)

    def list_announcements(self, cid: str) -> list[Announcement]:
        rows = self._paginate(f"{API}/courses/{cid}/announcements?limit=100")
        return [
            Announcement(
                id=a["id"],
                title=a.get("title") or "",
                created=a.get("created") or "",
                body=a.get("body") or "",
            )
            for a in rows
        ]

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
