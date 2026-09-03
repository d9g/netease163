"""
数据模型 - 借鉴 163yinyue pysql.py (Song_sheet163/Comment163 等)
SQLAlchemy 2.0 风格, 9 张表
"""
from datetime import datetime
from ..api.cst_time import now_cst
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Index, JSON,
)
from .db import Base


# ==================== 基础数据表 ====================
class Song(Base):
    __tablename__ = "songs"

    id = Column(Integer, primary_key=True)  # 网易云 song id
    name = Column(String(500), nullable=False)
    artists = Column(JSON)  # [{id, name}]
    album_id = Column(Integer)
    album_name = Column(String(500))
    duration_ms = Column(Integer, default=0)
    publish_time = Column(Integer, default=0)  # timestamp ms
    fee = Column(Integer, default=0)
    pic_url = Column(String(1000))
    comment_total = Column(Integer, default=0)  # 评论总数 (search 时填)
    created_at = Column(DateTime, default=now_cst)
    updated_at = Column(DateTime, default=now_cst, onupdate=now_cst)


class Artist(Base):
    __tablename__ = "artists"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False)
    alias = Column(JSON)  # list
    pic_url = Column(String(1000))
    mv_count = Column(Integer, default=0)
    album_count = Column(Integer, default=0)
    music_count = Column(Integer, default=0)
    brief_desc = Column(Text)
    created_at = Column(DateTime, default=now_cst)
    updated_at = Column(DateTime, default=now_cst, onupdate=now_cst)


class Album(Base):
    __tablename__ = "albums"

    id = Column(Integer, primary_key=True)
    name = Column(String(500), nullable=False)
    artists = Column(JSON)
    pic_url = Column(String(1000))
    publish_time = Column(Integer, default=0)
    track_count = Column(Integer, default=0)
    description = Column(Text)
    created_at = Column(DateTime, default=now_cst)
    updated_at = Column(DateTime, default=now_cst, onupdate=now_cst)


class Playlist(Base):
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True)
    name = Column(String(500), nullable=False)
    creator = Column(String(200))
    cover_url = Column(String(1000))
    description = Column(Text)
    track_count = Column(Integer, default=0)
    play_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now_cst)
    updated_at = Column(DateTime, default=now_cst, onupdate=now_cst)


# ==================== 评论/歌词 ====================
class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    comment_id = Column(Integer, index=True)  # 网易云 comment id
    song_id = Column(Integer, nullable=False, index=True)
    user_nickname = Column(String(200))
    content = Column(Text)
    liked_count = Column(Integer, default=0)
    comment_time = Column(Integer, default=0)  # timestamp ms
    is_hot = Column(Integer, default=0)  # 0=普通 1=热门
    crawled_at = Column(DateTime, default=now_cst)

    __table_args__ = (
        Index("idx_comments_song_time", "song_id", "comment_time"),
    )


class Lyric(Base):
    __tablename__ = "lyrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, nullable=False, unique=True, index=True)
    lyric = Column(Text)
    tlyric = Column(Text)
    crawled_at = Column(DateTime, default=now_cst)


# ==================== 审计/日志 ====================
class SearchLog(Base):
    """搜索记录 - 借鉴 163yinyue 风格"""
    __tablename__ = "search_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(200), nullable=False, index=True)
    search_type = Column(String(20), default="song")
    result_count = Column(Integer, default=0)
    searched_at = Column(DateTime, default=now_cst, index=True)


class CrawlLog(Base):
    """爬取日志 - 借鉴 NetCloud NetCloud.log"""
    __tablename__ = "crawl_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    spider_name = Column(String(50), nullable=False, index=True)
    target_id = Column(Integer, index=True)  # 抓取的 ID
    success = Column(Integer, default=1)  # 1=成功 0=失败
    error_msg = Column(Text)
    duration_ms = Column(Integer, default=0)
    crawled_at = Column(DateTime, default=now_cst, index=True)
