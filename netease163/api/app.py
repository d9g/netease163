"""
FastAPI 主应用 - 提供 HTTP REST API
端口 9700 (避开现有 8501/8080/8600/9876)
"""
import sys
from pathlib import Path

# 兼容 python -m netease163.api.app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import os
import json
from typing import Optional, List
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, func

from netease163.spiders import (
    LyricSpider, CommentSpider, SearchSpider, SongSpider,
    PlaylistSpider, ArtistSpider, AlbumSpider, RadioSpider, ToplistSpider,
)
from netease163.utils import get_logger, get_db_url

logger = get_logger("netease163.api")

# ==================== Pydantic schemas ====================
class HealthResponse(BaseModel):
    status: str
    version: str = "0.2.0"
    db_url: str


class LyricResponse(BaseModel):
    song_id: int
    lyric: str
    tlyric: Optional[str] = ""


class CommentItem(BaseModel):
    id: int
    user: str
    content: str
    liked_count: int = 0
    time: int = 0


class CommentResponse(BaseModel):
    song_id: int
    total: int
    hot_comments: List[CommentItem]
    comments: List[CommentItem]


class SongItem(BaseModel):
    id: int
    name: str
    artists: List[dict]
    album: dict
    duration_ms: int = 0


class SearchItem(BaseModel):
    id: int
    name: str


class SearchResponse(BaseModel):
    keyword: str
    type: str
    count: int
    items: List[dict]


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ==================== FastAPI app ====================



# ==================== Lifespan 启动 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动: 初始化 DB + 启动 scheduler"""
    # 1. 初始化 DB
    from netease163.storage.db import init_db
    from netease163.storage.db import get_engine
    init_db()
    logger.info("✅ DB 表已初始化")

    # 2. 启动 scheduler (后台线程)
    try:
        from netease163.random_crawler import start_scheduler_in_thread
        start_scheduler_in_thread()
        logger.info("✅ Scheduler 后台启动")
    except Exception as e:
        logger.error(f"❌ Scheduler 启动失败 (不影响 API): {e}")

    yield

    # 关闭: 停 scheduler
    from netease163.random_crawler.scheduler import get_scheduler
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)
        logger.info("👋 Scheduler 已关闭")


app = FastAPI(
    title="netease163 API",
    description="网易云音乐爬虫服务 · 借鉴 NetCloud + 163yinyue · pyncm 底层",
    version="0.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """统一异常处理 (借鉴 NetCloud Response)"""
    logger.error(f"❌ {request.url.path}: {exc}", exc_info=True)
    return {"error": str(exc)}


@app.get("/", response_model=HealthResponse, tags=["health"])
def root():
    """健康检查"""
    return {
        "status": "ok",
        "version": "0.1.0",
        "db_url": get_db_url(),
    }


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health():
    """健康检查 (k8s 风格)"""
    return root()


# ==================== 歌词 ====================
@app.get("/api/v1/lyric/{song_id}", response_model=LyricResponse, tags=["爬虫"])
def get_lyric(song_id: int):
    """获取歌曲歌词"""
    result = LyricSpider().safe_fetch(song_id)
    if result is None:
        raise HTTPException(404, "歌曲不存在")
    return result


# ==================== 评论 ====================
@app.get("/api/v1/comment/{song_id}", response_model=CommentResponse, tags=["爬虫"])
def get_comment(
    song_id: int,
    limit: int = Query(20, ge=1, le=100, description="返回条数"),
    offset: int = Query(0, ge=0, description="分页偏移"),
    hot_only: bool = Query(False, description="只看热门"),
):
    """获取歌曲评论"""
    result = CommentSpider().safe_fetch(song_id, limit=limit, offset=offset, hot_only=hot_only)
    if result is None:
        raise HTTPException(404, "评论获取失败")
    return result


# ==================== 搜索 ====================
@app.get("/api/v1/search", response_model=SearchResponse, tags=["爬虫"])
def search(
    q: str = Query(..., description="搜索关键词"),
    type: str = Query("song", description="类型: song/artist/album/playlist/user"),
    limit: int = Query(20, ge=1, le=100),
):
    """搜索歌曲/歌手/专辑/歌单/用户"""
    result = SearchSpider().safe_fetch(q, search_type=type, limit=limit)
    if result is None:
        raise HTTPException(400, "搜索失败")
    return result


# ==================== 歌曲详情 ====================
@app.get("/api/v1/song/{song_id}", tags=["爬虫"])
def get_song(song_id: int):
    """获取歌曲详情"""
    result = SongSpider().safe_fetch(song_id)
    if result is None:
        raise HTTPException(404, "歌曲不存在")
    return result


# ==================== 歌单详情 ====================
@app.get("/api/v1/playlist/{playlist_id}", tags=["爬虫"])
def get_playlist(playlist_id: int):
    """获取歌单详情 (含前 100 首歌曲)"""
    result = PlaylistSpider().safe_fetch(playlist_id)
    if result is None:
        raise HTTPException(404, "歌单不存在")
    return result


# ==================== 歌手详情 ====================
@app.get("/api/v1/artist/{artist_id}", tags=["爬虫"])
def get_artist(artist_id: int):
    """获取歌手详情"""
    result = ArtistSpider().safe_fetch(artist_id)
    if result is None:
        raise HTTPException(404, "歌手不存在")
    return result


# ==================== 专辑详情 ====================
@app.get("/api/v1/album/{album_id}", tags=["爬虫"])
def get_album(album_id: int):
    """获取专辑详情 (含歌曲列表)"""
    result = AlbumSpider().safe_fetch(album_id)
    if result is None:
        raise HTTPException(404, "专辑不存在")
    return result


# ==================== 电台 ====================
@app.get("/api/v1/radio/{radio_id}", tags=["爬虫"])
def get_radio(
    radio_id: int,
    limit: int = Query(30, ge=1, le=100),
):
    """获取电台节目列表"""
    result = RadioSpider().safe_fetch(radio_id, limit=limit)
    if result is None:
        raise HTTPException(404, "电台不存在")
    return result


# ==================== 排行榜 ====================
@app.get("/api/v1/toplist/{name}", tags=["爬虫"])
def get_toplist(
    name: str,
    limit: int = Query(50, ge=1, le=200),
):
    """获取排行榜 (cloud/new/original/...)"""
    toplist_id = ToplistSpider.get_toplist_ids.get(name)
    if not toplist_id:
        raise HTTPException(400, f"未知榜单: {name}, 可选: {list(ToplistSpider.get_toplist_ids.keys())}")
    result = ToplistSpider().safe_fetch(toplist_id, limit=limit)
    if result is None:
        raise HTTPException(404, "榜单获取失败")
    return result


@app.get("/api/v1/toplist", tags=["爬虫"])
def list_toplists():
    """列出所有可用榜单"""
    return {"toplists": ToplistSpider.get_toplist_ids}



# ==================== 登录态 API ====================
@app.get("/api/v1/login/status", tags=["登录"])
def api_login_status():
    """查看当前登录状态"""
    from netease163.login import LoginManager
    mgr = LoginManager()
    return {
        "logged_in": mgr.is_logged_in,
        "user_id": mgr.user_id,
        "nickname": mgr.nickname,
    }


@app.post("/api/v1/login/phone", tags=["登录"])
def api_login_phone(
    account: str = Query(..., description="手机号"),
    password: str = Query(..., description="密码"),
):
    """手机密码登录"""
    from netease163.login import LoginManager
    mgr = LoginManager()
    success = mgr.login_with_phone(account, password)
    if success:
        return {"success": True, "user_id": mgr.user_id, "nickname": mgr.nickname}
    return {"success": False, "error": "登录失败, 请检查账号密码或风控"}


@app.post("/api/v1/login/email", tags=["登录"])
def api_login_email(
    account: str = Query(..., description="邮箱"),
    password: str = Query(..., description="密码"),
):
    """邮箱密码登录"""
    from netease163.login import LoginManager
    mgr = LoginManager()
    success = mgr.login_with_email(account, password)
    if success:
        return {"success": True, "user_id": mgr.user_id, "nickname": mgr.nickname}
    return {"success": False, "error": "登录失败, 请检查账号密码或风控"}


@app.post("/api/v1/login/logout", tags=["登录"])
def api_logout():
    """登出"""
    from netease163.login import LoginManager
    LoginManager().logout()
    return {"success": True}


# ==================== 我的音乐 (需登录) ====================
@app.get("/api/v1/my/favorite", tags=["我的音乐"])
def api_my_favorite(limit: int = Query(50, ge=1, le=200)):
    """我的红心歌单"""
    from netease163.login import get_my_favorite
    try:
        return get_my_favorite(limit=limit)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/v1/my/recommend", tags=["我的音乐"])
def api_my_recommend():
    """每日推荐歌单"""
    from netease163.login import get_my_recommend
    try:
        return get_my_recommend()
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/v1/my/fm", tags=["我的音乐"])
def api_my_fm():
    """私人 FM"""
    from netease163.login import get_my_fm
    try:
        return get_my_fm()
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/v1/my/playlists", tags=["我的音乐"])
def api_my_playlists(limit: int = Query(30, ge=1, le=100)):
    """我的所有歌单"""
    from netease163.login import get_my_playlists
    try:
        return get_my_playlists(limit=limit)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))



# ==================== 随机爬取 (后台调度) ====================
@app.get("/api/v1/random/crawl/now", tags=["随机爬取"])
def api_random_crawl_now(target: int = Query(10, ge=1, le=50, description="本轮目标数")):
    """手动触发: 跑一轮随机爬取 (返回 stats)"""
    from netease163.random_crawler.scheduler import get_crawler
    try:
        stats = get_crawler().run_one_round()
        return {
            "success": True,
            "stats": stats,
            "today_count": get_crawler().today_count,
            "today_target": 100,
        }
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/v1/random/keywords", tags=["随机爬取"])
def api_get_keywords():
    """查看当前关键词池"""
    from netease163.random_crawler import get_keyword_pool
    pool = get_keyword_pool()
    return {
        "count": len(pool.get_all()),
        "keywords": pool.get_all(),
    }


@app.post("/api/v1/random/keywords/extend", tags=["随机爬取"])
def api_extend_keywords():
    """手动触发: 从已有 songs.name 扩展关键词池"""
    from netease163.random_crawler.scheduler import get_crawler
    from netease163.random_crawler.keywords import get_keyword_pool
    try:
        before = len(get_keyword_pool().get_all())
        get_crawler().extend_keywords_daily()
        after = len(get_keyword_pool().get_all())
        return {"success": True, "before": before, "after": after, "added": after - before}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/v1/stats/crawler", tags=["统计"])
def api_crawler_stats():
    """爬取统计: 歌曲数 / 评论数 / 今日累计 / 爬取日志"""
    from netease163.storage.db import get_session
    from netease163.storage.models import Song, Comment, CrawlLog
    session = get_session()
    try:
        songs_total = session.execute(select(func.count(Song.id))).scalar() or 0
        comments_total = session.execute(select(func.count(Comment.id))).scalar() or 0
        from datetime import datetime, timedelta
        today = (datetime.now() - timedelta(hours=24)).isoformat()
        crawls_24h = session.execute(
            select(func.count(CrawlLog.id)).where(CrawlLog.crawled_at >= today)
        ).scalar() or 0
        crawls_success = session.execute(
            select(func.count(CrawlLog.id)).where(CrawlLog.crawled_at >= today, CrawlLog.success == 1)
        ).scalar() or 0
        # 今日入库
        today_str = datetime.now().strftime("%Y-%m-%d")
        from sqlalchemy import and_
        songs_today = session.execute(
            select(func.count(Song.id)).where(Song.created_at >= today_str)
        ).scalar() or 0
        from netease163.random_crawler.scheduler import get_crawler
        return {
            "songs_total": songs_total,
            "songs_today": songs_today,
            "comments_total": comments_total,
            "crawls_24h": crawls_24h,
            "crawls_24h_success_rate": f"{crawls_success}/{crawls_24h}",
            "crawler_today_count": get_crawler().today_count,
            "crawler_target": 100,
            "db_url": get_db_url(),
        }
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        session.close()


@app.get("/api/v1/random/songs/latest", tags=["随机爬取"])
def api_latest_crawled_songs(limit: int = Query(20, ge=1, le=100)):
    """最新入库的歌曲"""
    from netease163.storage.db import get_session
    from netease163.storage.models import Song
    session = get_session()
    try:
        stmt = select(Song).order_by(Song.created_at.desc()).limit(limit)
        rows = session.execute(stmt).scalars().all()
        return {
            "count": len(rows),
            "songs": [
                {
                    "id": r.id,
                    "name": r.name,
                    "artists": r.artists or [],
                    "album_name": r.album_name,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }
    finally:
        session.close()



# ==================== 扫码登录 (老杨 18:06 路径 A - 绕过 8821 风控) ====================
@app.get("/api/v1/login/qrcode", tags=["登录"])
def api_qrcode_generate():
    """生成扫码登录二维码 (返回 base64 图片)"""
    from netease163.login import generate_qr_key
    try:
        return generate_qr_key()
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/v1/login/qrcode/check", tags=["登录"])
def api_qrcode_check(unikey: str = Query(..., description="扫码 unikey")):
    """轮询扫码状态: 0=等待扫码 1=已扫码待确认 2/803=成功 8821=过期"""
    from netease163.login import check_qr_login
    return check_qr_login(unikey)


@app.post("/api/v1/login/cookie", tags=["登录"])
def api_login_cookie(music_u: str = Query(..., description="MUSIC_U cookie 值")):
    """Cookie 兜底登录 (浏览器复制的 MUSIC_U)

    步骤:
    1. 浏览器打开 music.163.com 登录
    2. F12 → Console 输入 document.cookie
    3. 找 MUSIC_U=xxx; 复制值 (只要 MUSIC_U= 后面的部分)
    4. 调用本接口
    """
    from netease163.login import login_via_cookie
    return login_via_cookie(music_u=music_u)
