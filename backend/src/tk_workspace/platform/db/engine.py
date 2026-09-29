"""SQLite 引擎与会话。

- WAL 模式：界面读取与后台任务写入可以并发。
- 每个连接开启外键约束、设置忙等待，避免偶发的 database is locked。
- 后端所有数据库访问都走同步 SQLAlchemy：FastAPI 的同步路由在线程池中运行，任务执行器在独立线程中运行。
"""

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tk_workspace.config import get_settings


def _on_connect(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def make_engine(url: str) -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
    event.listen(engine, "connect", _on_connect)
    return engine


@lru_cache
def get_engine() -> Engine:
    s = get_settings()
    s.ensure_dirs()
    return make_engine(s.db_url)


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


@contextmanager
def tx() -> Iterator[Session]:
    """一个事务：成功提交，异常回滚。"""
    with _sessionmaker()() as s, s.begin():
        yield s


def reset_engine() -> None:
    """测试切换数据目录时使用。"""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    _sessionmaker.cache_clear()
