"""课件增量镜像：遍历内容树，按 课程/文件夹/原文件名 落盘，按 att_key+modified 增量。

稳定性：本地路径写入 download 表并对同一 att_key 复用（重跑稳定）；不同附件同名时加 id 后缀
避免互相覆盖；下载用 transport 的 .part + os.replace 原子落盘（见 CurlCffiTransport）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe(name: str) -> str:
    return _ILLEGAL.sub("_", name).strip().rstrip(".") or "_"


@dataclass
class MirrorResult:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def mirror(client, store, course, dest, *, now) -> MirrorResult:
    res = MirrorResult()
    base = Path(dest) / _safe(course.course_id or course.id)
    for ancestors, content in client.walk_contents(course.id):
        try:
            atts = client.list_attachments(course.id, content.id)
        except Exception as e:  # noqa: BLE001
            res.failed += 1
            res.errors.append(f"attachments {content.title}: {type(e).__name__}")
            continue
        if not atts:
            continue
        folder = base.joinpath(*[_safe(a) for a in ancestors])
        for att in atts:
            att_key = f"att:{course.id}:{content.id}:{att.id}"
            existing = store.get_download(att_key)
            if existing:
                target = Path(existing["local_path"])
            else:
                target = folder / _safe(att.file_name)
                owner = store.path_owner(str(target))
                if owner and owner != att_key:  # 不同附件同名 → 加 id 后缀避免覆盖
                    target = target.with_name(
                        f"{target.stem}_{att.id.strip('_')}{target.suffix}"
                    )
            if not store.need_download(att_key, content.modified, None):
                res.skipped += 1
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                size = client.download_attachment(course.id, content.id, att.id, str(target))
                store.record_download(
                    att_key, course.id, str(target), content.modified, size, now
                )
                res.downloaded += 1
                res.files.append(str(target))
            except Exception as e:  # noqa: BLE001
                res.failed += 1
                res.errors.append(f"{att.file_name}: {type(e).__name__}")
    return res
