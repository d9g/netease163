"""
数据库连接 - 借鉴 163yinyue utils/pysql.py (改造)
- 默认 SQLite, 通过 DATABASE_URL 切换 MySQL
- 自动创建表 (init_db)
"""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

_engine = None
_SessionLocal = None


def get_db_url() -> str:
    """获取 DB URL"""
    return os.getenv("DATABASE_URL") or "sqlite:////root/netease163/data/netease163.db"


def get_engine():
    global _engine
    if _engine is None:
        url = get_db_url()
        # SQLite 需要 check_same_thread=False
        connect_args = {}
        if url.startswith("sqlite"):
            # 确保 data 目录存在
            db_path = url.replace("sqlite:///", "")
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            connect_args = {
                "check_same_thread": False,
                # 老杨 18:10 修 readonly database bug
                # WAL 模式允许读写并发, 避免 uvicorn 主线程 + scheduler 线程同时写导致 readonly
                "timeout": 30,
            }
        _engine = create_engine(
            url,
            connect_args=connect_args,
            pool_pre_ping=True,  # 自动重连失效连接
            echo=False,
            future=True,
        )
        # SQLite WAL 模式
        if url.startswith("sqlite"):
            from sqlalchemy import event
            @event.listens_for(_engine, "connect")
            def set_sqlite_pragma(dbapi_connection, connection_record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()
    return _engine


def get_session():
    """获取 DB session (借鉴 163yinyue settings.engine)"""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False)
    return _SessionLocal()


def init_db():
    """初始化所有表"""
    from .models import (
        Song, Artist, Album, Playlist, Comment,
        Lyric, SearchLog, CrawlLog,
    )
    Base.metadata.create_all(get_engine())
