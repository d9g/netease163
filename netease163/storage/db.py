"""
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
    return os.getenv("DATABASE_URL") or "sqlite:///data/netease163.db"


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
                # 使用 WAL 模式支持读写并发
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
    # 2026-09-05 评论入库 bug + 情感标签 + 断点续传
    # Base.metadata.create_all 不会动已存在的表, 需手动补字段
    _run_migrations()


def _run_migrations():
    """手动 ALTER TABLE 迁移 (轻量级, 不引 alembic)"""
    from sqlalchemy import inspect, text
    engine = get_engine()
    with engine.connect() as conn:
        # 1. comments 表 加 4 个情感字段 + emotion索引
        cols = {c["name"] for c in inspect(engine).get_columns("comments")}
        if "ai_emotion" not in cols:
            conn.execute(text("ALTER TABLE comments ADD COLUMN ai_emotion VARCHAR(30)"))
            conn.execute(text("ALTER TABLE comments ADD COLUMN ai_emotion_secondary VARCHAR(30)"))
            conn.execute(text("ALTER TABLE comments ADD COLUMN ai_emotion_intensity VARCHAR(10)"))
            conn.execute(text("ALTER TABLE comments ADD COLUMN ai_emotion_keywords VARCHAR(200)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_comments_ai_emotion ON comments (ai_emotion)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_comments_emotion ON comments (ai_emotion, liked_count)"))
        # 1.1 comments 单独加 emotion_secondary (如已升级过老库跳这步)
        elif "ai_emotion_secondary" not in cols:
            conn.execute(text("ALTER TABLE comments ADD COLUMN ai_emotion_secondary VARCHAR(30)"))
        # 2. song_crawl_status 表 加 4 个断点续传字段
        cols = {c["name"] for c in inspect(engine).get_columns("song_crawl_status")}
        if "last_comment_offset" not in cols:
            conn.execute(text("ALTER TABLE song_crawl_status ADD COLUMN last_comment_offset INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE song_crawl_status ADD COLUMN comment_total INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE song_crawl_status ADD COLUMN comments_completed INTEGER DEFAULT 0"))
            conn.execute(text("ALTER TABLE song_crawl_status ADD COLUMN comments_completed_at DATETIME"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scs_completed_time ON song_crawl_status (comments_completed, last_comment_crawled_at)"))
        conn.commit()
