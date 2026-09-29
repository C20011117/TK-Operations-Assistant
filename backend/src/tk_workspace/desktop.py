"""桌面入口：由 Tauri 外壳以子进程方式启动。

启动顺序：日志 → 迁移（先备份）→ 任务执行器 → 绑定 127.0.0.1 随机端口 → 打印就绪行 → 提供服务。
外壳读取标准输出中的一行 `TKWS_READY {"port": 12345, "version": "..."}` 得到端口。
外壳退出（或崩溃）时，父进程看护线程让本进程随之退出，不留孤儿进程。

开发：uv run python -m tk_workspace.desktop --dev   （固定端口 8765、开发令牌 dev-token）
"""

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from tk_workspace.config import APP_VERSION, Settings, get_settings

log = logging.getLogger("tk_workspace.desktop")
READY_PREFIX = "TKWS_READY "


class _RedactFilter(logging.Filter):
    """日志中去掉令牌与常见密钥格式。"""

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        changed = False
        for s in self._secrets:
            if s in msg:
                msg, changed = msg.replace(s, "***"), True
        if changed:
            record.msg, record.args = msg, ()
        return True


def setup_logging(settings: Settings, token: str) -> None:
    settings.ensure_dirs()
    handler = RotatingFileHandler(
        settings.logs_dir / "backend.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(_RedactFilter([token]))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if settings.app_env != "production":
        stream = logging.StreamHandler(sys.stderr)
        stream.addFilter(_RedactFilter([token]))
        root.addHandler(stream)


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def watch_parent(pid: int, on_exit) -> None:
    def loop() -> None:
        while True:
            time.sleep(2)
            if not _pid_alive(pid):
                log.warning("parent process %s is gone, shutting down", pid)
                on_exit()
                return

    threading.Thread(target=loop, daemon=True, name="parent-watch").start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tk-backend")
    parser.add_argument("--dev", action="store_true", help="开发模式：固定端口 8765、开发令牌")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    if args.dev:
        os.environ.setdefault("TKWS_APP_ENV", "development")
    get_settings.cache_clear()
    settings = get_settings()

    from tk_workspace.api.main import create_app, resolve_token

    token = resolve_token(settings)
    setup_logging(settings, token)
    log.info("starting backend %s, data dir %s", APP_VERSION, settings.data_dir)

    from tk_workspace.platform.db.migrate import upgrade_to_head

    log.info("database: %s", upgrade_to_head(settings))

    from tk_workspace.platform.jobs.runner import JobRunner

    runner = JobRunner()
    runner.start()

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", args.port if args.port is not None else (8765 if args.dev else 0)))
    port = sock.getsockname()[1]

    app = create_app(settings, token)
    config = uvicorn.Config(app, log_config=None, access_log=False, lifespan="off")
    server = uvicorn.Server(config)

    def shutdown() -> None:
        server.should_exit = True

    if settings.parent_pid:
        watch_parent(settings.parent_pid, shutdown)

    # 就绪行必须单独一行、立即刷新，外壳据此得到端口
    print(READY_PREFIX + json.dumps({"port": port, "version": APP_VERSION}), flush=True)
    try:
        server.run(sockets=[sock])
    finally:
        runner.stop()
        log.info("backend stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
