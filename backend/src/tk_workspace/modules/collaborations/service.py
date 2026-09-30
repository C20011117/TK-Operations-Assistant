"""M3 合作与寄样。

规则：
- 候选决定是追加事件；“保留”只表示值得推进，不触发任何动作。
- 合作必须由本人记录双方约定后才能寄样；匹配分数不能代替约定。
- 寄样：草稿 → 核对（冻结内容 + 哈希）→ 本人确认 → 人工登记已寄出。修改内容使确认失效；
  状态更新都用“带条件的 UPDATE”做比较并交换，并发或重复提交不会产生第二次确认或第二次寄出。
- 签收状态只由物流节点决定，没有节点时一直是 unknown。
- 收件信息和快递单号只以密文落库；本模块不记录任何包含它们的日志。
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from tk_workspace.modules.collaborations.schemas import (
    AgreementIn,
    AgreementView,
    CollabEventView,
    CollaborationCreate,
    CollaborationDetail,
    CollaborationSummary,
    ConfirmationPreview,
    ConfirmationView,
    ConfirmIn,
    CreatorBrief,
    DecisionIn,
    DecisionView,
    DispatchIn,
    FollowUp,
    FollowUpIn,
    RecipientIn,
    RecipientView,
    ShipmentEventIn,
    ShipmentEventView,
    ShipmentIn,
    ShipmentItem,
    ShipmentView,
    SnoozeIn,
    TransitionIn,
    mask_recipient,
    mask_tracking,
)
from tk_workspace.platform import crypto
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso
from tk_workspace.platform.jobs import service as jobs

CONFIRM_VALID_DAYS = 7
ACTIVE_SHIPMENT = ("draft", "awaiting_confirmation", "confirmed")
TRANSITIONS: dict[str, list[str]] = {
    "planned": ["contacting", "closed"],
    "contacting": ["negotiating", "closed"],
    "negotiating": ["contacting", "closed"],
    "agreed": ["closed"],  # 有验收通过的视频后还可以“完成”（见 _allowed）
    "in_progress": ["closed"],
    "completed": [],
    "closed": ["contacting"],
}
AGREEABLE = ("planned", "contacting", "negotiating")
SHIPPABLE = ("agreed", "in_progress")


class CollabError(Exception):
    def __init__(self, code: str, message: str, status: int = 409, extra: dict | None = None) -> None:
        super().__init__(code)
        self.code, self.message, self.status, self.extra = code, message, status, extra or {}


def _nf(what: str) -> CollabError:
    return CollabError("not_found", f"{what}不存在", 404)


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _iso(v: datetime | date) -> str:
    if isinstance(v, datetime):
        dt = v if v.tzinfo else v.replace(tzinfo=UTC)
        return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
    return f"{v.isoformat()}T12:00:00.000Z"  # 只知道日期：记为当天中午（UTC），避免时区把日期推到前后一天


def _check_not_future(v: datetime | date, what: str) -> None:
    now = datetime.now(UTC)
    ts = datetime.fromisoformat(_iso(v).replace("Z", "+00:00"))
    if ts > now + timedelta(days=1):
        raise CollabError("future_time", f"{what}不能晚于今天", 422)


# ---------------- 幂等 ----------------


def _idem_lookup(s: Session, action: str, key: str, rhash: str) -> str | None:
    row = s.execute(
        text("SELECT request_hash, resource_id FROM idempotency_records WHERE action=:a AND key=:k"),
        {"a": action, "k": key},
    ).first()
    if row is None:
        return None
    if row.request_hash != rhash:
        raise CollabError("idempotency_conflict", "同一个 Idempotency-Key 已用于不同内容的请求", 422)
    return row.resource_id


def _idem_record(s: Session, action: str, key: str, rhash: str, resource_id: str, now: str) -> None:
    s.execute(
        text(
            """INSERT INTO idempotency_records (id, action, key, request_hash, resource_id, created_at)
               VALUES (:id, :a, :k, :h, :r, :n)"""
        ),
        {"id": str(uuid.uuid4()), "a": action, "k": key, "h": rhash, "r": resource_id, "n": now},
    )


# ---------------- 候选决定 ----------------


def _cm(s: Session, cm_id: str, writable: bool = True):
    cm = (
        s.execute(
            text(
                """SELECT cm.*, c.name AS campaign_name, c.status AS campaign_status
               FROM campaign_markets cm JOIN campaigns c ON c.id = cm.campaign_id WHERE cm.id=:id"""
            ),
            {"id": cm_id},
        )
        .mappings()
        .first()
    )
    if not cm:
        raise _nf("任务站点")
    if writable and cm["campaign_status"] != "active":
        raise CollabError("campaign_archived", "任务已归档，不能修改")
    return cm


def _creator_exists(s: Session, creator_id: str) -> None:
    if not s.execute(text("SELECT 1 FROM creators WHERE id=:id"), {"id": creator_id}).first():
        raise _nf("达人")


def _check_refs(
    s: Session, cm_id: str, creator_id: str, run_id: str | None, evaluation_id: str | None
) -> None:
    if (
        run_id
        and not s.execute(
            text("SELECT 1 FROM matching_runs WHERE id=:r AND campaign_market_id=:c"),
            {"r": run_id, "c": cm_id},
        ).first()
    ):
        raise CollabError("bad_reference", "匹配运行不属于这个任务站点", 422)
    if evaluation_id:
        row = s.execute(
            text(
                """SELECT e.run_id FROM candidate_evaluations e JOIN matching_runs r ON r.id = e.run_id
                   WHERE e.id=:e AND e.creator_id=:cr AND r.campaign_market_id=:c"""
            ),
            {"e": evaluation_id, "cr": creator_id, "c": cm_id},
        ).first()
        if not row or (run_id and row.run_id != run_id):
            raise CollabError("bad_reference", "评价记录不属于这个达人或任务站点", 422)


def _decision_rows(s: Session, cm_id: str, creator_id: str | None = None):
    sql = """SELECT d.*, co.id AS collaboration_id
             FROM creator_decisions d
             LEFT JOIN creator_relationships rel ON rel.creator_id = d.creator_id
             LEFT JOIN collaborations co ON co.relationship_id = rel.id AND co.campaign_market_id = d.campaign_market_id
             WHERE d.campaign_market_id = :c
               AND d.created_at = (SELECT MAX(d2.created_at) FROM creator_decisions d2
                                   WHERE d2.campaign_market_id = d.campaign_market_id AND d2.creator_id = d.creator_id)"""
    params: dict[str, Any] = {"c": cm_id}
    if creator_id:
        sql += " AND d.creator_id = :cr"
        params["cr"] = creator_id
    return s.execute(text(sql + " ORDER BY d.created_at DESC"), params).mappings().all()


def _decision_view(r) -> DecisionView:
    return DecisionView(
        id=r["id"],
        campaign_market_id=r["campaign_market_id"],
        creator_id=r["creator_id"],
        decision=r["decision"],
        reason_code=r["reason_code"],
        note=r["note"],
        run_id=r["run_id"],
        evaluation_id=r["evaluation_id"],
        created_at=r["created_at"],
        collaboration_id=r["collaboration_id"],
    )


def record_decision(cm_id: str, body: DecisionIn) -> DecisionView:
    now = utcnow_iso()
    with tx() as s:
        _cm(s, cm_id)
        _creator_exists(s, body.creator_id)
        _check_refs(s, cm_id, body.creator_id, body.run_id, body.evaluation_id)
        prev = _decision_rows(s, cm_id, body.creator_id)
        # 同一毫秒内的连续决定也要能区分先后
        if prev and prev[0]["created_at"] >= now:
            now = _bump(prev[0]["created_at"])
        s.execute(
            text(
                """INSERT INTO creator_decisions (id, campaign_market_id, creator_id, decision, reason_code, note,
                                                 run_id, evaluation_id, supersedes_id, created_at)
                   VALUES (:id, :c, :cr, :d, :rc, :n, :r, :e, :sup, :now)"""
            ),
            {
                "id": str(uuid.uuid4()),
                "c": cm_id,
                "cr": body.creator_id,
                "d": body.decision,
                "rc": body.reason_code,
                "n": body.note,
                "r": body.run_id,
                "e": body.evaluation_id,
                "sup": prev[0]["id"] if prev else None,
                "now": now,
            },
        )
        return _decision_view(_decision_rows(s, cm_id, body.creator_id)[0])


def _bump(ts: str) -> str:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00")) + timedelta(milliseconds=1)
    return _iso(dt)


def list_decisions(cm_id: str) -> list[DecisionView]:
    with tx() as s:
        _cm(s, cm_id, writable=False)
        return [_decision_view(r) for r in _decision_rows(s, cm_id)]


# ---------------- 合作 ----------------

_COLLAB_SQL = """
SELECT co.*, rel.creator_id, cr.unique_id, cr.nickname, cr.region,
       cm.campaign_id, cm.market_code, cm.reporting_currency, cm.time_zone,
       c.name AS campaign_name, p.name AS product_name, pv.version_no AS product_version_no
FROM collaborations co
JOIN creator_relationships rel ON rel.id = co.relationship_id
JOIN creators cr ON cr.id = rel.creator_id
JOIN campaign_markets cm ON cm.id = co.campaign_market_id
JOIN campaigns c ON c.id = cm.campaign_id
JOIN product_versions pv ON pv.id = co.product_version_id
JOIN products p ON p.id = pv.product_id
"""


def _collab_row(s: Session, collab_id: str):
    row = s.execute(text(_COLLAB_SQL + " WHERE co.id = :id"), {"id": collab_id}).mappings().first()
    if not row:
        raise _nf("合作")
    return row


def _event(s: Session, collab_id: str, kind: str, now: str, **kw: Any) -> None:
    s.execute(
        text(
            """INSERT INTO collaboration_events (id, collaboration_id, kind, from_status, to_status, note, ref_id,
                                                created_at)
               VALUES (:id, :co, :k, :f, :t, :n, :ref, :now)"""
        ),
        {
            "id": str(uuid.uuid4()),
            "co": collab_id,
            "k": kind,
            "f": kw.get("from_status"),
            "t": kw.get("to_status"),
            "n": kw.get("note", ""),
            "ref": kw.get("ref_id"),
            "now": now,
        },
    )
    if kind != "follow_up":
        # 有了新进展：“稍后提醒”设定的时间作废，按规则重新计时
        s.execute(text("UPDATE collaborations SET follow_up_at=NULL WHERE id=:id"), {"id": collab_id})


def _latest_shipment(s: Session, collab_id: str):
    return s.execute(
        text(
            """SELECT status, delivery_status, payload_version FROM sample_shipments
               WHERE collaboration_id=:c AND status != 'cancelled' ORDER BY created_at DESC LIMIT 1"""
        ),
        {"c": collab_id},
    ).first()


_SHIPMENT_STEP = {
    "draft": "核对寄样单并确认",
    "awaiting_confirmation": "确认寄样",
    "confirmed": "寄出后登记快递信息",
}


def _stage(s: Session, collab_id: str):
    from tk_workspace.modules.production import service as production

    return production.stage(s, collab_id)


def _production_step(r, stage) -> str | None:
    rs = stage.round_status if stage else None
    n = stage.round_no if stage else None
    if rs == "briefing":
        return f"确认第 {n} 轮拍摄包并发给达人"
    if rs == "awaiting_video":
        return f"等待达人交第 {n} 轮视频"
    if rs == "in_review":
        return f"审核第 {n} 轮视频"
    if rs == "revision_requested":
        return f"等待达人交第 {n} 轮返修版本"
    if rs == "accepted":
        agreed = r["agreed_video_count"]
        if agreed and stage.accepted_videos >= agreed:
            return "约定的视频已全部验收，可以完成合作"
        return f"第 {n} 轮已验收，开始下一轮"
    return None


def _next_step(status: str, ship, r=None, stage=None) -> str | None:
    if status in SHIPPABLE and stage and stage.round_status and stage.round_status != "briefing":
        return _production_step(r, stage)
    if status == "planned":
        return "联系达人"
    if status == "contacting":
        return "等待回复，开始洽谈"
    if status == "negotiating":
        return "谈妥后记录双方约定"
    if status in SHIPPABLE:
        if ship is None:
            return "创建寄样单"
        if ship.status in _SHIPMENT_STEP:
            return _SHIPMENT_STEP[ship.status]
        if ship.delivery_status in ("unknown", "in_transit"):
            return "跟进物流，登记签收"
        if ship.delivery_status in ("exception", "returned"):
            return "处理物流异常（补寄或关闭）"
        if stage and stage.round_status == "briefing":
            return _production_step(r, stage)
        return "准备拍摄包"
    return None


# ---------------- 跟进提醒 ----------------
# 按“当前阶段 + 最近一次进展”计算，不需要后台定时任务：打开列表时就是最新结果。
# (天数, 提醒内容, 适合生成跟进话术)
FOLLOW_UP_RULES: dict[str, tuple[int, str, bool]] = {
    "planned": (2, "准备联系 {d} 天了，还没发出邀约", False),
    "contacting": (3, "已联系 {d} 天没有新进展，建议再跟进一次", True),
    "negotiating": (4, "洽谈 {d} 天没有新进展，确认对方意向", True),
    "no_shipment": (2, "约定后 {d} 天还没建寄样单", False),
    "ship_unconfirmed": (1, "寄样单 {d} 天还没核对确认", False),
    "ship_confirmed": (2, "已确认寄样 {d} 天，还没登记寄出", False),
    "in_transit": (7, "寄出 {d} 天还没确认签收，跟进物流或问达人是否收到", False),
    "exception": (0, "物流异常，需要处理（补寄或关闭）", False),
    "delivered": (5, "签收 {d} 天了，跟进达人拍摄进度", False),
    # M4 拍摄阶段
    "briefing": (2, "拍摄包 {d} 天还没确认发给达人", False),
    "awaiting_video": (7, "拍摄包发出 {d} 天还没收到视频，跟进拍摄进度", True),
    "in_review": (1, "视频交来 {d} 天还没审核", False),
    "revision_requested": (5, "要求返修 {d} 天还没收到新版本", True),
    "round_accepted": (7, "上一条视频验收 {d} 天了，约下一条或完成合作", False),
}


def _parse_ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def _phase(status: str, ship, stage=None, agreed_count: int | None = None) -> str | None:
    if status in ("planned", "contacting", "negotiating"):
        return status
    if status not in SHIPPABLE:
        return None
    rs = stage.round_status if stage else None
    if rs in ("awaiting_video", "in_review", "revision_requested"):
        return rs
    if rs == "accepted":
        if agreed_count and stage.accepted_videos >= agreed_count:
            return None  # 约定的视频已全部验收：只剩“完成合作”，不再提醒
        return "round_accepted"
    p = _ship_phase(ship)
    if rs == "briefing" and p == "delivered":
        return "briefing"
    return p


def _ship_phase(ship) -> str:
    if ship is None:
        return "no_shipment"
    if ship.status in ("draft", "awaiting_confirmation"):
        return "ship_unconfirmed"
    if ship.status == "confirmed":
        return "ship_confirmed"
    if ship.delivery_status in ("exception", "returned"):
        return "exception"
    if ship.delivery_status == "delivered":
        return "delivered"
    return "in_transit"


def _follow_up(s: Session, r, ship, now: datetime | None = None, stage=None) -> FollowUp | None:
    if stage is None and r["status"] in SHIPPABLE:
        stage = _stage(s, r["id"])
    phase = _phase(r["status"], ship, stage, r["agreed_video_count"])
    if phase is None:
        return None
    days, msg, suggest = FOLLOW_UP_RULES[phase]
    last = (
        s.execute(
            text("SELECT MAX(created_at) FROM collaboration_events WHERE collaboration_id=:c"), {"c": r["id"]}
        ).scalar()
        or r["updated_at"]
    )
    last_dt = _parse_ts(last)
    now_dt = now or _parse_ts(utcnow_iso())
    idle = max(0, (now_dt - last_dt).days)
    manual = r.get("follow_up_at")
    if manual and manual > last:
        due, source = _parse_ts(manual), "manual"
    else:
        due, source = last_dt + timedelta(days=days), "rule"
    return FollowUp(
        due_at=_iso(due),
        overdue=due <= now_dt,
        idle_days=idle,
        message=msg.format(d=max(idle, days) if source == "rule" else idle),
        source=source,
        suggest_follow_up_draft=suggest,
    )


def record_follow_up(collab_id: str, body: FollowUpIn) -> CollaborationDetail:
    """记录一次跟进（重新开始计时）；可指定几天后再提醒。"""
    now = utcnow_iso()
    with tx() as s:
        r = _collab_row(s, collab_id)
        if _follow_up(s, r, _latest_shipment(s, collab_id)) is None:
            raise CollabError("not_active", "这个合作已关闭或已完成，不需要跟进")
        nxt = _iso(_parse_ts(now) + timedelta(days=body.next_in_days)) if body.next_in_days else None
        s.execute(
            text("UPDATE collaborations SET follow_up_at=:f, updated_at=:n WHERE id=:id"),
            {"f": nxt, "n": now, "id": collab_id},
        )
        note = body.note or "已跟进"
        if body.next_in_days:
            note += f"（{body.next_in_days} 天后再提醒）"
        _event(s, collab_id, "follow_up", now, note=note)
        _touch_relationship(s, r, now)
        return _detail(s, collab_id)


def snooze_follow_up(collab_id: str, body: SnoozeIn) -> CollaborationDetail:
    """稍后提醒：不算一次进展，只把提醒推迟到 N 天后。"""
    now = utcnow_iso()
    with tx() as s:
        r = _collab_row(s, collab_id)
        if _follow_up(s, r, _latest_shipment(s, collab_id)) is None:
            raise CollabError("not_active", "这个合作已关闭或已完成，不需要跟进")
        s.execute(
            text("UPDATE collaborations SET follow_up_at=:f WHERE id=:id"),
            {"f": _iso(_parse_ts(now) + timedelta(days=body.days)), "id": collab_id},
        )
        return _detail(s, collab_id)


def _summary(s: Session, r) -> CollaborationSummary:
    ship = _latest_shipment(s, r["id"])
    stage = _stage(s, r["id"])
    handle = r["unique_id"]
    return CollaborationSummary(
        id=r["id"],
        status=r["status"],
        closed_reason=r["closed_reason"],
        creator=CreatorBrief(
            id=r["creator_id"],
            unique_id=handle,
            nickname=r["nickname"],
            region=r["region"],
            profile_url=f"https://www.tiktok.com/@{handle}" if handle else None,
        ),
        campaign_id=r["campaign_id"],
        campaign_name=r["campaign_name"],
        campaign_market_id=r["campaign_market_id"],
        market_code=r["market_code"],
        product_name=r["product_name"],
        product_version_no=r["product_version_no"],
        agreed_at=r["agreed_at"],
        next_step=_next_step(r["status"], ship, r, stage),
        follow_up=_follow_up(s, r, ship, stage=stage),
        production=stage if stage.round_id or stage.accepted_videos else None,
        shipment_status=ship.status if ship else None,
        delivery_status=ship.delivery_status if ship and ship.status == "dispatched" else None,
        revision=r["revision"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _detail(s: Session, collab_id: str) -> CollaborationDetail:
    r = _collab_row(s, collab_id)
    base = _summary(s, r)
    events = (
        s.execute(
            text("SELECT * FROM collaboration_events WHERE collaboration_id=:c ORDER BY created_at, rowid"),
            {"c": collab_id},
        )
        .mappings()
        .all()
    )
    ships = (
        s.execute(
            text("SELECT id FROM sample_shipments WHERE collaboration_id=:c ORDER BY created_at DESC"),
            {"c": collab_id},
        )
        .scalars()
        .all()
    )
    agreement = json.loads(r["agreement"]) if r["agreement"] else None
    return CollaborationDetail(
        **base.model_dump(),
        reporting_currency=r["reporting_currency"],
        time_zone=r["time_zone"],
        agreement=AgreementView(**agreement) if agreement else None,
        agreed_video_count=r["agreed_video_count"],
        allowed_transitions=_allowed(r, base.production),
        can_confirm_agreement=r["status"] in AGREEABLE,
        can_create_shipment=r["status"] in SHIPPABLE,
        events=[
            CollabEventView(
                id=e["id"],
                kind=e["kind"],
                from_status=e["from_status"],
                to_status=e["to_status"],
                note=e["note"],
                created_at=e["created_at"],
            )
            for e in events
        ],
        shipments=[_shipment_view(s, sid) for sid in ships],
    )


def _allowed(r, stage) -> list[str]:
    allowed = list(TRANSITIONS.get(r["status"], []))
    if r["status"] in SHIPPABLE and stage and stage.accepted_videos > 0:
        allowed.insert(0, "completed")
    return allowed


def get_collaboration(collab_id: str) -> CollaborationDetail:
    with tx() as s:
        return _detail(s, collab_id)


def list_collaborations(
    status: str | None = None, campaign_market_id: str | None = None, due: bool = False
) -> list[CollaborationSummary]:
    sql = _COLLAB_SQL + " WHERE 1=1"
    params: dict[str, Any] = {}
    if status:
        sql += " AND co.status = :st"
        params["st"] = status
    if campaign_market_id:
        sql += " AND co.campaign_market_id = :cm"
        params["cm"] = campaign_market_id
    with tx() as s:
        rows = s.execute(text(sql + " ORDER BY co.updated_at DESC"), params).mappings().all()
        out = [_summary(s, r) for r in rows]
    if due:
        out = [c for c in out if c.follow_up and c.follow_up.overdue]
        out.sort(key=lambda c: c.follow_up.due_at if c.follow_up else "")
    return out


def create_collaboration(cm_id: str, body: CollaborationCreate) -> tuple[CollaborationDetail, bool]:
    now = utcnow_iso()
    with tx() as s:
        cm = _cm(s, cm_id)
        _creator_exists(s, body.creator_id)
        _check_refs(s, cm_id, body.creator_id, body.run_id, body.evaluation_id)
        cur = _decision_rows(s, cm_id, body.creator_id)
        if not cur or cur[0]["decision"] != "keep":
            raise CollabError("decision_required", "请先把这位达人标记为“保留”，再准备合作")
        rel = s.execute(
            text("SELECT id FROM creator_relationships WHERE creator_id=:c"), {"c": body.creator_id}
        ).scalar()
        if rel is None:
            rel = str(uuid.uuid4())
            s.execute(
                text(
                    """INSERT INTO creator_relationships (id, creator_id, status, created_at, updated_at)
                       VALUES (:id, :c, 'active', :n, :n)"""
                ),
                {"id": rel, "c": body.creator_id, "n": now},
            )
        existing = s.execute(
            text("SELECT id FROM collaborations WHERE relationship_id=:r AND campaign_market_id=:c"),
            {"r": rel, "c": cm_id},
        ).scalar()
        if existing:
            return _detail(s, existing), False
        cid = str(uuid.uuid4())
        s.execute(
            text(
                """INSERT INTO collaborations (id, relationship_id, campaign_market_id, product_version_id, run_id,
                                              evaluation_id, status, created_at, updated_at)
                   VALUES (:id, :r, :cm, :pv, :run, :ev, 'planned', :n, :n)"""
            ),
            {
                "id": cid,
                "r": rel,
                "cm": cm_id,
                "pv": cm["product_version_id"],
                "run": body.run_id or cur[0]["run_id"],
                "ev": body.evaluation_id or cur[0]["evaluation_id"],
                "n": now,
            },
        )
        _event(s, cid, "created", now, to_status="planned", note=body.note)
        return _detail(s, cid), True


def _lock_collab(s: Session, collab_id: str, revision: int | None):
    r = _collab_row(s, collab_id)
    if revision is not None and revision != r["revision"]:
        raise CollabError("stale_revision", "合作已在别处更新，请刷新后再操作")
    camp = s.execute(text("SELECT status FROM campaigns WHERE id=:id"), {"id": r["campaign_id"]}).scalar()
    if camp != "active":
        raise CollabError("campaign_archived", "任务已归档，不能修改")
    return r


def _set_status(s: Session, r, to: str, now: str, **f: Any) -> None:
    """推进状态（按 revision 比较并交换）。f 可含 closed(bool) / closed_reason / agreement /
    agreed_video_count / agreed / started。"""
    res = s.execute(
        text(
            """UPDATE collaborations SET status=:to, revision=revision+1, updated_at=:n,
                   closed_reason = CASE WHEN :set_closed THEN :cr ELSE closed_reason END,
                   closed_at = CASE WHEN :set_closed THEN :ca ELSE closed_at END,
                   agreement = COALESCE(:a, agreement),
                   agreed_video_count = COALESCE(:vc, agreed_video_count),
                   agreed_at = COALESCE(:aa, agreed_at),
                   started_at = COALESCE(:sa, started_at)
               WHERE id=:id AND revision=:rev"""
        ),
        {
            "to": to,
            "n": now,
            "id": r["id"],
            "rev": r["revision"],
            "set_closed": "closed" in f,
            "cr": f.get("closed_reason"),
            "ca": now if f.get("closed") else None,
            "a": f.get("agreement"),
            "vc": f.get("agreed_video_count"),
            "aa": now if f.get("agreed") else None,
            "sa": now if f.get("started") else None,
        },
    )
    if res.rowcount != 1:
        raise CollabError("stale_revision", "合作已在别处更新，请刷新后再操作")


def _touch_relationship(s: Session, r, now: str, first_contact: bool = False) -> None:
    s.execute(
        text(
            """UPDATE creator_relationships SET last_interaction_at=:n, updated_at=:n,
                   first_contact_at = CASE WHEN :fc AND first_contact_at IS NULL THEN :n ELSE first_contact_at END
               WHERE id=:id"""
        ),
        {"n": now, "fc": first_contact, "id": r["relationship_id"]},
    )


def transition(collab_id: str, body: TransitionIn) -> CollaborationDetail:
    now = utcnow_iso()
    with tx() as s:
        r = _lock_collab(s, collab_id, body.revision)
        from tk_workspace.modules.production import service as production

        stage = _stage(s, collab_id)
        if body.to not in _allowed(r, stage):
            if body.to == "completed":
                raise CollabError("no_accepted_video", "还没有验收通过的视频，不能完成合作")
            raise CollabError("invalid_transition", f"当前状态不能改为 {body.to}")
        if body.to == "completed":
            if stage.round_status in ("awaiting_video", "in_review", "revision_requested"):
                raise CollabError("round_open", f"第 {stage.round_no} 轮还在进行中，先验收或取消这一轮")
            production.cancel_open_rounds(s, collab_id, now, "合作完成，未开始的轮次自动取消")
            _set_status(s, r, "completed", now, closed=False)
        elif body.to == "closed":
            production.cancel_open_rounds(s, collab_id, now, "合作关闭，进行中的轮次自动取消")
            _set_status(s, r, "closed", now, closed=True, closed_reason=body.closed_reason)
            # 未寄出的寄样单一并取消，确认作废
            for sid in s.execute(
                text(
                    "SELECT id FROM sample_shipments WHERE collaboration_id=:c AND status IN ('draft','awaiting_confirmation','confirmed')"
                ),
                {"c": collab_id},
            ).scalars():
                _cancel_shipment(s, sid, now, "合作关闭，寄样单自动取消")
        else:
            _set_status(s, r, body.to, now, closed=False)
        _event(s, collab_id, "transition", now, from_status=r["status"], to_status=body.to, note=body.note)
        _touch_relationship(s, r, now, first_contact=body.to == "contacting")
        return _detail(s, collab_id)


def confirm_agreement(collab_id: str, body: AgreementIn) -> CollaborationDetail:
    now = utcnow_iso()
    with tx() as s:
        r = _lock_collab(s, collab_id, body.revision)
        if r["status"] not in AGREEABLE:
            raise CollabError("invalid_transition", "当前状态不能记录约定（已达成或已关闭）")
        _check_not_future(body.agreed_on, "达成日期")
        agreement = {
            "agreed_via": body.agreed_via,
            "agreed_on": body.agreed_on.isoformat(),
            "agreed_video_count": body.agreed_video_count,
            "sample_included": body.sample_included,
            "terms_note": body.terms_note,
            "recorded_at": now,
        }
        _set_status(
            s,
            r,
            "agreed",
            now,
            agreement=_j(agreement),
            agreed_video_count=body.agreed_video_count,
            agreed=True,
        )
        _event(
            s,
            collab_id,
            "agreement",
            now,
            from_status=r["status"],
            to_status="agreed",
            note=f"约定 {body.agreed_video_count} 条新视频；{body.terms_note}",
        )
        _touch_relationship(s, r, now, first_contact=True)
        return _detail(s, collab_id)


# ---------------- 寄样 ----------------

T_SHIP = "sample_shipments"


def _ship_row(s: Session, sid: str):
    row = s.execute(text("SELECT * FROM sample_shipments WHERE id=:id"), {"id": sid}).mappings().first()
    if not row:
        raise _nf("寄样单")
    return row


def _recipient(row) -> dict:
    return crypto.decrypt(row["recipient_enc"], table=T_SHIP, column="recipient_enc", row_id=row["id"])


def _tracking(row) -> str | None:
    if not row["tracking_enc"]:
        return None
    return crypto.decrypt(row["tracking_enc"], table=T_SHIP, column="tracking_enc", row_id=row["id"])


def _items_total(items: list[dict]) -> str | None:
    if any(i.get("unit_cost") is None for i in items):
        return None
    total = sum((Decimal(i["unit_cost"]) * i["quantity"] for i in items), Decimal(0))
    return format(total.normalize(), "f")


def _payload(row, recipient: dict) -> dict:
    """确认所冻结的内容：收件人、全部明细、费用上限、用途（所属合作）。"""
    return {
        "shipment_id": row["id"],
        "collaboration_id": row["collaboration_id"],
        "payload_version": row["payload_version"],
        "kind": row["kind"],
        "items": json.loads(row["items"]),
        "cost_cap_amount": row["cost_cap_amount"],
        "currency": row["currency"],
        "recipient": recipient,
    }


def _masked_summary(s: Session, row, recipient: dict) -> dict:
    co = _collab_row(s, row["collaboration_id"])
    items = json.loads(row["items"])
    return {
        "creator": co["unique_id"] or co["nickname"],
        "campaign": co["campaign_name"],
        "market_code": co["market_code"],
        "product": f"{co['product_name']} v{co['product_version_no']}",
        "kind": row["kind"],
        "items": items,
        "items_cost_total": _items_total(items),
        "cost_cap_amount": row["cost_cap_amount"],
        "currency": row["currency"],
        "recipient": mask_recipient(recipient),
        "recipient_country": recipient.get("country_code"),
        "payload_version": row["payload_version"],
    }


def _active_confirmation(s: Session, sid: str, version: int):
    return (
        s.execute(
            text(
                """SELECT ac.* FROM action_confirmations ac JOIN external_actions a ON a.id = ac.action_id
               WHERE a.action_kind='sample_shipment' AND a.target_id=:s AND ac.payload_version=:v
                 AND ac.revoked_at IS NULL ORDER BY ac.confirmed_at DESC LIMIT 1"""
            ),
            {"s": sid, "v": version},
        )
        .mappings()
        .first()
    )


def _action(s: Session, sid: str, version: int):
    return (
        s.execute(
            text(
                """SELECT * FROM external_actions WHERE action_kind='sample_shipment' AND target_id=:s
               AND payload_version=:v"""
            ),
            {"s": sid, "v": version},
        )
        .mappings()
        .first()
    )


def _shipment_view(s: Session, sid: str) -> ShipmentView:
    row = _ship_row(s, sid)
    try:
        masked = mask_recipient(_recipient(row))
        tracking = mask_tracking(_tracking(row))
    except crypto.PiiKeyError:
        masked, tracking = "（本机无法解密收件信息）", None
    items = json.loads(row["items"])
    conf = _active_confirmation(s, sid, row["payload_version"])
    action = _action(s, sid, row["payload_version"])
    now = utcnow_iso()
    events = (
        s.execute(
            text("SELECT * FROM shipment_events WHERE shipment_id=:s ORDER BY occurred_at, observed_at"),
            {"s": sid},
        )
        .mappings()
        .all()
    )
    status = row["status"]
    if status == "draft":
        nxt = "preview"
    elif status == "awaiting_confirmation":
        nxt = "confirm"
    elif status == "confirmed":
        nxt = "register_dispatch"
    elif status == "dispatched" and row["delivery_status"] in ("unknown", "in_transit"):
        nxt = "record_delivery"
    else:
        nxt = None
    return ShipmentView(
        id=row["id"],
        collaboration_id=row["collaboration_id"],
        kind=row["kind"],
        status=status,
        delivery_status=row["delivery_status"],
        items=[ShipmentItem(**i) for i in items],
        items_cost_total=_items_total(items),
        cost_cap_amount=row["cost_cap_amount"],
        currency=row["currency"],
        recipient_masked=masked,
        recipient_country=row["recipient_country"],
        note=row["note"],
        payload_version=row["payload_version"],
        carrier=row["carrier"],
        tracking_masked=tracking,
        dispatched_at=row["dispatched_at"],
        delivered_at=row["delivered_at"],
        action_status=action["status"] if action else None,
        confirmation=ConfirmationView(
            id=conf["id"],
            payload_version=conf["payload_version"],
            confirmed_at=conf["confirmed_at"],
            expires_at=conf["expires_at"],
            consumed_at=conf["consumed_at"],
            revoked_at=conf["revoked_at"],
            valid=conf["consumed_at"] is None and conf["expires_at"] > now,
        )
        if conf
        else None,
        events=[
            ShipmentEventView(
                id=e["id"],
                source=e["source"],
                status=e["status"],
                occurred_at=e["occurred_at"],
                observed_at=e["observed_at"],
                note=e["note"],
            )
            for e in events
        ],
        next_action=nxt,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_shipment(sid: str) -> ShipmentView:
    with tx() as s:
        return _shipment_view(s, sid)


def _shippable_collab(s: Session, collab_id: str):
    r = _lock_collab(s, collab_id, None)
    if r["status"] not in SHIPPABLE:
        raise CollabError("agreement_required", "需要先记录双方已达成的合作约定，才能安排寄样")
    return r


def create_shipment(collab_id: str, body: ShipmentIn, idem_key: str) -> tuple[ShipmentView, bool]:
    if body.recipient is None:
        raise CollabError("recipient_required", "请填写收件信息", 422)
    now = utcnow_iso()
    # 请求里有收件信息明文：幂等哈希用带密钥的 HMAC，数据库里不留可反推的哈希
    rhash = crypto.keyed_hash({"collaboration_id": collab_id, **body.model_dump(mode="json")})
    with tx() as s:
        existing = _idem_lookup(s, "shipment.create", idem_key, rhash)
        if existing:
            return _shipment_view(s, existing), False
        co = _shippable_collab(s, collab_id)
        sid = str(uuid.uuid4())
        s.execute(
            text(
                """INSERT INTO sample_shipments (id, collaboration_id, kind, status, delivery_status, items,
                                                cost_cap_amount, currency, recipient_enc, recipient_country, note,
                                                payload_version, created_at, updated_at)
                   VALUES (:id, :co, :k, 'draft', 'unknown', :items, :cap, :cur, :rec, :cc, :note, 1, :n, :n)"""
            ),
            {
                "id": sid,
                "co": collab_id,
                "k": body.kind,
                "items": _j([i.model_dump() for i in body.items]),
                "cap": body.cost_cap_amount,
                "cur": body.currency or co["reporting_currency"],
                "rec": crypto.encrypt(
                    body.recipient.model_dump(), table=T_SHIP, column="recipient_enc", row_id=sid
                ),
                "cc": body.recipient.country_code,
                "note": body.note,
                "n": now,
            },
        )
        _idem_record(s, "shipment.create", idem_key, rhash, sid, now)
        _event(s, collab_id, "shipment", now, note="创建寄样单（草稿，待核对确认）", ref_id=sid)
        return _shipment_view(s, sid), True


def _revoke(s: Session, sid: str, version: int, now: str) -> None:
    s.execute(
        text(
            """UPDATE action_confirmations SET revoked_at=:n
               WHERE revoked_at IS NULL AND consumed_at IS NULL AND action_id IN
                 (SELECT id FROM external_actions WHERE action_kind='sample_shipment' AND target_id=:s
                  AND payload_version=:v)"""
        ),
        {"n": now, "s": sid, "v": version},
    )
    s.execute(
        text(
            """UPDATE external_actions SET status='cancelled', updated_at=:n
               WHERE action_kind='sample_shipment' AND target_id=:s AND payload_version=:v
                 AND status IN ('draft','awaiting_confirmation','confirmed')"""
        ),
        {"n": now, "s": sid, "v": version},
    )


def update_shipment(sid: str, body: ShipmentIn) -> ShipmentView:
    now = utcnow_iso()
    with tx() as s:
        row = _ship_row(s, sid)
        co = _shippable_collab(s, row["collaboration_id"])
        if row["status"] not in ACTIVE_SHIPMENT:
            raise CollabError("shipment_locked", "寄样单已寄出或已取消，不能修改")
        rec = body.recipient.model_dump() if body.recipient else _recipient(row)
        _revoke(s, sid, row["payload_version"], now)
        res = s.execute(
            text(
                """UPDATE sample_shipments SET kind=:k, items=:items, cost_cap_amount=:cap, currency=:cur,
                          recipient_enc=:rec, recipient_country=:cc, note=:note, status='draft',
                          payload_version=payload_version+1, updated_at=:n
                   WHERE id=:id AND payload_version=:v AND status IN ('draft','awaiting_confirmation','confirmed')"""
            ),
            {
                "id": sid,
                "v": row["payload_version"],
                "k": body.kind,
                "items": _j([i.model_dump() for i in body.items]),
                "cap": body.cost_cap_amount,
                "cur": body.currency or co["reporting_currency"],
                "rec": crypto.encrypt(rec, table=T_SHIP, column="recipient_enc", row_id=sid),
                "cc": rec.get("country_code"),
                "note": body.note,
                "n": now,
            },
        )
        if res.rowcount != 1:
            raise CollabError("stale_revision", "寄样单已在别处更新，请刷新后再操作")
        note = "修改寄样单，之前的确认已作废，需要重新核对确认" if row["status"] != "draft" else "修改寄样单"
        _event(s, row["collaboration_id"], "shipment", now, note=note, ref_id=sid)
        return _shipment_view(s, sid)


def preview_confirmation(sid: str) -> ConfirmationPreview:
    now = utcnow_iso()
    with tx() as s:
        row = _ship_row(s, sid)
        _shippable_collab(s, row["collaboration_id"])
        if row["status"] == "confirmed":
            raise CollabError("already_confirmed", "这张寄样单已经确认过；如需修改请先编辑（会使确认失效）")
        if row["status"] not in ("draft", "awaiting_confirmation"):
            raise CollabError("shipment_locked", "寄样单已寄出或已取消")
        recipient = _recipient(row)
        phash = crypto.keyed_hash(_payload(row, recipient))
        action = _action(s, sid, row["payload_version"])
        if action is None:
            s.execute(
                text(
                    """INSERT INTO external_actions (id, action_kind, collaboration_id, target_id, channel,
                                                    payload_version, payload_hash, status, created_at, updated_at)
                       VALUES (:id, 'sample_shipment', :co, :s, 'manual', :v, :h, 'awaiting_confirmation', :n, :n)"""
                ),
                {
                    "id": str(uuid.uuid4()),
                    "co": row["collaboration_id"],
                    "s": sid,
                    "v": row["payload_version"],
                    "h": phash,
                    "n": now,
                },
            )
        else:
            s.execute(
                text(
                    "UPDATE external_actions SET payload_hash=:h, status='awaiting_confirmation', updated_at=:n "
                    "WHERE id=:id"
                ),
                {"h": phash, "n": now, "id": action["id"]},
            )
        s.execute(
            text(
                "UPDATE sample_shipments SET status='awaiting_confirmation', updated_at=:n "
                "WHERE id=:id AND status='draft'"
            ),
            {"n": now, "id": sid},
        )
        return ConfirmationPreview(
            shipment_id=sid,
            payload_version=row["payload_version"],
            payload_hash=phash,
            summary=_masked_summary(s, row, recipient),
            expires_in_days=CONFIRM_VALID_DAYS,
        )


def confirm_shipment(sid: str, body: ConfirmIn, idem_key: str) -> tuple[ShipmentView, bool]:
    now = utcnow_iso()
    rhash = jobs.request_hash({"shipment_id": sid, **body.model_dump()})
    with tx() as s:
        if _idem_lookup(s, "shipment.confirm", idem_key, rhash):
            return _shipment_view(s, sid), False
        row = _ship_row(s, sid)
        _shippable_collab(s, row["collaboration_id"])
        if row["status"] == "confirmed" and row["payload_version"] == body.payload_version:
            raise CollabError("already_confirmed", "这张寄样单已经确认过，不会重复确认")
        if row["status"] != "awaiting_confirmation":
            raise CollabError("not_previewed", "请先核对寄样内容（生成确认摘要）再确认")
        action = _action(s, sid, row["payload_version"])
        current = crypto.keyed_hash(_payload(row, _recipient(row)))
        if (
            body.payload_version != row["payload_version"]
            or action is None
            or body.payload_hash != action["payload_hash"]
            or body.payload_hash != current
        ):
            raise CollabError("payload_changed", "寄样内容在核对后发生了变化，请重新核对后再确认")
        # 比较并交换：并发的两次确认只有一次能成功
        res = s.execute(
            text(
                """UPDATE sample_shipments SET status='confirmed', updated_at=:n
                   WHERE id=:id AND status='awaiting_confirmation' AND payload_version=:v"""
            ),
            {"n": now, "id": sid, "v": body.payload_version},
        )
        if res.rowcount != 1:
            raise CollabError("already_confirmed", "这张寄样单已经确认过，不会重复确认")
        conf_id = str(uuid.uuid4())
        expires = _iso(datetime.now(UTC) + timedelta(days=CONFIRM_VALID_DAYS))
        s.execute(
            text(
                """INSERT INTO action_confirmations (id, action_id, payload_version, payload_hash, summary,
                                                    confirmed_at, expires_at)
                   VALUES (:id, :a, :v, :h, :sum, :n, :exp)"""
            ),
            {
                "id": conf_id,
                "a": action["id"],
                "v": body.payload_version,
                "h": body.payload_hash,
                "sum": _j(_masked_summary(s, row, _recipient(row))),
                "n": now,
                "exp": expires,
            },
        )
        s.execute(
            text(
                "UPDATE external_actions SET status='confirmed', confirmation_id=:c, updated_at=:n WHERE id=:id"
            ),
            {"c": conf_id, "n": now, "id": action["id"]},
        )
        _idem_record(s, "shipment.confirm", idem_key, rhash, sid, now)
        _event(s, row["collaboration_id"], "shipment", now, note="本人确认寄样", ref_id=sid)
        return _shipment_view(s, sid), True


def register_dispatch(sid: str, body: DispatchIn, idem_key: str) -> tuple[ShipmentView, bool]:
    now = utcnow_iso()
    rhash = crypto.keyed_hash({"shipment_id": sid, **body.model_dump(mode="json")})
    with tx() as s:
        if _idem_lookup(s, "shipment.dispatch", idem_key, rhash):
            return _shipment_view(s, sid), False
        row = _ship_row(s, sid)
        co = _shippable_collab(s, row["collaboration_id"])
        if row["status"] == "dispatched":
            raise CollabError("already_dispatched", "这张寄样单已经登记寄出，不会重复寄样")
        if row["status"] != "confirmed":
            raise CollabError("not_confirmed", "寄样需要先由本人确认")
        _check_not_future(body.dispatched_at, "寄出时间")
        conf = _active_confirmation(s, sid, row["payload_version"])
        if conf is None or conf["consumed_at"] is not None:
            raise CollabError("not_confirmed", "没有可用的寄样确认，请重新核对确认")
        if conf["expires_at"] > now:
            return _dispatch_confirmed(s, row, co, conf, body, idem_key, rhash, now)
        expired_version = row["payload_version"]
    # 确认过期：在单独的事务里作废确认、退回草稿（上面的事务没有写入），再报错
    with tx() as s:
        _revoke(s, sid, expired_version, now)
        s.execute(
            text(
                "UPDATE sample_shipments SET status='draft', updated_at=:n WHERE id=:id AND status='confirmed'"
            ),
            {"n": now, "id": sid},
        )
    raise CollabError("confirmation_expired", "确认已超过 7 天有效期，请重新核对确认")


def _dispatch_confirmed(s: Session, row, co, conf, body: DispatchIn, idem_key: str, rhash: str, now: str):
    """在已校验的确认上登记寄出（调用方持有事务）。"""
    sid = row["id"]
    if crypto.keyed_hash(_payload(row, _recipient(row))) != conf["payload_hash"]:
        raise CollabError("payload_changed", "寄样内容与确认时不一致，请重新核对确认")
    res = s.execute(
        text(
            """UPDATE sample_shipments SET status='dispatched', carrier=:car, tracking_enc=:tr,
                      dispatched_at=:d, updated_at=:n
               WHERE id=:id AND status='confirmed' AND payload_version=:v"""
        ),
        {
            "car": body.carrier,
            "tr": crypto.encrypt(body.tracking_number, table=T_SHIP, column="tracking_enc", row_id=sid)
            if body.tracking_number
            else None,
            "d": _iso(body.dispatched_at),
            "n": now,
            "id": sid,
            "v": row["payload_version"],
        },
    )
    if res.rowcount != 1:
        raise CollabError("already_dispatched", "这张寄样单已经登记寄出，不会重复寄样")
    s.execute(
        text("UPDATE action_confirmations SET consumed_at=:n WHERE id=:id AND consumed_at IS NULL"),
        {"n": now, "id": conf["id"]},
    )
    s.execute(
        text(
            "UPDATE external_actions SET status='succeeded', result=:r, updated_at=:n "
            "WHERE action_kind='sample_shipment' AND target_id=:s AND payload_version=:v"
        ),
        {
            "r": _j({"mode": "manual", "note": "人工登记已寄出，没有调用物流接口", "registered_at": now}),
            "n": now,
            "s": sid,
            "v": row["payload_version"],
        },
    )
    s.execute(
        text(
            "UPDATE sample_shipments SET external_action_id=(SELECT id FROM external_actions WHERE action_kind='sample_shipment' AND target_id=:s AND payload_version=:v) WHERE id=:s"
        ),
        {"s": sid, "v": row["payload_version"]},
    )
    _idem_record(s, "shipment.dispatch", idem_key, rhash, sid, now)
    _event(
        s,
        row["collaboration_id"],
        "shipment",
        now,
        note=f"登记已寄出{('（' + body.carrier + '）') if body.carrier else ''}；签收状态未知，等待物流节点",
        ref_id=sid,
    )
    if co["status"] == "agreed":
        _set_status(s, co, "in_progress", now, started=True)
        _event(
            s,
            row["collaboration_id"],
            "transition",
            now,
            from_status="agreed",
            to_status="in_progress",
            note="样品已寄出",
        )
    return _shipment_view(s, sid), True


def _project_delivery(s: Session, sid: str, now: str) -> None:
    latest = s.execute(
        text(
            """SELECT status, occurred_at FROM shipment_events WHERE shipment_id=:s
               ORDER BY occurred_at DESC, observed_at DESC LIMIT 1"""
        ),
        {"s": sid},
    ).first()
    status = latest.status if latest else "unknown"
    delivered = latest.occurred_at if latest and latest.status == "delivered" else None
    s.execute(
        text("UPDATE sample_shipments SET delivery_status=:st, delivered_at=:d, updated_at=:n WHERE id=:id"),
        {"st": status, "d": delivered, "n": now, "id": sid},
    )


def add_shipment_event(sid: str, body: ShipmentEventIn) -> ShipmentView:
    now = utcnow_iso()
    with tx() as s:
        row = _ship_row(s, sid)
        _lock_collab(s, row["collaboration_id"], None)
        if row["status"] != "dispatched":
            raise CollabError("not_dispatched", "登记寄出后才能记录物流节点")
        _check_not_future(body.occurred_at, "发生时间")
        occurred = _iso(body.occurred_at)
        if row["dispatched_at"] and occurred < row["dispatched_at"][:10]:
            raise CollabError("before_dispatch", "物流节点不能早于寄出日期", 422)
        s.execute(
            text(
                """INSERT INTO shipment_events (id, shipment_id, source, status, occurred_at, observed_at, note)
                   VALUES (:id, :s, 'manual', :st, :o, :n, :note)"""
            ),
            {
                "id": str(uuid.uuid4()),
                "s": sid,
                "st": body.status,
                "o": occurred,
                "n": now,
                "note": body.note,
            },
        )
        _project_delivery(s, sid, now)
        labels = {
            "in_transit": "运输中",
            "delivered": "已签收",
            "exception": "物流异常",
            "returned": "已退回",
        }
        _event(
            s, row["collaboration_id"], "shipment", now, note=f"物流节点：{labels[body.status]}", ref_id=sid
        )
        return _shipment_view(s, sid)


def _cancel_shipment(s: Session, sid: str, now: str, note: str) -> None:
    row = _ship_row(s, sid)
    _revoke(s, sid, row["payload_version"], now)
    s.execute(
        text(
            """UPDATE sample_shipments SET status='cancelled', updated_at=:n
               WHERE id=:id AND status IN ('draft','awaiting_confirmation','confirmed')"""
        ),
        {"n": now, "id": sid},
    )
    _event(s, row["collaboration_id"], "shipment", now, note=note, ref_id=sid)


def cancel_shipment(sid: str) -> ShipmentView:
    now = utcnow_iso()
    with tx() as s:
        row = _ship_row(s, sid)
        _lock_collab(s, row["collaboration_id"], None)
        if row["status"] not in ACTIVE_SHIPMENT:
            raise CollabError("shipment_locked", "已寄出或已取消的寄样单不能取消；寄出后的问题请记录物流节点")
        _cancel_shipment(s, sid, now, "取消寄样单，确认作废")
        return _shipment_view(s, sid)


def reveal_recipient(sid: str) -> RecipientView:
    with tx() as s:
        row = _ship_row(s, sid)
        try:
            return RecipientView(recipient=RecipientIn(**_recipient(row)), tracking_number=_tracking(row))
        except crypto.PiiKeyError as e:
            raise CollabError("pii_key_unavailable", str(e)) from e


# ---------------- 给其他模块用的公开函数（production 等） ----------------


def collab_row(s: Session, collab_id: str):
    """合作行（含达人、任务站点、产品版本信息）。"""
    return _collab_row(s, collab_id)


def lock_collab(s: Session, collab_id: str):
    """要修改合作下的数据时调用：任务未归档，且合作处于“已达成约定 / 合作进行中”。"""
    r = _lock_collab(s, collab_id, None)
    if r["status"] not in SHIPPABLE:
        raise CollabError("not_active", "合作需要处于“已达成约定”或“合作进行中”")
    return r


def add_event(s: Session, collab_id: str, kind: str, note: str, ref_id: str | None = None) -> None:
    """写一条合作历史（算作一次进展，跟进提醒重新计时）。"""
    now = utcnow_iso()
    _event(s, collab_id, kind, now, note=note, ref_id=ref_id)
    s.execute(text("UPDATE collaborations SET updated_at=:n WHERE id=:id"), {"n": now, "id": collab_id})
