"""M4 拍摄包与视频审核。

规则：
- 拍摄包版本不可修改：AI 生成、手动修改都产生新版本。
- 1 轮 = 1 条新视频。确认拍摄包时把版本写进本轮，之后本轮的拍摄包不再改变（本轮锁定）。
- 同一轮的 V1、V2… 只算 1 条；验收通过的轮次才计数。
- 同一文件在同一轮重复上传返回已有版本（(round_id, sha256) 唯一），不会重复产生视频。
- 轮次状态用带 revision 条件的 UPDATE 推进，重复或并发提交不会产生第二个结论。
- 合作历史通过 collaborations 的公开函数写入；本模块不读写寄样和收件信息。
"""

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tk_workspace.config import get_settings
from tk_workspace.modules.collaborations import service as collabs
from tk_workspace.modules.collaborations.service import CollabError
from tk_workspace.modules.production.schemas import (
    BriefSaveIn,
    BriefVersionView,
    ConfirmBriefIn,
    FeedbackIn,
    FeedbackView,
    ProductionStage,
    ProductionView,
    ReviewIn,
    ReviewView,
    RoundIn,
    RoundView,
    VideoVersionView,
)
from tk_workspace.platform import media
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso

OPEN = ("briefing", "awaiting_video", "in_review", "revision_requested")
UPLOADABLE = ("awaiting_video", "revision_requested")
ROUND_ZH = {
    "briefing": "准备拍摄包",
    "awaiting_video": "等视频",
    "in_review": "待审核",
    "revision_requested": "等返修",
    "accepted": "已验收",
    "cancelled": "已取消",
}


def _nf(what: str) -> CollabError:
    return CollabError("not_found", f"{what}不存在", 404)


def fmt_tc(ms: int) -> str:
    s, ms = divmod(ms, 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{ms // 100}"


# ---------------- 给 collaborations 用的公开函数 ----------------


def stage(s: Session, collab_id: str) -> ProductionStage:
    r = s.execute(
        text(
            """SELECT id, round_no, status, updated_at FROM production_rounds
               WHERE collaboration_id=:c AND status != 'cancelled' ORDER BY round_no DESC LIMIT 1"""
        ),
        {"c": collab_id},
    ).first()
    accepted = s.execute(
        text("SELECT COUNT(*) FROM production_rounds WHERE collaboration_id=:c AND status='accepted'"),
        {"c": collab_id},
    ).scalar()
    return ProductionStage(
        round_id=r.id if r else None,
        round_no=r.round_no if r else None,
        round_status=r.status if r else None,
        accepted_videos=int(accepted or 0),
        last_activity_at=r.updated_at if r else None,
    )


def cancel_open_rounds(s: Session, collab_id: str, now: str, note: str) -> None:
    """合作关闭 / 完成时调用。只取消还没交视频的轮次和进行中的轮次；已验收的不变。"""
    for rid, no in s.execute(
        text(
            """SELECT id, round_no FROM production_rounds
               WHERE collaboration_id=:c AND status IN ('briefing','awaiting_video','in_review','revision_requested')"""
        ),
        {"c": collab_id},
    ).all():
        s.execute(
            text(
                """UPDATE production_rounds SET status='cancelled', cancelled_at=:n, updated_at=:n,
                   revision=revision+1 WHERE id=:id"""
            ),
            {"n": now, "id": rid},
        )
        collabs.add_event(s, collab_id, "round_cancelled", f"第 {no} 轮已取消：{note}", rid)


# ---------------- 拍摄包 ----------------


def _brief_id(s: Session, collab_id: str, create: bool, now: str) -> str | None:
    bid = s.execute(
        text("SELECT id FROM content_briefs WHERE collaboration_id=:c"), {"c": collab_id}
    ).scalar()
    if bid or not create:
        return bid
    bid = str(uuid.uuid4())
    s.execute(
        text(
            "INSERT INTO content_briefs (id, collaboration_id, created_at, updated_at) VALUES (:id,:c,:n,:n)"
        ),
        {"id": bid, "c": collab_id, "n": now},
    )
    return bid


def _brief_view(s: Session, row) -> BriefVersionView:
    rounds = (
        s.execute(
            text("SELECT round_no FROM production_rounds WHERE brief_version_id=:v ORDER BY round_no"),
            {"v": row["id"]},
        )
        .scalars()
        .all()
    )
    return BriefVersionView(
        id=row["id"],
        version_no=row["version_no"],
        content_language=row["content_language"],
        title=row["title"],
        body=row["body"],
        body_zh=row["body_zh"],
        cautions=json.loads(row["cautions"] or "[]"),
        source=row["source"],
        based_on_version_id=row["based_on_version_id"],
        product_version_id=row["product_version_id"],
        model=row["model"],
        locked_round_nos=list(rounds),
        created_at=row["created_at"],
    )


def _brief_row(s: Session, version_id: str):
    row = (
        s.execute(
            text(
                """SELECT v.*, b.collaboration_id FROM content_brief_versions v
                   JOIN content_briefs b ON b.id = v.brief_id WHERE v.id=:id"""
            ),
            {"id": version_id},
        )
        .mappings()
        .first()
    )
    if not row:
        raise _nf("拍摄包版本")
    return row


def insert_brief_version(
    s: Session,
    r,
    *,
    language: str,
    title: str,
    body: str,
    body_zh: str,
    cautions: list[str],
    source: str,
    based_on: str | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
) -> BriefVersionView:
    now = utcnow_iso()
    bid = _brief_id(s, r["id"], True, now)
    no = (
        s.execute(
            text("SELECT COALESCE(MAX(version_no), 0) FROM content_brief_versions WHERE brief_id=:b"),
            {"b": bid},
        ).scalar()
        or 0
    ) + 1
    vid = str(uuid.uuid4())
    s.execute(
        text(
            """INSERT INTO content_brief_versions (id, brief_id, version_no, content_language, title, body, body_zh,
                   cautions, source, based_on_version_id, product_version_id, model, prompt_version, created_at)
               VALUES (:id,:b,:no,:lang,:title,:body,:zh,:cau,:src,:base,:pv,:model,:pver,:n)"""
        ),
        {
            "id": vid,
            "b": bid,
            "no": no,
            "lang": language,
            "title": title,
            "body": body,
            "zh": body_zh,
            "cau": json.dumps(cautions, ensure_ascii=False),
            "src": source,
            "base": based_on,
            "pv": r["product_version_id"],
            "model": model,
            "pver": prompt_version,
            "n": now,
        },
    )
    s.execute(text("UPDATE content_briefs SET updated_at=:n WHERE id=:b"), {"n": now, "b": bid})
    return _brief_view(s, _brief_row(s, vid))


def save_brief(collab_id: str, body: BriefSaveIn) -> BriefVersionView:
    with tx() as s:
        r = collabs.lock_collab(s, collab_id)
        base = _brief_row(s, body.based_on_version_id)
        if base["collaboration_id"] != collab_id:
            raise _nf("拍摄包版本")
        if (base["title"], base["body"], base["body_zh"]) == (body.title, body.body, body.body_zh):
            raise CollabError("no_change", "内容没有变化，不需要保存新版本", 422)
        return insert_brief_version(
            s,
            r,
            language=base["content_language"],
            title=body.title,
            body=body.body,
            body_zh=body.body_zh,
            cautions=[],
            source="manual",
            based_on=base["id"],
        )


def _brief_versions(s: Session, collab_id: str) -> list[BriefVersionView]:
    bid = _brief_id(s, collab_id, False, "")
    if not bid:
        return []
    rows = (
        s.execute(
            text(
                """SELECT v.*, b.collaboration_id FROM content_brief_versions v
                   JOIN content_briefs b ON b.id=v.brief_id WHERE v.brief_id=:b ORDER BY v.version_no DESC"""
            ),
            {"b": bid},
        )
        .mappings()
        .all()
    )
    return [_brief_view(s, row) for row in rows]


# ---------------- 轮次 ----------------


def _round_row(s: Session, round_id: str):
    row = s.execute(text("SELECT * FROM production_rounds WHERE id=:id"), {"id": round_id}).mappings().first()
    if not row:
        raise _nf("拍摄轮次")
    return row


def _open_round(s: Session, collab_id: str):
    return (
        s.execute(
            text(
                """SELECT * FROM production_rounds WHERE collaboration_id=:c
                   AND status IN ('briefing','awaiting_video','in_review','revision_requested')
                   ORDER BY round_no DESC LIMIT 1"""
            ),
            {"c": collab_id},
        )
        .mappings()
        .first()
    )


def _set_round(s: Session, row, to: str, now: str, **f: Any) -> None:
    sets = ", ".join(f"{k}=:{k}" for k in f)
    res = s.execute(
        text(
            f"""UPDATE production_rounds SET status=:to, revision=revision+1, updated_at=:n{", " + sets if sets else ""}
                WHERE id=:id AND revision=:rev"""  # noqa: S608 — 列名来自代码常量
        ),
        {"to": to, "n": now, "id": row["id"], "rev": row["revision"], **f},
    )
    if res.rowcount != 1:
        raise CollabError("stale_revision", "这一轮已在别处更新，请刷新后再操作")


def _create_round(s: Session, collab_id: str, now: str) -> Any:
    op = _open_round(s, collab_id)
    if op:
        raise CollabError(
            "round_open",
            f"第 {op['round_no']} 轮还没结束（{ROUND_ZH[op['status']]}），先验收或取消这一轮",
            extra={"round_id": op["id"]},
        )
    no = (
        s.execute(
            text("SELECT COALESCE(MAX(round_no), 0) FROM production_rounds WHERE collaboration_id=:c"),
            {"c": collab_id},
        ).scalar()
        or 0
    ) + 1
    rid = str(uuid.uuid4())
    s.execute(
        text(
            """INSERT INTO production_rounds (id, collaboration_id, round_no, status, created_at, updated_at)
               VALUES (:id,:c,:no,'briefing',:n,:n)"""
        ),
        {"id": rid, "c": collab_id, "no": no, "n": now},
    )
    collabs.add_event(s, collab_id, "round_started", f"开始第 {no} 轮拍摄", rid)
    return _round_row(s, rid)


def start_round(collab_id: str, body: RoundIn) -> tuple[RoundView, bool]:
    """开始新一轮。已有“准备拍摄包”的轮次时直接返回它（重复点击不会产生新轮次）。"""
    now = utcnow_iso()
    with tx() as s:
        collabs.lock_collab(s, collab_id)
        op = _open_round(s, collab_id)
        if op and op["status"] == "briefing":
            return _round_view(s, op), False
        row = _create_round(s, collab_id, now)
        if body.note:
            collabs.add_event(s, collab_id, "note", body.note, row["id"])
        return _round_view(s, row), True


def confirm_brief(collab_id: str, body: ConfirmBriefIn) -> RoundView:
    """确认拍摄包：写入当前“准备拍摄包”的轮次（没有则开始新一轮），之后本轮拍摄包锁定。"""
    now = utcnow_iso()
    with tx() as s:
        collabs.lock_collab(s, collab_id)
        v = _brief_row(s, body.brief_version_id)
        if v["collaboration_id"] != collab_id:
            raise _nf("拍摄包版本")
        row = _open_round(s, collab_id)
        if row and row["status"] != "briefing":
            locked = _brief_row(s, row["brief_version_id"])["version_no"] if row["brief_version_id"] else "?"
            raise CollabError(
                "brief_locked",
                f"第 {row['round_no']} 轮的拍摄包已锁定为 v{locked}；新版本会用于下一轮",
            )
        if row is None:
            row = _create_round(s, collab_id, now)
        elif body.revision is not None and body.revision != row["revision"]:
            raise CollabError("stale_revision", "这一轮已在别处更新，请刷新后再操作")
        _set_round(s, row, "awaiting_video", now, brief_version_id=v["id"], brief_confirmed_at=now)
        collabs.add_event(
            s,
            collab_id,
            "brief_confirmed",
            f"确认第 {row['round_no']} 轮拍摄包 v{v['version_no']}（{v['content_language']}），本轮锁定",
            row["id"],
        )
        return _round_view(s, _round_row(s, row["id"]))


def cancel_round(round_id: str) -> RoundView:
    now = utcnow_iso()
    with tx() as s:
        row = _round_row(s, round_id)
        collabs.lock_collab(s, row["collaboration_id"])
        if row["status"] not in OPEN:
            raise CollabError("round_closed", "这一轮已验收或已取消")
        _set_round(s, row, "cancelled", now, cancelled_at=now)
        collabs.add_event(
            s, row["collaboration_id"], "round_cancelled", f"取消第 {row['round_no']} 轮", round_id
        )
        return _round_view(s, _round_row(s, round_id))


# ---------------- 视频 ----------------


def precheck_upload(round_id: str) -> None:
    """写盘前的快速检查，避免把大文件传完才发现不能上传。"""
    with tx() as s:
        row = _round_row(s, round_id)
        collabs.lock_collab(s, row["collaboration_id"])
        if row["status"] == "briefing":
            raise CollabError("brief_required", "本轮还没有确认拍摄包，先确认拍摄包再上传视频")
        if row["status"] in ("accepted", "cancelled"):
            raise CollabError("round_closed", "这一轮已验收或已取消，不能再上传")


def register_video(
    round_id: str, *, sha: str, rel_path: str, size: int, mime: str, original_name: str, note: str
) -> tuple[VideoVersionView, bool]:
    now = utcnow_iso()
    with tx() as s:
        row = _round_row(s, round_id)
        collabs.lock_collab(s, row["collaboration_id"])
        existing = (
            s.execute(
                text("SELECT * FROM video_versions WHERE round_id=:r AND sha256=:h"),
                {"r": round_id, "h": sha},
            )
            .mappings()
            .first()
        )
        if existing:
            return _video_view(s, existing), False
        if row["status"] == "briefing":
            raise CollabError("brief_required", "本轮还没有确认拍摄包，先确认拍摄包再上传视频")
        if row["status"] == "in_review":
            raise CollabError(
                "not_uploadable", "当前版本还在审核，先给出审核结论（要求返修或验收）再上传新版本"
            )
        if row["status"] not in UPLOADABLE:
            raise CollabError("round_closed", "这一轮已验收或已取消，不能再上传")
        aid = s.execute(text("SELECT id FROM video_assets WHERE sha256=:h"), {"h": sha}).scalar()
        if not aid:
            aid = str(uuid.uuid4())
            s.execute(
                text(
                    """INSERT INTO video_assets (id, sha256, size_bytes, mime_type, rel_path, created_at)
                       VALUES (:id,:h,:sz,:m,:p,:n)"""
                ),
                {"id": aid, "h": sha, "sz": size, "m": mime, "p": rel_path, "n": now},
            )
        no = (
            s.execute(
                text("SELECT COALESCE(MAX(version_no), 0) FROM video_versions WHERE round_id=:r"),
                {"r": round_id},
            ).scalar()
            or 0
        ) + 1
        vid = str(uuid.uuid4())
        try:
            with s.begin_nested():
                s.execute(
                    text(
                        """INSERT INTO video_versions (id, round_id, version_no, asset_id, sha256, original_name, note,
                               status, created_at) VALUES (:id,:r,:no,:a,:h,:name,:note,'in_review',:n)"""
                    ),
                    {
                        "id": vid,
                        "r": round_id,
                        "no": no,
                        "a": aid,
                        "h": sha,
                        "name": original_name[:255],
                        "note": note,
                        "n": now,
                    },
                )
        except IntegrityError:
            # 并发上传同一文件：另一请求已登记
            again = (
                s.execute(
                    text("SELECT * FROM video_versions WHERE round_id=:r AND sha256=:h"),
                    {"r": round_id, "h": sha},
                )
                .mappings()
                .first()
            )
            if again:
                return _video_view(s, again), False
            raise
        _set_round(s, row, "in_review", now)
        collabs.add_event(
            s,
            row["collaboration_id"],
            "video_uploaded",
            f"收到第 {row['round_no']} 轮 V{no}：{original_name}",
            vid,
        )
        return _video_view(s, _video_row(s, vid)), True


def _video_row(s: Session, vid: str):
    row = s.execute(text("SELECT * FROM video_versions WHERE id=:id"), {"id": vid}).mappings().first()
    if not row:
        raise _nf("视频版本")
    return row


def asset_file(asset_id: str) -> tuple[Path, str]:
    with tx() as s:
        a = s.execute(
            text("SELECT rel_path, mime_type FROM video_assets WHERE id=:id"), {"id": asset_id}
        ).first()
    if not a:
        raise _nf("视频文件")
    path = get_settings().files_dir / a.rel_path
    if not path.is_file():
        raise CollabError("file_missing", "视频文件已不在数据目录中（可能被手动删除）", 404)
    return path, a.mime_type


def _feedback_view(row) -> FeedbackView:
    return FeedbackView(
        id=row["id"],
        video_version_id=row["video_version_id"],
        timecode_ms=row["timecode_ms"],
        category=row["category"],
        severity=row["severity"],
        body=row["body"],
        submitted=row["review_id"] is not None,
        created_at=row["created_at"],
    )


def _video_view(s: Session, row) -> VideoVersionView:
    a = s.execute(
        text("SELECT size_bytes, mime_type FROM video_assets WHERE id=:id"), {"id": row["asset_id"]}
    ).first()
    fb = (
        s.execute(
            text("SELECT * FROM feedback_items WHERE video_version_id=:v ORDER BY timecode_ms, created_at"),
            {"v": row["id"]},
        )
        .mappings()
        .all()
    )
    rv = (
        s.execute(text("SELECT * FROM video_reviews WHERE video_version_id=:v"), {"v": row["id"]})
        .mappings()
        .first()
    )
    return VideoVersionView(
        id=row["id"],
        round_id=row["round_id"],
        version_no=row["version_no"],
        label=f"V{row['version_no']}",
        status=row["status"],
        original_name=row["original_name"],
        note=row["note"],
        size_bytes=a.size_bytes if a else 0,
        mime_type=a.mime_type if a else "",
        sha256=row["sha256"],
        media_url=media.signed_url(row["asset_id"]),
        feedback=[_feedback_view(f) for f in fb],
        review=ReviewView(
            id=rv["id"],
            decision=rv["decision"],
            summary=rv["summary"],
            message=rv["message"],
            message_language=rv["message_language"],
            created_at=rv["created_at"],
        )
        if rv
        else None,
        created_at=row["created_at"],
    )


def _reviewable(s: Session, vid: str):
    v = _video_row(s, vid)
    r = _round_row(s, v["round_id"])
    collabs.lock_collab(s, r["collaboration_id"])
    if v["status"] != "in_review" or r["status"] != "in_review":
        raise CollabError("not_reviewable", "这个版本已经给出审核结论，不能再修改反馈")
    return v, r


def add_feedback(vid: str, body: FeedbackIn) -> FeedbackView:
    now = utcnow_iso()
    with tx() as s:
        _reviewable(s, vid)
        fid = str(uuid.uuid4())
        s.execute(
            text(
                """INSERT INTO feedback_items (id, video_version_id, timecode_ms, category, severity, body, created_at)
                   VALUES (:id,:v,:tc,:c,:sv,:b,:n)"""
            ),
            {
                "id": fid,
                "v": vid,
                "tc": body.timecode_ms,
                "c": body.category,
                "sv": body.severity,
                "b": body.body,
                "n": now,
            },
        )
        return _feedback_view(
            s.execute(text("SELECT * FROM feedback_items WHERE id=:id"), {"id": fid}).mappings().one()
        )


def delete_feedback(fid: str) -> None:
    with tx() as s:
        f = s.execute(text("SELECT * FROM feedback_items WHERE id=:id"), {"id": fid}).mappings().first()
        if not f:
            raise _nf("反馈")
        if f["review_id"]:
            raise CollabError("not_reviewable", "这条反馈已随审核结论提交，不能删除")
        _reviewable(s, f["video_version_id"])
        s.execute(text("DELETE FROM feedback_items WHERE id=:id"), {"id": fid})


def review(vid: str, body: ReviewIn) -> RoundView:
    now = utcnow_iso()
    with tx() as s:
        v, r = _reviewable(s, vid)
        n_fb = s.execute(
            text("SELECT COUNT(*) FROM feedback_items WHERE video_version_id=:v AND review_id IS NULL"),
            {"v": vid},
        ).scalar()
        if body.decision == "changes_requested" and not n_fb:
            raise CollabError("feedback_required", "要求返修至少要有 1 条时间码反馈", 422)
        rid = str(uuid.uuid4())
        try:
            with s.begin_nested():
                s.execute(
                    text(
                        """INSERT INTO video_reviews (id, video_version_id, decision, summary, message, message_language,
                               created_at) VALUES (:id,:v,:d,:s,:m,:ml,:n)"""
                    ),
                    {
                        "id": rid,
                        "v": vid,
                        "d": body.decision,
                        "s": body.summary,
                        "m": body.message,
                        "ml": body.message_language,
                        "n": now,
                    },
                )
        except IntegrityError as e:
            raise CollabError("not_reviewable", "这个版本已经给出审核结论") from e
        s.execute(
            text("UPDATE feedback_items SET review_id=:r WHERE video_version_id=:v AND review_id IS NULL"),
            {"r": rid, "v": vid},
        )
        label = f"第 {r['round_no']} 轮 V{v['version_no']}"
        if body.decision == "accepted":
            s.execute(text("UPDATE video_versions SET status='accepted' WHERE id=:id"), {"id": vid})
            _set_round(s, r, "accepted", now, accepted_version_id=vid, accepted_at=now)
            total = stage(s, r["collaboration_id"]).accepted_videos
            collabs.add_event(
                s,
                r["collaboration_id"],
                "video_accepted",
                f"{label} 验收通过，本轮计 1 条（累计 {total} 条）",
                vid,
            )
        else:
            s.execute(text("UPDATE video_versions SET status='changes_requested' WHERE id=:id"), {"id": vid})
            _set_round(s, r, "revision_requested", now)
            collabs.add_event(
                s, r["collaboration_id"], "revision_requested", f"{label} 要求返修（{n_fb} 条反馈）", vid
            )
        return _round_view(s, _round_row(s, r["id"]))


# ---------------- 视图 ----------------


def _round_view(s: Session, row) -> RoundView:
    vids = (
        s.execute(
            text("SELECT * FROM video_versions WHERE round_id=:r ORDER BY version_no DESC"), {"r": row["id"]}
        )
        .mappings()
        .all()
    )
    brief = _brief_view(s, _brief_row(s, row["brief_version_id"])) if row["brief_version_id"] else None
    return RoundView(
        id=row["id"],
        collaboration_id=row["collaboration_id"],
        round_no=row["round_no"],
        status=row["status"],
        brief_version=brief,
        brief_confirmed_at=row["brief_confirmed_at"],
        videos=[_video_view(s, v) for v in vids],
        accepted_version_id=row["accepted_version_id"],
        accepted_at=row["accepted_at"],
        counted=row["status"] == "accepted",
        can_upload=row["status"] in UPLOADABLE,
        revision=row["revision"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_round(round_id: str) -> RoundView:
    with tx() as s:
        return _round_view(s, _round_row(s, round_id))


def get_video(vid: str) -> VideoVersionView:
    with tx() as s:
        return _video_view(s, _video_row(s, vid))


def market_languages(s: Session, market_code: str) -> list[str]:
    raw = s.execute(
        text("SELECT content_languages FROM markets WHERE market_code=:m"), {"m": market_code}
    ).scalar()
    langs = json.loads(raw) if raw else []
    return langs or ["en"]


def get_production(collab_id: str) -> ProductionView:
    with tx() as s:
        r = collabs.collab_row(s, collab_id)
        langs = market_languages(s, r["market_code"])
        rounds = (
            s.execute(
                text("SELECT * FROM production_rounds WHERE collaboration_id=:c ORDER BY round_no DESC"),
                {"c": collab_id},
            )
            .mappings()
            .all()
        )
        op = _open_round(s, collab_id)
        blocker = None
        if r["status"] not in collabs.SHIPPABLE:
            blocker = "记录双方约定后才能开始拍摄" if r["status"] in collabs.AGREEABLE else "合作已结束"
        elif op and op["status"] != "briefing":
            blocker = f"第 {op['round_no']} 轮还没结束（{ROUND_ZH[op['status']]}）"
        return ProductionView(
            collaboration_id=collab_id,
            content_language=langs[0],
            content_languages=langs,
            brief_versions=_brief_versions(s, collab_id),
            rounds=[_round_view(s, x) for x in rounds],
            accepted_videos=sum(1 for x in rounds if x["status"] == "accepted"),
            agreed_video_count=r["agreed_video_count"],
            can_start_round=blocker is None and op is None,
            start_round_blocker=blocker,
        )
