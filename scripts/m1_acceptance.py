"""M1 验收：用打包后的后端 exe 跑一遍产品与任务流程。

用法（仓库根目录）：cd backend && uv run python ../scripts/m1_acceptance.py
1) 全新数据目录：逐条检查 M1 四项完成标准；
2) 复制本机现有数据目录（%LOCALAPPDATA%\\TKWorkspace）到临时目录再启动：确认旧库能升级到新结构。
"""

import json, os, re, shutil, subprocess, sys, tempfile

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(ROOT, "backend", "dist", "tk-backend", "tk-backend.exe")
NOWIN = 0x08000000
H = {"Authorization": "Bearer accept-token"}
cli = httpx.Client(trust_env=False, timeout=30, headers=H)
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  ({detail})" if detail else ""))


def run_backend(data_dir):
    env = dict(os.environ, TKWS_DATA_DIR=data_dir, TKWS_LAUNCH_TOKEN="accept-token")
    p = subprocess.Popen([EXE], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=NOWIN, text=True)
    m = re.match(r"TKWS_READY (.*)", p.stdout.readline())
    if not m:
        p.kill()
        raise RuntimeError("backend not ready")
    return p, f"http://127.0.0.1:{json.loads(m.group(1))['port']}/api/v1"


TERMS = [
    {"market_code": "UK", "price_status": "known", "price_amount": "19.99", "sample_policy": "free"},
    {"market_code": "DE", "price_status": "known", "price_amount": "22.90", "sample_policy": "free"},
]
FACTS = {"summary": "便携榨汁杯", "selling_points": ["便携"], "use_scenarios": ["办公室"], "forbidden_claims": []}

# ---------- 1) 全新数据目录 ----------
tmp = tempfile.mkdtemp(prefix="tkws-m1-")
p, B = run_backend(tmp)
try:
    prod = cli.post(f"{B}/products", json={"sku": "JC-01", "name": "便携榨汁杯"}).json()
    pid = prod["id"]
    cli.put(f"{B}/products/{pid}/draft", json={"facts": FACTS, "market_terms": TERMS}).raise_for_status()
    v1 = cli.post(f"{B}/products/{pid}/draft/confirm").json()

    def new_campaign(mc):
        r = cli.post(f"{B}/campaigns", json={"product_id": pid, "name": f"榨汁杯 {mc}", "goal": "sales", "collaboration_type": "free_sample", "market_code": mc, "market": {}})
        r.raise_for_status()
        return r.json()

    uk, de = new_campaign("UK"), new_campaign("DE")
    ukm, dem = uk["markets"][0], de["markets"][0]

    # ④ 缺少必填条件时不能启动
    r = cli.post(f"{B}/campaign-markets/{ukm['id']}/confirm")
    blockers = {b["code"] for b in r.json().get("error", {}).get("blockers", [])}
    check("④ 缺必填项时确认被拒", r.status_code == 422 and {"target_list_size_missing", "cost_cap_missing", "criteria_empty"} <= blockers, f"{r.status_code} {sorted(blockers)}")

    # ③ 英国/德国币种、时区
    check("③ 英国任务 GBP / Europe/London / FastMoss GB", (ukm["reporting_currency"], ukm["time_zone"], ukm["fastmoss_region"]) == ("GBP", "Europe/London", "GB"))
    check("③ 德国任务 EUR / Europe/Berlin", (dem["reporting_currency"], dem["time_zone"]) == ("EUR", "Europe/Berlin"))
    check("③ 各自价格条款", (ukm["price_term"]["price_currency"], ukm["price_term"]["price_amount"], dem["price_term"]["price_currency"]) == ("GBP", "19.99", "EUR"))

    # 补齐后确认
    cli.put(f"{B}/campaign-markets/{ukm['id']}", json={"target_list_size": 30, "cost_cap_credits": 60}).raise_for_status()
    cli.put(f"{B}/campaign-markets/{ukm['id']}/criteria", json={"criteria": [{"field_key": "follower_count", "operator": "between", "expected": {"min": 10000, "max": 500000}, "hardness": "hard"}], "search": {"keywords": ["juicer"]}}).raise_for_status()
    r = cli.post(f"{B}/campaign-markets/{ukm['id']}/confirm")
    check("④ 补齐后可以确认", r.status_code == 200 and r.json()["markets"][0]["status"] == "ready")

    # ① 修改产品产生新版本
    cli.post(f"{B}/products/{pid}/draft")
    cli.put(f"{B}/products/{pid}/draft", json={"facts": FACTS, "market_terms": [{**TERMS[0], "price_amount": "24.99"}, TERMS[1]]}).raise_for_status()
    v2 = cli.post(f"{B}/products/{pid}/draft/confirm").json()
    detail = cli.get(f"{B}/products/{pid}").json()
    old = cli.get(f"{B}/products/versions/{v1['current']['id']}").json()
    check(
        "① 修改产品产生 v2，v1 原样保留",
        v2["current_version_no"] == 2 and len(detail["versions"]) == 2 and next(t["price_amount"] for t in old["market_terms"] if t["market_code"] == "UK") == "19.99",
        f"versions={[v['version_no'] for v in detail['versions']]}",
    )

    # ② 任务固定引用版本
    cm = cli.get(f"{B}/campaigns/{uk['id']}").json()["markets"][0]
    check("② 任务仍用 v1 与 19.99", (cm["product_version_no"], cm["price_term"]["price_amount"], cm["status"]) == (1, "19.99", "ready"))
    cm = cli.post(f"{B}/campaign-markets/{ukm['id']}/use-latest-product").json()["markets"][0]
    check("② 手动更新后用 v2 并需重新确认", (cm["product_version_no"], cm["price_term"]["price_amount"], cm["status"]) == (2, "24.99", "draft"))
finally:
    p.kill()
    p.wait()
shutil.rmtree(tmp, ignore_errors=True)

# ---------- 2) 旧数据升级 ----------
real = os.path.join(os.environ["LOCALAPPDATA"], "TKWorkspace")
if os.path.isdir(real):
    tmp = tempfile.mkdtemp(prefix="tkws-m1-up-")
    shutil.copytree(real, tmp, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.lock", "logs"))
    p, B = run_backend(tmp)
    try:
        ok = all(cli.get(f"{B}/{u}").status_code == 200 for u in ("system/health", "products", "campaigns", "markets"))
        check("⑤ 现有数据目录升级后正常读取", ok)
    finally:
        p.kill()
        p.wait()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} PASS")
sys.exit(0 if all(results) else 1)
