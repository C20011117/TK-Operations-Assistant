"""M0-D 桌面版验收（在 Windows 开发机上对安装后的程序执行）。"""
import csv, glob, io, json, os, re, shutil, subprocess, sys, tempfile, time
import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL = os.environ["LOCALAPPDATA"]
NOWIN = 0x08000000
res = []

def mark(n, ok, msg):
    res.append((n, ok, msg)); print(f"[{'PASS' if ok else 'FAIL' if ok is False else 'TODO'}] {n} {msg}", flush=True)

def procs(image):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"], capture_output=True, text=True, encoding="mbcs", errors="replace").stdout
    return [int(r[1]) for r in csv.reader(io.StringIO(out)) if len(r) > 1 and r[0].lower() == image.lower()]

def port_of(pid):
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, encoding="mbcs", errors="replace").stdout
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 5 and p[3] == "LISTENING" and p[4] == str(pid) and p[1].startswith("127.0.0.1:"):
            return int(p[1].split(":")[1])
    return None

def wait(fn, timeout, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v: return v
        time.sleep(step)
    return None

cli = httpx.Client(trust_env=False, timeout=30)

# ---------- 安装 ----------
inst = glob.glob(os.path.join(ROOT, "apps/desktop/src-tauri/target/release/bundle/nsis/*-setup.exe"))[0]
print("installer:", os.path.basename(inst), f"{os.path.getsize(inst)/2**20:.1f} MB")
for pid in procs("tk-backend.exe"): subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
t = time.time(); r = subprocess.run([inst, "/S"]); print(f"silent install exit={r.returncode} in {time.time()-t:.1f}s")
cands = [d for d in glob.glob(os.path.join(LOCAL, "*")) if os.path.exists(os.path.join(d, "tk-backend", "tk-backend.exe"))]
appdir = max(cands, key=os.path.getmtime)
app_exe = [f for f in glob.glob(os.path.join(appdir, "*.exe")) if "uninstall" not in f.lower()][0]
backend_exe = os.path.join(appdir, "tk-backend", "tk-backend.exe")
app_image = os.path.basename(app_exe)
print("installed to:", appdir, "| main exe:", app_image)

# ---------- ① 无 Python/Node 环境可运行（后端在最小 PATH 下启动） ----------
def run_backend(data_dir, extra_env=None, minimal=False):
    env = {"SYSTEMROOT": os.environ["SYSTEMROOT"], "PATH": r"C:\Windows\System32;C:\Windows", "LOCALAPPDATA": LOCAL,
           "USERPROFILE": os.environ["USERPROFILE"], "APPDATA": os.environ["APPDATA"], "TEMP": os.environ["TEMP"]} if minimal else dict(os.environ)
    env.update({"TKWS_DATA_DIR": data_dir, "TKWS_LAUNCH_TOKEN": "accept-token"}, **(extra_env or {}))
    p = subprocess.Popen([backend_exe], env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, creationflags=NOWIN, text=True)
    line = p.stdout.readline()
    m = re.match(r"TKWS_READY (.*)", line)
    if not m: p.kill(); raise RuntimeError("backend not ready: " + line)
    return p, f"http://127.0.0.1:{json.loads(m.group(1))['port']}/api/v1"

H = {"Authorization": "Bearer accept-token"}
tmp1 = tempfile.mkdtemp(prefix="tkws-accept-")
p, B = run_backend(tmp1, minimal=True)
h = cli.get(f"{B}/system/health", headers=H).json()
mark("①", h["checks"]["database"] == "ok", f"已安装的后端在仅含 System32 的 PATH 下启动，数据库 {h['checks']['database']}（界面依赖系统自带 WebView2）")

# ---------- ④ 本机接口保护 ----------
c1 = cli.get(f"{B}/system/health").status_code
c2 = cli.get(f"{B}/system/health", headers={"Authorization": "Bearer wrong"}).status_code
c3 = cli.get(f"{B}/system/health", headers={**H, "Host": "evil.example"}).status_code
mark("④", (c1, c2, c3) == (401, 401, 403), f"无令牌 {c1}、错误令牌 {c2}、外部 Host {c3}")

# ---------- ③ 任务执行与中断恢复 ----------
ok_job = cli.post(f"{B}/system/jobs", headers={**H, "Idempotency-Key": "accept-job-0001"}, json={"kind": "system.noop", "params": {"sleep_ms": 300}}).json()
st1 = wait(lambda: (lambda s: s if s in ("succeeded", "failed") else None)(cli.get(f"{B}/system/jobs/{ok_job['id']}", headers=H).json()["status"]), 10)
long_job = cli.post(f"{B}/system/jobs", headers={**H, "Idempotency-Key": "accept-job-0002"}, json={"kind": "system.noop", "params": {"sleep_ms": 6000}}).json()
wait(lambda: cli.get(f"{B}/system/jobs/{long_job['id']}", headers=H).json()["status"] == "running", 10)
subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True); p.wait()
p, B = run_backend(tmp1)
final = wait(lambda: (lambda j: j if j["status"] in ("succeeded", "failed", "cancelled") else None)(cli.get(f"{B}/system/jobs/{long_job['id']}", headers=H).json()), 20, 0.3)
mark("③", st1 == "succeeded" and final and final["status"] == "succeeded" and final["result"]["attempt"] == 2,
     f"空任务 {st1}；执行中强制结束后重启 → {final and final['status']}（第 {final and final['result'].get('attempt')} 次执行，中断记录：{final and (final.get('last_error') or {}).get('class')}）")

# ---------- ⑥ 连通检查 & ⑤ 明文扫描 ----------
key = next(l.split("=", 1)[1].strip() for l in open(os.path.join(ROOT, ".env"), encoding="utf-8") if l.startswith("FASTMOSS_MCP_API_KEY="))
r = cli.put(f"{B}/settings/fastmoss", headers=H, json={"api_key": key})
chk = cli.post(f"{B}/settings/checks", headers=H).json()
fm, llm = chk["fastmoss"], chk["llm"]
fm_ok = fm["status"] == "ok" and (fm.get("credit_cost") or 0) == 0
mark("⑥a", fm_ok, f"FastMoss 连通 {fm['status']}，本次扣费 {fm.get('credit_cost')}，可用额度 {fm.get('available_credits')}，{fm.get('latency_ms')} ms")
mark("⑥b", True if llm["status"] == "ok" else None, f"大模型 {llm['status']}：{llm.get('detail') or ''}")
subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True); p.wait()

def scan(d):
    hits = []
    for dp, _, fs in os.walk(d):
        for f in fs:
            fp = os.path.join(dp, f)
            try:
                if key.encode() in open(fp, "rb").read(): hits.append(fp)
            except OSError: pass
    return hits
hits = scan(tmp1)

# ---------- ② 冷启动 & ⑦ 关闭后无残留 ----------
def launch():
    before = set(procs("tk-backend.exe"))
    t0 = time.time()
    app = subprocess.Popen([app_exe], creationflags=0x00000008 | 0x00000200)  # DETACHED | NEW_PROCESS_GROUP
    bpid = wait(lambda: next(iter(set(procs("tk-backend.exe")) - before), None), 30)
    port = wait(lambda: port_of(bpid), 30) if bpid else None
    code = wait(lambda: (lambda: cli.get(f"http://127.0.0.1:{port}/api/v1/system/health").status_code)() if port else None, 30) if port else None
    return app, bpid, time.time() - t0, code

times = []
app, bpid, dt, code = launch(); times.append(dt)
time.sleep(1.5)
subprocess.run(["taskkill", "/PID", str(app.pid)], capture_output=True)  # 等同点窗口关闭按钮（WM_CLOSE）
gone1 = wait(lambda: not procs(app_image) and bpid not in procs("tk-backend.exe"), 10, 0.2)
app, bpid, dt2, _ = launch(); times.append(dt2)
time.sleep(1.5)
subprocess.run(["taskkill", "/F", "/PID", str(app.pid)], capture_output=True)  # 外壳被强制结束
t_kill = time.time()
gone2 = wait(lambda: bpid not in procs("tk-backend.exe"), 15, 0.2)
orphan_s = time.time() - t_kill
mark("②", code == 401 and max(times) <= 5, f"双击到后端就绪：首次 {times[0]:.2f}s，再次 {times[1]:.2f}s（目标 ≤ 5s）")
mark("⑦", bool(gone1) and bool(gone2), f"关闭窗口后后端随之退出：{'是' if gone1 else '否'}；外壳被强制结束后后端 {orphan_s:.1f}s 内自行退出：{'是' if gone2 else '否'}")

real = os.path.join(LOCAL, "TKWorkspace")
hits += scan(real)
mark("⑤", not hits, f"扫描验收数据目录与 {real}（数据库、日志、备份），Key 明文命中 {len(hits)} 处" + (f"：{hits}" if hits else ""))

shutil.rmtree(tmp1, ignore_errors=True)
print("SUMMARY", " ".join(f"{n}:{'P' if ok else 'F' if ok is False else 'T'}" for n, ok, _ in res))
