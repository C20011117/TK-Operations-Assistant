"""M0 端到端验收：经由前端开发服务器（/api 代理）走一遍真实链路。

前提：./scripts/dev-up.sh 已执行，API（8000）、前端（5173）、Worker 与分发器在运行。
用法：cd backend && uv run python ../scripts/m0_acceptance.py [--base http://localhost:5173]
"""

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

import httpx


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in (Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def login(base: str, email: str, password: str) -> tuple[httpx.Client, dict]:
    c = httpx.Client(base_url=base, timeout=60)
    r = c.post("/api/v1/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    c.headers["X-CSRF-Token"] = c.cookies["tkws_csrf"]
    return c, r.json()


results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'通过' if ok else '失败'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:5173")
    args = ap.parse_args()
    pw = os.environ.get("SEED_DEV_PASSWORD") or load_env().get("SEED_DEV_PASSWORD", "")

    # 1. 两名 BD 登录
    a, me_a = login(args.base, "bd.a@demo.local", pw)
    b, me_b = login(args.base, "bd.b@demo.local", pw)
    tid = me_a["current"]["tenant_id"]
    check("BD-A、BD-B 登录", me_a["current"]["role"] == "bd" and me_b["current"]["tenant_id"] == tid)

    # 2. 空任务：API → outbox → 分发器 → Worker → 完成
    key = f"m0-{uuid.uuid4()}"
    body = {"kind": "system.noop", "params": {"sleep_ms": 500, "message": "M0 验收"}}
    r = a.post(f"/api/v1/tenants/{tid}/system/jobs", json=body, headers={"Idempotency-Key": key})
    job = r.json()
    check("创建任务返回 202", r.status_code == 202, f"job={job.get('id')}")
    t0 = time.monotonic()
    status = job.get("status")
    while status in ("queued", "running") and time.monotonic() - t0 < 60:
        time.sleep(0.5)
        job = a.get(f"/api/v1/tenants/{tid}/system/jobs/{job['id']}").json()
        status = job["status"]
    check(
        "任务经分发器与 Worker 完成",
        status == "succeeded",
        f"status={status} 用时 {time.monotonic() - t0:.1f}s worker={(job.get('result') or {}).get('worker_id')}",
    )
    r2 = a.post(f"/api/v1/tenants/{tid}/system/jobs", json=body, headers={"Idempotency-Key": key})
    check("相同幂等键重放返回原任务", r2.json()["id"] == job["id"] and r2.headers.get("Idempotent-Replayed") == "true")

    # 3. 数据隔离
    rb = b.get(f"/api/v1/tenants/{tid}/system/jobs/{job['id']}")
    ids_b = {j["id"] for j in b.get(f"/api/v1/tenants/{tid}/system/jobs").json()}
    check("BD-B 看不到 BD-A 的任务", rb.status_code == 404 and job["id"] not in ids_b, f"GET={rb.status_code}")
    c, _ = login(args.base, "bd.c@other.local", pw)
    rc = c.get(f"/api/v1/tenants/{tid}/system/jobs")
    check("其他企业成员不能访问本企业路径", rc.status_code == 404, f"status={rc.status_code}")

    # 4. 站点目录
    markets = a.get("/api/v1/markets").json()
    codes = [m["market_code"] for m in markets]
    check("欧洲 11 站目录", len(markets) == 11, " ".join(codes))

    # 5. 外部连通（管理员）
    adm, _ = login(args.base, "admin@demo.local", pw)
    ext = adm.post(f"/api/v1/tenants/{tid}/system/checks").json()
    fm, llm = ext["fastmoss"], ext["llm"]
    check(
        "FastMoss MCP 连通且未扣费",
        fm["status"] == "ok" and (fm.get("credit_cost") or 0) == 0,
        f"可用额度={fm.get('available_credits')} 本次扣费={fm.get('credit_cost')}",
    )
    llm_ok = llm["status"] == "ok"
    check(
        "LLM（OpenAI 兼容）连通",
        llm_ok,
        llm.get("detail") or llm["status"] + ("" if llm_ok else "（在 .env 配置 LLM_* 后重跑）"),
    )

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n合计 {len(results)} 项，通过 {len(results) - len(failed)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
