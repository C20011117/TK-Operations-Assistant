"""启动时迁移：先备份，再升级；失败则还原备份，不带着半升级的数据库运行。"""

import logging
import re
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tk_workspace.config import Settings, bundle_root, get_settings

if TYPE_CHECKING:
    from alembic.config import Config

# Alembic 及其依赖加载较慢（约 0.3 秒），只在确实需要升级时才导入。

log = logging.getLogger(__name__)
KEEP_BACKUPS = 7


def alembic_config(settings: Settings | None = None) -> "Config":
    from alembic.config import Config

    s = settings or get_settings()
    cfg = Config()
    cfg.set_main_option("script_location", str(bundle_root() / "migrations"))
    cfg.set_main_option("sqlalchemy.url", s.db_url)
    return cfg


def backup(settings: Settings | None = None, reason: str = "auto") -> Path | None:
    """用 SQLite 在线备份接口复制数据库（运行中也安全）。数据库还不存在时返回 None。"""
    s = settings or get_settings()
    if not s.db_path.exists():
        return None
    s.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = s.backups_dir / f"app-{stamp}-{reason}.db"
    src = sqlite3.connect(s.db_path)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    _prune(s.backups_dir)
    return target


def _prune(folder: Path) -> None:
    files = sorted(folder.glob("app-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP_BACKUPS:]:
        old.unlink(missing_ok=True)


_REV_RE = re.compile(r"^revision\s*=\s*[\"']([^\"']+)[\"']", re.M)
_DOWN_RE = re.compile(r"^down_revision\s*=\s*(?:None|[\"']([^\"']+)[\"'])", re.M)


def _scan_head() -> str | None:
    """不加载 Alembic，直接从迁移脚本文本找出唯一的最新版本；无法确定时返回 None。"""
    folder = bundle_root() / "migrations" / "versions"
    revs: set[str] = set()
    downs: set[str] = set()
    for f in folder.glob("*.py"):
        text = f.read_text(encoding="utf-8")
        rev, down = _REV_RE.search(text), _DOWN_RE.search(text)
        if not rev or not down:
            return None
        revs.add(rev.group(1))
        if down.group(1):
            downs.add(down.group(1))
    heads = revs - downs
    return next(iter(heads)) if len(heads) == 1 else None


def _db_revision(settings: Settings) -> str | None:
    con = sqlite3.connect(settings.db_path)
    try:
        row = con.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return row[0] if row else None


def is_up_to_date(settings: Settings) -> bool:
    """快速判断：数据库已存在且版本等于最新迁移。任何不确定都返回 False，交给 Alembic 处理。"""
    if not settings.db_path.exists():
        return False
    head = _scan_head()
    return head is not None and _db_revision(settings) == head


def _current_and_head(cfg: "Config", settings: Settings) -> tuple[str | None, str | None]:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from sqlalchemy import create_engine

    head = ScriptDirectory.from_config(cfg).get_current_head()
    if not settings.db_path.exists():
        return None, head
    eng = create_engine(settings.db_url)
    try:
        with eng.connect() as c:
            current = MigrationContext.configure(c).get_current_revision()
    finally:
        eng.dispose()
    return current, head


def upgrade_to_head(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    s.ensure_dirs()
    if is_up_to_date(s):
        return "up_to_date"
    from alembic import command

    cfg = alembic_config(s)
    current, head = _current_and_head(cfg, s)
    if current == head:
        return "up_to_date"
    snapshot = backup(s, reason="pre-migrate") if current else None
    try:
        command.upgrade(cfg, "head")
    except Exception:
        log.exception("database migration failed")
        if snapshot:
            from tk_workspace.platform.db.engine import reset_engine

            reset_engine()
            shutil.copyfile(snapshot, s.db_path)
            for suffix in ("-wal", "-shm"):
                Path(str(s.db_path) + suffix).unlink(missing_ok=True)
        raise
    return "upgraded"
