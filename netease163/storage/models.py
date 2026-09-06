"""
SQLAlchemy 2.0 风格, 9 张表
"""
from datetime import datetime
from ..api.cst_time import now_cst
from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Index, JSON, UniqueConstraint,
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
    # AI 评分 () 
    ai_score = Column(Integer, default=-1)  # 0-5 星 (-1=未评分)
    ai_label = Column(String(20))  # "口水" / "中等" / "高质量"
    ai_reason = Column(String(500))  # AI 给出理由 (20字内)
    ai_analyzed_at = Column(DateTime)  # AI 分析时间
    # 2026-09-05 情感标签扩展:
    # 26 标签体系 详见 PLAN_2026-09-05_netease163.md
    ai_emotion = Column(String(30), index=True)  # 主标签 "感动" / "忧伤" / "幸福" 等
    ai_emotion_secondary = Column(String(30))  # 辅标签 (复杂情绪时填, 例 "感动 + 孤独")
    ai_emotion_intensity = Column(String(10))  # 强度 "深" / "浅" / ""
    ai_emotion_keywords = Column(String(200))  # 触发关键词 (例 "雨, 思念, 远方")

    __table_args__ = (
        # (song_id, comment_id) 唯一 → 评论去重
        UniqueConstraint("song_id", "comment_id", name="uq_comments_song_comment"),
        Index("idx_comments_song_time", "song_id", "comment_time"),
        Index("idx_comments_ai_score", "ai_score", "liked_count"),
        # 2026-09-05 情感检索索引
        Index("idx_comments_emotion", "ai_emotion", "liked_count"),
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
    __tablename__ = "search_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    keyword = Column(String(200), nullable=False, index=True)
    search_type = Column(String(20), default="song")
    result_count = Column(Integer, default=0)
    searched_at = Column(DateTime, default=now_cst, index=True)
    deleted_at = Column(DateTime)  # P2-5 软删除标记 (服务重启后能记住被删的词)


class CrawlLog(Base):
    __tablename__ = "crawl_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    spider_name = Column(String(50), nullable=False, index=True)
    target_id = Column(Integer, index=True)  # 抓取的 ID
    success = Column(Integer, default=1)  # 1=成功 0=失败
    error_msg = Column(Text)
    duration_ms = Column(Integer, default=0)
    crawled_at = Column(DateTime, default=now_cst, index=True)


# ==================== 跟时间做朋友 ()  ====================
class SongCrawlStatus(Base):
    """歌曲爬取状态 - 跟时间做朋友核心表"""
    __tablename__ = "song_crawl_status"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, nullable=False, unique=True, index=True)
    last_crawled_at = Column(DateTime, default=now_cst, index=True)  # 上次爬详情时间
    last_comment_crawled_at = Column(DateTime)  # 上次爬评论时间
    crawl_count = Column(Integer, default=0)  # 爬过几次
    comment_crawl_count = Column(Integer, default=0)  # 评论爬过几次
    is_priority = Column(Integer, default=0, index=True)  # 1=优先重爬
    # 2026-09-05 断点续传字段:
    last_comment_offset = Column(Integer, default=0)  # 上次爬到的 offset (断点)
    comment_total = Column(Integer, default=0)  # 这首歌总评论数 (首次爬拿, 全量用)
    comments_completed = Column(Integer, default=0)  # 1=全量爬完 (改走增量模式)
    comments_completed_at = Column(DateTime)  # 全量完成时间
    
    __table_args__ = (
        Index("idx_scs_priority_time", "is_priority", "last_crawled_at"),
        # 2026-09-05 断点续传索引
        Index("idx_scs_completed_time", "comments_completed", "last_comment_crawled_at"),
    )


class SongHotStats(Base):
    """歌曲热度统计 - 每日 02:00 全量跑"""
    __tablename__ = "song_hot_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    song_id = Column(Integer, nullable=False, index=True)
    stat_date = Column(DateTime, nullable=False, index=True)  # 统计日期
    comment_total = Column(Integer, default=0)
    liked_total = Column(Integer, default=0)  # 所有评论点赞总数
    hot_score = Column(Integer, default=0)  # 热度分 (comment*10 + liked*5)
    rank_24h = Column(Integer, default=0)  # 24h 排名
    prev_hot_score = Column(Integer, default=0)  # 上一次分数
    delta_24h = Column(Integer, default=0)  # 24h 增量
    
    __table_args__ = (
        Index("idx_shs_date_score", "stat_date", "hot_score"),
        Index("idx_shs_date_delta", "stat_date", "delta_24h"),
    )
