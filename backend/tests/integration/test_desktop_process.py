"""桌面入口进程：就绪行、令牌保护、父进程退出后自动退出。"""

import json
import os
import subprocess
import sys
import time

import httpx

TOKEN = "proc-test-token-abcdef0123456789"


def _start_backend(tmp_path, parent_pid: int) -> tuple[subprocess.Popen, int]:
    env = {
        **os.environ,
        "TKWS_APP_ENV": "production",
        "TKWS_DATA_DIR": str(tmp_path / "TKWorkspace"),
        "TKWS_LAUNCH_TOKEN": TOKEN,
        "TKWS_PARENT_PID": str(parent_pid),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "tk_workspace.desktop"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line.startswith("TKWS_READY "):
            return proc, json.loads(line.split(" ", 1)[1])["port"]
        if not line and proc.poll() is not None:
            break
    proc.kill()
    raise AssertionError("backend did not become ready")


def test_backend_process_lifecycle(tmp_path):
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    backend = None
    try:
        backend, port = _start_backend(tmp_path, parent.pid)
        base = f"http://127.0.0.1:{port}/api/v1"
        assert httpx.get(f"{base}/system/health").status_code == 401
        ok = httpx.get(f"{base}/system/health", headers={"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200 and ok.json()["checks"]["database"] == "ok"
        assert (tmp_path / "TKWorkspace" / "data" / "app.db").is_file()
        log = (tmp_path / "TKWorkspace" / "logs" / "backend.log").read_text(encoding="utf-8")
        assert TOKEN not in log

        parent.kill()  # 外壳“崩溃”
        parent.wait()
        backend.wait(timeout=15)
        assert backend.returncode is not None
    finally:
        parent.kill()
        if backend and backend.poll() is None:
            backend.kill()
