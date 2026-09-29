"""M3 验收：在本机（真实 Windows 凭据管理器）跑一遍合作与寄样流程，逐条检查完成标准。

用法（仓库根目录）：cd backend && uv run python ../scripts/m3_acceptance.py
- 使用全新的临时数据目录，候选用人工导入（不调用 FastMoss、不花额度）。
- 后端从源码启动；个人数据密钥与正式应用共用同一个凭据管理器条目（本机本用户）。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import httpx

TOKEN = "m3-accept-token-0123456789"
ADDRESS = "221B Baker Street Flat 7"
NAME = "Sherlock Holmesworth"
PHONE = "+44 7700 900123"
TRACKING = "RM123456789GB"
SECRETS = (ADDRESS, NAME, PHONE, TRACKING)
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""), flush=True)


def key() -> dict:
    return {"Idempotency-Key": f"m3-{uuid.uuid4()}"}


tmp = tempfile.mkdtemp(prefix="tkws-m3-")
data = os.path.join(tmp, "TKWorkspace")
env = dict(os.environ, TKWS_DATA_DIR=data, TKWS_LAUNCH_TOKEN=TOKEN, PYTHONIOENCODING="utf-8")
proc = subprocess.Popen(
    [sys.executable, "-c", "from tk_workspace.desktop import main; raise SystemExit(main())"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    stdin=subprocess.DEVNULL,
    text=True,
    creationflags=0x08000000 if sys.platform == "win32" else 0,
)
m = re.match(r"TKWS_READY (.*)", proc.stdout.readline())
if not m:
    proc.kill()
    raise SystemExit("backend not ready")
B = f"http://127.0.0.1:{json.loads(m.group(1))['port']}/api/v1"
c = httpx.Client(trust_env=False, timeout=60, headers={"Authorization": f"Bearer {TOKEN}"})

try:
    # ---------- 准备：产品、英国任务、人工导入候选 ----------
    p = c.post(f"{B}/products", json={"sku": "JC-01", "name": "便携榨汁杯"}).json()
    facts = {"summary": "便携榨汁杯", "selling_points": ["便携"], "use_scenarios": ["办公室"], "forbidden_claims": []}
    terms = [{"market_code": "UK", "price_status": "known", "price_amount": "19.99", "sample_policy": "free"}]
    c.put(f"{B}/products/{p['id']}/draft", json={"facts": facts, "market_terms": terms}).raise_for_status()
    c.post(f"{B}/products/{p['id']}/draft/confirm").raise_for_status()
    camp = c.post(
        f"{B}/campaigns",
        json={"product_id": p["id"], "name": "榨汁杯 UK", "goal": "sales", "collaboration_type": "free_sample",
              "market_code": "UK", "market": {}},
    ).json()
    cm = camp["markets"][0]["id"]
    c.put(f"{B}/campaign-markets/{cm}", json={"target_list_size": 5, "cost_cap_credits": 5}).raise_for_status()
    crit = [{"field_key": "follower_count", "operator": "between", "expected": {"min": 1000, "max": 900000},
             "hardness": "hard"}]
    c.put(f"{B}/campaign-markets/{cm}/criteria", json={"criteria": crit, "search": {"keywords": ["juicer"]}}
          ).raise_for_status()
    c.post(f"{B}/campaign-markets/{cm}/confirm").raise_for_status()
    csv_text = "用户名,昵称,粉丝数,近28天GMV,币种\n@kitchen_kate,Kate,82000,1500,GBP\n"
    run = c.post(f"{B}/campaign-markets/{cm}/manual-import", json={"csv": csv_text}, headers=key()).json()
    for _ in range(200):
        if c.get(f"{B}/matching-runs/{run['id']}").json()["status"] not in ("queued", "running"):
            break
        time.sleep(0.2)
    rec = c.get(f"{B}/matching-runs/{run["id"]}/recommendations").json()
    if not rec.get("cards"):
        raise SystemExit(f"no cards: run={c.get(f'{B}/matching-runs/{run['id']}').json()} rec={rec}")
    card = rec["cards"][0]
    creator = card["creator"]["id"]

    # ---------- 步骤 5：决定与合作 ----------
    r = c.post(f"{B}/campaign-markets/{cm}/collaborations", json={"creator_id": creator})
    check("⑤ 没有标记“保留”不能准备合作", r.status_code == 409, r.json()["error"]["code"])
    c.post(
        f"{B}/campaign-markets/{cm}/creator-decisions",
        json={"creator_id": creator, "decision": "keep", "run_id": run["id"], "evaluation_id": card["evaluation_id"]},
    ).raise_for_status()
    co = c.post(f"{B}/campaign-markets/{cm}/collaborations", json={"creator_id": creator}).json()
    ship_body = {
        "items": [{"sku": "JC-01", "quantity": 1, "unit_cost": "6.5"}],
        "cost_cap_amount": "20",
        "recipient": {"name": NAME, "phone": PHONE, "address_line1": ADDRESS, "city": "London",
                      "postcode": "NW1 6XE", "country_code": "GB"},
    }
    r = c.post(f"{B}/collaborations/{co['id']}/shipments", json=ship_body, headers=key())
    check("⑤ 没有记录双方约定不能寄样", r.status_code == 409, r.json()["error"]["code"])
    co = c.post(
        f"{B}/collaborations/{co['id']}/confirm-agreement",
        json={"agreed_via": "tiktok_message", "agreed_on": "2026-09-28", "agreed_video_count": 2,
              "terms_note": "免费寄样，两条新视频"},
    ).json()
    check("⑤ 记录约定后合作为“已达成约定”", co["status"] == "agreed")

    # ---------- 步骤 6：寄样确认与幂等 ----------
    k = key()
    s1 = c.post(f"{B}/collaborations/{co['id']}/shipments", json=ship_body, headers=k)
    s2 = c.post(f"{B}/collaborations/{co['id']}/shipments", json=ship_body, headers=k)
    sid = s1.json()["id"]
    check("⑥ 重复提交创建寄样单只产生一张", s2.status_code == 200 and s2.json()["id"] == sid)
    d = c.post(f"{B}/shipments/{sid}/register-dispatch", json={"dispatched_at": "2026-09-29"}, headers=key())
    check("⑥ 未经本人确认不能登记寄出", d.status_code == 409, d.json()["error"]["code"])
    pv = c.post(f"{B}/shipments/{sid}/confirmation-preview").json()
    ck = key()
    body = {"payload_version": pv["payload_version"], "payload_hash": pv["payload_hash"]}
    c1 = c.post(f"{B}/shipments/{sid}/confirm", json=body, headers=ck)
    c2 = c.post(f"{B}/shipments/{sid}/confirm", json=body, headers=ck)
    c3 = c.post(f"{B}/shipments/{sid}/confirm", json=body, headers=key())
    check("⑥ 本人确认成功；同一请求重放不重复；换请求再确认被拒",
          c1.status_code == 200 and c2.status_code == 200 and c3.status_code == 409,
          f"{c1.status_code}/{c2.status_code}/{c3.status_code}")
    # 修改使确认失效
    edited = c.put(f"{B}/shipments/{sid}", json={**ship_body, "items": [{"sku": "JC-01", "quantity": 2}],
                                                "recipient": None}).json()
    d = c.post(f"{B}/shipments/{sid}/register-dispatch", json={"dispatched_at": "2026-09-29"}, headers=key())
    check("⑥ 修改寄样单后原确认作废，不能寄出", edited["status"] == "draft" and d.status_code == 409)
    pv = c.post(f"{B}/shipments/{sid}/confirmation-preview").json()
    c.post(f"{B}/shipments/{sid}/confirm", json={"payload_version": pv["payload_version"],
                                                 "payload_hash": pv["payload_hash"]}, headers=key())
    dk = key()
    dbody = {"dispatched_at": "2026-09-29", "carrier": "Royal Mail", "tracking_number": TRACKING}
    d1 = c.post(f"{B}/shipments/{sid}/register-dispatch", json=dbody, headers=dk)
    d2 = c.post(f"{B}/shipments/{sid}/register-dispatch", json=dbody, headers=dk)
    d3 = c.post(f"{B}/shipments/{sid}/register-dispatch", json=dbody, headers=key())
    check("⑥ 登记寄出只生效一次（重放返回同一结果，再次登记被拒）",
          d1.status_code == 200 and d2.status_code == 200 and d3.status_code == 409,
          f"{d1.status_code}/{d2.status_code}/{d3.status_code}")
    v = d1.json()
    check("⑥ 寄出后签收状态为“未知”", v["delivery_status"] == "unknown" and v["delivered_at"] is None)
    time.sleep(1)
    v = c.get(f"{B}/shipments/{sid}").json()
    check("⑥ 没有物流节点时一直保持未知", v["delivery_status"] == "unknown")
    v = c.post(f"{B}/shipments/{sid}/events", json={"status": "delivered", "occurred_at": "2026-09-29"}).json()
    check("⑥ 登记签收节点后才变为已签收", v["delivery_status"] == "delivered")
    full = c.get(f"{B}/shipments/{sid}/recipient").json()
    check("⑥ 本机可以查看完整收件信息", full["recipient"]["address_line1"] == ADDRESS)
    detail = c.get(f"{B}/collaborations/{co['id']}").json()
    check("⑥ 列表 / 详情只显示脱敏收件信息", all(sec not in json.dumps(detail, ensure_ascii=False) for sec in SECRETS))
finally:
    proc.terminate()
    try:
        proc.wait(10)
    except Exception:
        proc.kill()

# ---------- 地址加密、日志中查不到 ----------
db_dir = os.path.join(data, "data")
raw = b"".join(open(os.path.join(db_dir, f), "rb").read() for f in os.listdir(db_dir) if f.startswith("app.db"))
check("⑦ 数据库文件（含 WAL）里没有地址、姓名、电话、单号明文", all(s.encode() not in raw for s in SECRETS))
logs = os.path.join(data, "logs")
text_all = ""
for root, _, files in os.walk(logs):
    for f in files:
        text_all += open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
check("⑦ 日志里查不到收件信息", all(s not in text_all for s in SECRETS), f"日志 {len(text_all)} 字符")
shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(results)}/{len(results)} PASS", flush=True)
sys.exit(0 if all(results) else 1)
