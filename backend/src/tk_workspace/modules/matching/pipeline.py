"""匹配流程（LangGraph 最小图）与后台任务处理函数。

    load_context → discover → hard_filter → assess → persist

- 图状态只放 run_id 和停止原因；业务数据都在数据库里，每个节点都可重入：
  重复执行时，FastMoss 调用按请求哈希复用，候选与证据按 (run, creator) 覆盖写，推荐快照每次运行只写一次。
- 每次调用 FastMoss 前检查费用上限与取消请求。
- 应用中途被关闭后，任务执行器重新排队，本流程从头再跑一遍，已完成的调用不会重复发出。
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import text

from tk_workspace.integrations.fastmoss import creator_search as cs
from tk_workspace.modules.campaigns.criteria import FIELDS
from tk_workspace.modules.matching import assess, provider
from tk_workspace.modules.matching.evaluate import (
    DISPLAY_METRICS,
    aggregate,
    build_observations,
    describe_actual,
    evaluate_rule,
)
from tk_workspace.platform.db.engine import tx
from tk_workspace.platform.db.types import utcnow_iso
from tk_workspace.platform.jobs.registry import JobKind, JobRuntime, register

GRAPH_VERSION = "m2-graph-1"
RANKING_VERSION = "m2-rank-2"
MAX_POOL = 200
MAX_PAGES_PER_QUERY = 20
MAX_ASSESS = 40
ASSESS_WORKERS = 3

STOP_MESSAGES = {
    "pool_reached": "候选数量已足够",
    "exhausted": "搜索结果已全部取完",
    "cost_cap_reached": "已达到本任务的 FastMoss 额度上限，停止继续搜索，结果可能不完整",
    "insufficient_credits": "FastMoss 账户额度不足，已停止；请充值后重新启动",
    "provider_outcome_unknown": "有一次 FastMoss 调用结果未知（超时或中断），为避免重复扣费已停止",
    "provider_failed": "FastMoss 连续调用失败，已停止",
    "manual_import": "人工导入名单，非实时数据",
}
PARTIAL_REASONS = {"cost_cap_reached", "insufficient_credits", "provider_outcome_unknown", "provider_failed"}
GROUP_ORDER = {"qualified": 0, "needs_verification": 1, "excluded": 2}
_SOFT_STATUS_ORDER = {"pass": 0, "unknown": 1, "fail": 2}


def soft_tiebreak(rules: list[dict[str, Any]]) -> tuple[int, ...]:
    """软条件得分相同时的次序：按条件填写顺序逐条比较，先通过靠前条件的排前面（未知介于通过和不通过之间）。"""
    return tuple(_SOFT_STATUS_ORDER.get(r["status"], 1) for r in rules if r["hardness"] == "soft")


FIT_ORDER = {"high": 0, "medium": 1, "unknown": 2, None: 2, "low": 3}


class Cancelled(Exception):
    pass


class RunError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class State(TypedDict, total=False):
    run_id: str
    stop_reason: str | None


@dataclass
class Ctx:
    rt: JobRuntime | None
    data: dict[str, Any] = field(default_factory=dict)
    client: Any = None


def _ctx(config: RunnableConfig) -> Ctx:
    return config["configurable"]["ctx"]


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _progress(ctx: Ctx, run_id: str, stage: str, percent: int, message: str, **counters: Any) -> None:
    with tx() as s:
        row = s.execute(text("SELECT counters FROM matching_runs WHERE id=:id"), {"id": run_id}).first()
        cur = json.loads(row.counters) if row else {}
        cur.update(counters)
        s.execute(
            text("UPDATE matching_runs SET stage=:st, counters=:c, updated_at=:n WHERE id=:id"),
            {"st": stage, "c": _j(cur), "n": utcnow_iso(), "id": run_id},
        )
    if ctx.rt is not None and not ctx.rt.report_progress(
        {"percent": percent, "stage": stage, "message": message, **cur}
    ):
        raise Cancelled()


# ---------------- 节点 ----------------


def load_context(state: State, config: RunnableConfig) -> State:
    ctx, run_id = _ctx(config), state["run_id"]
    with tx() as s:
        run = s.execute(text("SELECT * FROM matching_runs WHERE id=:id"), {"id": run_id}).mappings().first()
        if run is None:
            raise RunError("run_not_found", "匹配运行不存在")
        cv = s.execute(
            text("SELECT criteria, search FROM criteria_versions WHERE id=:id"),
            {"id": run["criteria_version_id"]},
        ).first()
        pv = s.execute(
            text("SELECT facts FROM product_versions WHERE id=:id"), {"id": run["product_version_id"]}
        ).first()
        term = s.execute(
            text(
                """SELECT price_status, price_amount, price_currency FROM product_market_terms
                   WHERE product_version_id=:p AND market_code=:m"""
            ),
            {"p": run["product_version_id"], "m": run["market_code"]},
        ).first()
        mk = s.execute(
            text("SELECT name_zh, content_languages FROM markets WHERE market_code=:m"),
            {"m": run["market_code"]},
        ).first()
        camp = s.execute(
            text(
                """SELECT c.goal, c.collaboration_type, p.name FROM campaign_markets cm
                   JOIN campaigns c ON c.id=cm.campaign_id JOIN products p ON p.id=c.product_id
                   WHERE cm.id=:id"""
            ),
            {"id": run["campaign_market_id"]},
        ).first()
        s.execute(
            text(
                """UPDATE matching_runs SET status='running', started_at=COALESCE(started_at, :n), updated_at=:n
                   WHERE id=:id"""
            ),
            {"n": utcnow_iso(), "id": run_id},
        )
    facts = json.loads(pv.facts)
    price_known = term is not None and term.price_status == "known"
    ctx.data = {
        "run": dict(run),
        "criteria": json.loads(cv.criteria),
        "keywords": json.loads(cv.search).get("keywords", []),
        "price": term.price_amount if price_known else None,
        "price_currency": term.price_currency if term else None,
        "task": {
            "product_name": camp.name,
            "product": {
                k: facts.get(k)
                for k in ("summary", "selling_points", "use_scenarios", "forbidden_claims", "category")
                if facts.get(k)
            },
            "market": {
                "code": run["market_code"],
                "name": mk.name_zh,
                "languages": json.loads(mk.content_languages),
            },
            "goal": camp.goal,
            "collaboration_type": camp.collaboration_type,
            "price": f"{term.price_amount} {term.price_currency}" if price_known else "未知",
        },
    }
    _progress(ctx, run_id, "load_context", 2, "准备中")
    return {}


def upsert_candidate(s, run_id: str, rec: dict[str, Any], discovery: dict[str, Any], now: str) -> str | None:
    uid, handle = rec.get("provider_uid"), rec.get("unique_id")
    if handle:
        handle = handle.lstrip("@").strip()
    if not uid and not handle:
        return None
    row = None
    if uid:
        row = s.execute(
            text("SELECT id FROM creators WHERE platform='tiktok' AND provider_uid=:u"), {"u": uid}
        ).first()
    if row is None and handle:
        row = s.execute(
            text(
                """SELECT id FROM creators WHERE platform='tiktok' AND lower(unique_id)=lower(:h)
                   AND (provider_uid IS NULL OR :u IS NULL OR provider_uid=:u)"""
            ),
            {"h": handle, "u": uid},
        ).first()
    if row is None:
        creator_id = str(uuid.uuid4())
        s.execute(
            text(
                """INSERT INTO creators (id, platform, provider_uid, unique_id, nickname, region, first_seen_at,
                                         last_seen_at)
                   VALUES (:id, 'tiktok', :u, :h, :nk, :rg, :n, :n)"""
            ),
            {
                "id": creator_id,
                "u": uid,
                "h": handle,
                "nk": rec.get("nickname"),
                "rg": rec.get("region"),
                "n": now,
            },
        )
    else:
        creator_id = row.id
        s.execute(
            text(
                """UPDATE creators SET provider_uid=COALESCE(provider_uid, :u), unique_id=COALESCE(:h, unique_id),
                          nickname=COALESCE(:nk, nickname), region=COALESCE(:rg, region), last_seen_at=:n
                   WHERE id=:id"""
            ),
            {
                "id": creator_id,
                "u": uid,
                "h": handle,
                "nk": rec.get("nickname"),
                "rg": rec.get("region"),
                "n": now,
            },
        )
    ev = s.execute(
        text("SELECT id, discovery FROM candidate_evaluations WHERE run_id=:r AND creator_id=:c"),
        {"r": run_id, "c": creator_id},
    ).first()
    if ev is None:
        s.execute(
            text(
                """INSERT INTO candidate_evaluations (id, run_id, creator_id, discovery, observations, rule_results,
                                                     hard_status, group_key, created_at, updated_at)
                   VALUES (:id, :r, :c, :d, :o, '[]', 'unknown', 'pending', :n, :n)"""
            ),
            {
                "id": str(uuid.uuid4()),
                "r": run_id,
                "c": creator_id,
                "d": _j([discovery]),
                "o": _j({"record": rec}),
                "n": now,
            },
        )
    else:
        disc = json.loads(ev.discovery)
        if discovery not in disc:
            disc.append(discovery)
        s.execute(
            text("UPDATE candidate_evaluations SET discovery=:d, updated_at=:n WHERE id=:id"),
            {"d": _j(disc), "n": now, "id": ev.id},
        )
    return creator_id


def discover(state: State, config: RunnableConfig) -> State:
    ctx, run_id = _ctx(config), state["run_id"]
    run = ctx.data["run"]
    if run["source"] == "manual_import":
        return {"stop_reason": "manual_import"}
    hard_search = [
        c
        for c in ctx.data["criteria"]
        if c["hardness"] == "hard" and FIELDS[c["field_key"]].support == "search_filter"
    ]
    flt = cs.compile_filter(run["provider_region"], hard_search)
    keywords = ctx.data["keywords"] or [None]
    orderby = None if ctx.data["keywords"] else [cs.OrderBy(field="follower_count")]
    target = run["target_list_size"]
    pool_target = min(MAX_POOL, max(target * 3, 20))
    cap = run["cost_cap_credits"]

    exhausted = {i: False for i in range(len(keywords))}
    pages = {i: 0 for i in range(len(keywords))}
    seen: set[str] = set()
    failures, stop_reason = 0, None
    if ctx.client is None:
        ctx.client = provider.new_client()

    while stop_reason is None:
        active = [i for i in exhausted if not exhausted[i] and pages[i] < MAX_PAGES_PER_QUERY]
        if not active:
            stop_reason = "exhausted"
            break
        for i in active:
            if len(seen) >= pool_target:
                stop_reason = "pool_reached"
                break
            with tx() as s:
                used = s.execute(
                    text("SELECT credits_used FROM matching_runs WHERE id=:id"), {"id": run_id}
                ).scalar()
            if cap is not None and used + 1 > cap:
                stop_reason = "cost_cap_reached"
                break
            page = pages[i] + 1
            _progress(
                ctx,
                run_id,
                "discover",
                min(5 + len(seen) * 45 // pool_target, 50),
                f"搜索第 {page} 页",
                candidates=len(seen),
                credits_used=used,
            )
            params = cs.CreatorSearchParams(filter=flt, keywords=keywords[i], orderby=orderby, page=page)
            out = provider.call(run_id, "creator.search", params, ctx.client)
            pages[i] = page
            if out.status == "unauthorized":
                raise RunError("fastmoss_unauthorized", "FastMoss 拒绝了 API Key，请到设置页检查")
            if out.status == "insufficient_credits":
                stop_reason = "insufficient_credits"
                break
            if out.status == "unknown":
                stop_reason = "provider_outcome_unknown"
                break
            if out.status in ("failed", "rate_limited"):
                failures += 1
                if failures >= 2:
                    stop_reason = "provider_failed"
                    break
                continue
            failures = 0
            now = utcnow_iso()
            with tx() as s:
                for pos, rec in enumerate(out.records):
                    if flt.is_ecommerce_creator is not None:
                        rec = {**rec, "filter_guaranteed": {"is_ecommerce_creator": flt.is_ecommerce_creator}}
                    cid = upsert_candidate(
                        s,
                        run_id,
                        rec,
                        {"keyword": keywords[i], "page": page, "position": pos + 1, "call_id": out.call_id},
                        now,
                    )
                    if cid:
                        seen.add(cid)
            if len(out.records) < cs.PAGE_SIZE or (
                out.total is not None and page * cs.PAGE_SIZE >= out.total
            ):
                exhausted[i] = True
    with tx() as s:
        s.execute(
            text("UPDATE matching_runs SET stop_reason=:r WHERE id=:id"), {"r": stop_reason, "id": run_id}
        )
        used = s.execute(text("SELECT credits_used FROM matching_runs WHERE id=:id"), {"id": run_id}).scalar()
    _progress(ctx, run_id, "discover", 50, "搜索完成", candidates=len(seen), credits_used=used)
    return {"stop_reason": stop_reason}


def hard_filter(state: State, config: RunnableConfig) -> State:
    ctx, run_id = _ctx(config), state["run_id"]
    run = ctx.data["run"]
    now = utcnow_iso()
    counts = {"qualified": 0, "needs_verification": 0, "excluded": 0}
    with tx() as s:
        rows = s.execute(
            text("SELECT id, creator_id, observations FROM candidate_evaluations WHERE run_id=:r"),
            {"r": run_id},
        ).all()
        s.execute(text("DELETE FROM evidence_items WHERE run_id=:r"), {"r": run_id})
        for row in rows:
            rec = json.loads(row.observations)["record"]
            obs = build_observations(
                rec, run["reporting_currency"], ctx.data["price"], ctx.data["price_currency"]
            )
            for key, o in obs.items():
                eid = str(uuid.uuid4())
                o["evidence_id"] = eid
                s.execute(
                    text(
                        """INSERT INTO evidence_items (id, run_id, creator_id, kind, key, value, currency, state,
                                                       locator, fetched_at)
                           VALUES (:id, :r, :c, 'metric', :k, :v, :cur, :st, :loc, :n)"""
                    ),
                    {
                        "id": eid,
                        "r": run_id,
                        "c": row.creator_id,
                        "k": key,
                        "v": None if o["value"] is None else str(o["value"]),
                        "cur": o.get("currency"),
                        "st": o["state"],
                        "loc": f"snapshot:/metrics/{key}",
                        "n": now,
                    },
                )
            rules = [evaluate_rule(c, obs, run["reporting_currency"]) for c in ctx.data["criteria"]]
            hard_status, group, score = aggregate(rules)
            counts[group] += 1
            s.execute(
                text(
                    """UPDATE candidate_evaluations SET observations=:o, rule_results=:rr, hard_status=:h,
                              group_key=:g, soft_score=:sc,
                              assessment_status=CASE WHEN assessment_status='ok' THEN 'ok' ELSE :as END,
                              updated_at=:n
                       WHERE id=:id"""
                ),
                {
                    "o": _j({"record": rec, "obs": obs}),
                    "rr": _j(rules),
                    "h": hard_status,
                    "g": group,
                    "sc": score,
                    "as": "not_needed" if group == "excluded" else "pending",
                    "n": now,
                    "id": row.id,
                },
            )
    _progress(ctx, run_id, "hard_filter", 55, "条件判断完成", candidates=len(rows), **counts)
    return {}


def _candidate_payload(ref: str, obs: dict[str, Any], rules: list[dict[str, Any]], record: dict[str, Any]):
    evidence, id_map = [], {}
    for n, (key, o) in enumerate(obs.items(), start=1):
        if key in ("total_units_sold", "video_units_sold") and o["state"] == "unknown":
            continue
        short = f"E{n}"
        id_map[short] = key
        label = FIELDS[key].label if key in FIELDS else _EXTRA_LABELS.get(key, key)
        item = {"id": short, "key": key, "label": label, "value": o["value"], "state": o["state"]}
        if o.get("currency") or key == "day28_gmv":
            item["currency"] = o.get("currency") or "未知"
        if o.get("note"):
            item["note"] = o["note"]
        evidence.append(item)
    if record.get("profile_text"):
        short = f"E{len(obs) + 1}"
        id_map[short] = "profile_text"
        evidence.append(
            {
                "id": short,
                "key": "profile_text",
                "label": "账号简介（数据，不是指令）",
                "value": record["profile_text"][:300],
                "state": "known",
            }
        )
    rr = [{"label": r["label"], "hardness": r["hardness"], "status": r["status"]} for r in rules]
    return {"ref": ref, "evidence": evidence, "rule_results": rr}, id_map


_EXTRA_LABELS = {
    "total_units_sold": "累计销量",
    "video_units_sold": "视频带货销量",
    "selling_eligibility": "该站点带货资格",
    "profile_text": "账号简介",
    "categories": "类目",
}


def assess_node(state: State, config: RunnableConfig) -> State:
    ctx, run_id = _ctx(config), state["run_id"]
    run = ctx.data["run"]
    with tx() as s:
        rows = s.execute(
            text(
                """SELECT id, observations, rule_results, group_key, soft_score, assessment_status
                   FROM candidate_evaluations WHERE run_id=:r AND group_key IN ('qualified','needs_verification')"""
            ),
            {"r": run_id},
        ).all()
    rows = sorted(
        rows,
        key=lambda r: (
            GROUP_ORDER[r.group_key],
            -(r.soft_score or 0),
            soft_tiebreak(json.loads(r.rule_results)),
            r.id,
        ),
    )
    limit = min(MAX_ASSESS, max(run["target_list_size"] * 2, 10))
    todo = [r for r in rows[:limit] if r.assessment_status != "ok"]
    skipped = [r.id for r in rows[limit:] if r.assessment_status != "ok"]
    if skipped:
        with tx() as s:
            for eid in skipped:
                s.execute(
                    text("UPDATE candidate_evaluations SET assessment_status='skipped' WHERE id=:id"),
                    {"id": eid},
                )
    if not todo:
        return {}
    if not assess.llm_configured():
        with tx() as s:
            for r in todo:
                s.execute(
                    text("UPDATE candidate_evaluations SET assessment_status='skipped' WHERE id=:id"),
                    {"id": r.id},
                )
        _progress(ctx, run_id, "assess", 90, "未配置大模型，跳过 AI 判断", llm_configured=False)
        return {}

    batches = []
    for b in range(0, len(todo), assess.BATCH_SIZE):
        chunk = todo[b : b + assess.BATCH_SIZE]
        cands, maps = [], {}
        for n, r in enumerate(chunk, start=1):
            o = json.loads(r.observations)
            payload, id_map = _candidate_payload(f"C{n}", o["obs"], json.loads(r.rule_results), o["record"])
            cands.append(payload)
            maps[f"C{n}"] = (r.id, id_map, o["obs"], o["record"].get("profile_text"))
        batches.append((cands, maps))

    done = failed = 0
    _progress(ctx, run_id, "assess", 60, f"AI 判断 0/{len(todo)}", assessed=0, assess_total=len(todo))
    with ThreadPoolExecutor(max_workers=ASSESS_WORKERS) as pool:
        futures = {pool.submit(assess.assess_batch, ctx.data["task"], cands): maps for cands, maps in batches}
        try:
            for fut in as_completed(futures):
                maps = futures[fut]
                results, usages = fut.result()
                now = utcnow_iso()
                with tx() as s:
                    for ref, (eval_id, id_map, obs, profile) in maps.items():
                        res = results.get(ref)
                        if res is None:
                            failed += 1
                            s.execute(
                                text(
                                    "UPDATE candidate_evaluations SET assessment_status='failed', updated_at=:n WHERE id=:id"
                                ),
                                {"n": now, "id": eval_id},
                            )
                            continue
                        for part in ("fit_points", "concerns"):
                            for p in res[part]:
                                p["evidence"] = [
                                    _evidence_view(id_map[i], obs, profile) for i in p.pop("evidence_ids")
                                ]
                        done += 1
                        s.execute(
                            text(
                                """UPDATE candidate_evaluations SET assessment=:a, assessment_status='ok', updated_at=:n
                                   WHERE id=:id"""
                            ),
                            {"a": _j(res), "n": now, "id": eval_id},
                        )
                    tin = tout = 0
                    for u in usages:
                        if u.get("failed"):
                            continue
                        tin += u.get("input_tokens") or 0
                        tout += u.get("output_tokens") or 0
                        s.execute(
                            text(
                                """INSERT INTO usage_ledger (id, run_id, source, operation, model, prompt_version,
                                                            input_tokens, output_tokens, created_at)
                                   VALUES (:id, :r, 'llm', 'matching.assess', :m, :pv, :i, :o, :n)"""
                            ),
                            {
                                "id": str(uuid.uuid4()),
                                "r": run_id,
                                "m": u.get("model"),
                                "pv": assess.PROMPT_VERSION,
                                "i": u.get("input_tokens"),
                                "o": u.get("output_tokens"),
                                "n": now,
                            },
                        )
                    s.execute(
                        text(
                            """UPDATE matching_runs SET llm_input_tokens=llm_input_tokens+:i,
                                      llm_output_tokens=llm_output_tokens+:o WHERE id=:id"""
                        ),
                        {"i": tin, "o": tout, "id": run_id},
                    )
                _progress(
                    ctx,
                    run_id,
                    "assess",
                    60 + 35 * (done + failed) // len(todo),
                    f"AI 判断 {done + failed}/{len(todo)}",
                    assessed=done,
                    assess_failed=failed,
                )
        except Cancelled:
            for f in futures:
                f.cancel()
            raise
    return {}


def _evidence_view(key: str, obs: dict[str, Any], profile_text: str | None = None) -> dict[str, Any]:
    if key == "profile_text":
        text_ = (profile_text or "")[:40]
        return {
            "key": key,
            "label": "账号简介",
            "value": text_ + ("…" if len(profile_text or "") > 40 else ""),
        }
    o = obs.get(key, {})
    label = FIELDS[key].label if key in FIELDS else _EXTRA_LABELS.get(key, key)
    return {"key": key, "label": label, "value": describe_actual(key, o), "state": o.get("state")}


def build_card(row: Any, creator: Any) -> dict[str, Any]:
    o = json.loads(row.observations)
    obs, rec = o["obs"], o["record"]
    rules = json.loads(row.rule_results)
    a = json.loads(row.assessment) if row.assessment else None
    matches, mismatches, unknowns, anomalies, questions = [], [], [], [], []
    for r in rules:
        tag = "硬条件" if r["hardness"] == "hard" else "软条件"
        line = f"{r['label']}：{r['actual']}（要求 {r['expected']}）"
        if r["status"] == "pass":
            matches.append({"text": line, "source": "rule", "tag": tag})
        elif r["status"] == "fail":
            mismatches.append({"text": line, "source": "rule", "tag": tag})
        else:
            unknowns.append({"text": f"{r['label']}：未知 —— {r['reason']}", "tag": tag})
            if r["hardness"] == "hard":
                questions.append(f"请人工核实“{r['label']}”（要求 {r['expected']}）")
    criteria_keys = {r["field_key"] for r in rules}
    for key in ("selling_eligibility", "content_language"):
        if key not in criteria_keys:
            label = _EXTRA_LABELS.get(key) or FIELDS[key].label
            unknowns.append({"text": f"{label}：未知 —— {obs[key]['note']}", "tag": "默认"})
    if (
        "day28_gmv" not in criteria_keys
        and obs["day28_gmv"]["value"] is not None
        and not obs["day28_gmv"].get("currency")
    ):
        unknowns.append(
            {"text": "近 28 天 GMV 的币种未知（数据源未返回币种），不换算、不比较", "tag": "数据"}
        )
    seen_notes = set()
    for ob in obs.values():
        if ob["state"] in ("anomaly", "inconsistent") and ob["note"] not in seen_notes:
            seen_notes.add(ob["note"])
            anomalies.append(
                {"text": ob["note"], "keys": [k for k, x in obs.items() if x.get("note") == ob["note"]]}
            )
    if a:
        matches += [{"text": p["text"], "source": "ai", "evidence": p["evidence"]} for p in a["fit_points"]]
        mismatches += [{"text": p["text"], "source": "ai", "evidence": p["evidence"]} for p in a["concerns"]]
        questions += a["questions"]
    metrics = {}
    for key in DISPLAY_METRICS:
        ob = obs[key]
        metrics[key] = {
            "label": FIELDS[key].label,
            "value": ob["value"],
            "state": ob["state"],
            "currency": ob.get("currency"),
            "note": ob.get("note", ""),
        }
    handle = creator.unique_id
    return {
        "evaluation_id": row.id,
        "creator": {
            "id": creator.id,
            "unique_id": handle,
            "nickname": creator.nickname,
            "region": creator.region,
            "profile_url": f"https://www.tiktok.com/@{handle}" if handle else None,
        },
        "group": row.group_key,
        "hard_status": row.hard_status,
        "soft_score": row.soft_score,
        "metrics": metrics,
        "has_email": obs["has_email"]["value"],
        "matches": matches,
        "mismatches": mismatches,
        "unknowns": unknowns,
        "anomalies": anomalies,
        "questions": questions,
        "ai": {
            "status": row.assessment_status,
            "product_fit": a["product_fit"] if a else None,
            "summary": a["summary"] if a else None,
            "unsupported": a["unsupported"] if a else [],
        },
        "discovery": json.loads(row.discovery),
        "profile_text": rec.get("profile_text"),
        "categories": rec.get("categories"),
    }


def persist(state: State, config: RunnableConfig) -> State:
    ctx, run_id = _ctx(config), state["run_id"]
    run = ctx.data["run"]
    with tx() as s:
        if s.execute(text("SELECT 1 FROM recommendation_snapshots WHERE run_id=:r"), {"r": run_id}).first():
            return {}
        rows = s.execute(text("SELECT * FROM candidate_evaluations WHERE run_id=:r"), {"r": run_id}).all()
        creators = {
            c.id: c
            for c in s.execute(
                text(
                    """SELECT id, unique_id, nickname, region FROM creators
                       WHERE id IN (SELECT creator_id FROM candidate_evaluations WHERE run_id=:r)"""
                ),
                {"r": run_id},
            ).all()
        }
        cards = [build_card(r, creators[r.creator_id]) for r in rows]
        tiebreak = {r.id: soft_tiebreak(json.loads(r.rule_results)) for r in rows}
        cards.sort(
            key=lambda c: (
                GROUP_ORDER[c["group"]],
                FIT_ORDER.get(c["ai"]["product_fit"], 2),
                -(c["soft_score"] or 0),
                tiebreak[c["evaluation_id"]],
                (c["creator"]["unique_id"] or "").lower(),
            )
        )
        counts = {g: sum(1 for c in cards if c["group"] == g) for g in GROUP_ORDER}
        counts["total"] = len(cards)
        counts["assessed"] = sum(1 for c in cards if c["ai"]["status"] == "ok")
        counts["assessment_failed"] = sum(1 for c in cards if c["ai"]["status"] == "failed")
        stop_reason = state.get("stop_reason")
        limitations = []
        if stop_reason and stop_reason in STOP_MESSAGES and stop_reason not in ("pool_reached", "exhausted"):
            limitations.append(STOP_MESSAGES[stop_reason])
        if not cards:
            limitations.append("没有找到候选。可以更换关键词或放宽硬条件后重新确认任务再启动。")
        if any(c["ai"]["status"] == "skipped" for c in cards if c["group"] != "excluded"):
            limitations.append(
                "部分或全部候选没有 AI 判断（未配置大模型或超出本次判断数量），推荐卡只含规则判断"
            )
        if counts["assessment_failed"]:
            limitations.append(f"{counts['assessment_failed']} 位候选的 AI 判断失败，只显示规则判断")
        if any(
            c["metrics"]["day28_gmv"]["value"] is not None and not c["metrics"]["day28_gmv"]["currency"]
            for c in cards
        ):
            limitations.append("该站点部分 GMV 数据没有币种，显示为“币种未知”，不换算")
        limitations.append("带货资格默认未知：FastMoss 的地区是达人所在地，不证明具备该站点带货资格")
        versions = {
            "graph": GRAPH_VERSION,
            "ranking": RANKING_VERSION,
            "prompt": assess.PROMPT_VERSION,
            "normalizer": cs.SCHEMA_VERSION,
        }
        snap_id, now = str(uuid.uuid4()), utcnow_iso()
        s.execute(
            text(
                """INSERT INTO recommendation_snapshots (id, run_id, counts, versions, limitations, input_hash,
                                                         created_at)
                   VALUES (:id, :r, :c, :v, :l, :h, :n)"""
            ),
            {
                "id": snap_id,
                "r": run_id,
                "c": _j(counts),
                "v": _j(versions),
                "l": _j(limitations),
                "h": provider.request_hash(
                    {"criteria": run["criteria_version_id"], "product": run["product_version_id"]}
                ),
                "n": now,
            },
        )
        rank = {g: 0 for g in GROUP_ORDER}
        for c in cards:
            rank[c["group"]] += 1
            c["rank"] = rank[c["group"]]
            s.execute(
                text(
                    """INSERT INTO recommendation_items (id, snapshot_id, evaluation_id, group_key, rank, card)
                       VALUES (:id, :s, :e, :g, :rk, :c)"""
                ),
                {
                    "id": str(uuid.uuid4()),
                    "s": snap_id,
                    "e": c["evaluation_id"],
                    "g": c["group"],
                    "rk": c["rank"],
                    "c": _j(c),
                },
            )
        partial = (stop_reason in PARTIAL_REASONS) or counts["assessment_failed"] > 0
        s.execute(
            text(
                """UPDATE matching_runs SET status=:st, stage='done', finished_at=:n, updated_at=:n WHERE id=:id"""
            ),
            {"st": "partial" if partial else "succeeded", "n": now, "id": run_id},
        )
    _progress(
        ctx,
        run_id,
        "done",
        100,
        "完成",
        **{k: counts[k] for k in ("qualified", "needs_verification", "excluded")},
    )
    return {}


def _build_graph():
    g = StateGraph(State)
    g.add_node("load_context", load_context)
    g.add_node("discover", discover)
    g.add_node("hard_filter", hard_filter)
    g.add_node("assess", assess_node)
    g.add_node("persist", persist)
    g.add_edge(START, "load_context")
    g.add_edge("load_context", "discover")
    g.add_edge("discover", "hard_filter")
    g.add_edge("hard_filter", "assess")
    g.add_edge("assess", "persist")
    g.add_edge("persist", END)
    return g.compile()


GRAPH = _build_graph()


def _finish(run_id: str, status: str, error: dict[str, str] | None = None) -> None:
    now = utcnow_iso()
    with tx() as s:
        s.execute(
            text(
                """UPDATE matching_runs SET status=:st, error=:e, finished_at=:n, updated_at=:n
                   WHERE id=:id AND status NOT IN ('succeeded','partial')"""
            ),
            {"st": status, "e": _j(error) if error else None, "n": now, "id": run_id},
        )


def run_matching(rt: JobRuntime | None, params: dict[str, Any]) -> dict[str, Any]:
    run_id = params["run_id"]
    ctx = Ctx(rt=rt)
    try:
        GRAPH.invoke({"run_id": run_id}, config={"configurable": {"ctx": ctx}})
    except Cancelled:
        _finish(run_id, "cancelled")
        return {"run_id": run_id, "status": "cancelled"}
    except RunError as e:
        _finish(run_id, "failed", {"code": e.code, "message": str(e)})
        raise
    except Exception as e:
        _finish(
            run_id, "failed", {"code": "internal_error", "message": f"{type(e).__name__}: {str(e)[:200]}"}
        )
        raise
    finally:
        if ctx.client is not None:
            ctx.client.close()
    with tx() as s:
        st = s.execute(text("SELECT status FROM matching_runs WHERE id=:id"), {"id": run_id}).scalar()
    return {"run_id": run_id, "status": st}


register(
    JobKind(
        name="matching.run",
        queue="matching",
        handler=run_matching,
        user_creatable=False,
        max_params_bytes=512,
        retry_on_interrupt=True,  # 重跑时已完成的 FastMoss 调用会被复用，不重复扣费
        max_attempts=3,
    )
)
