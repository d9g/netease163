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
from fastapi import FastAPI, HTTPException, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, func, desc

from netease163.spiders import (
    LyricSpider, CommentSpider, SearchSpider, SongSpider,
    PlaylistSpider, ArtistSpider, AlbumSpider, RadioSpider, ToplistSpider,
)
from netease163.utils import get_logger

logger = get_logger("netease163.api")

# ==================== Pydantic schemas ====================
class HealthResponse(BaseModel):
    status: str
    version: str = "0.2.0"


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
    }


@app.get("/health", response_model=HealthResponse, tags=["health"])
def health():
    """健康检查 (k8s 风格)"""
    return root()


@app.get("/api/v1/comment/by-name", tags=["爬虫"])
def get_comment_by_name(
    q: str = Query(..., description="歌曲名"),
    limit: int = Query(20, ge=1, le=100),
):
    """按歌曲名搜评论 - 重名时展示评论数, 用户选定后调 /comment/{song_id}"""
    from netease163.spiders.search_helpers import search_by_name
    from netease163.spiders import CommentSpider
    candidates_data = search_by_name(q, "song", limit=8)
    # 拿每首歌评论数 (10 个以内)
    out = []
    spider = CommentSpider()
    for c in candidates_data:
        song_id = c.get("id")
        if not song_id:
            continue
        try:
            cr = spider.safe_fetch(song_id, limit=1)
            count = cr.get("total", 0) if cr else 0
        except Exception:
            count = 0
        out.append({
            **c,
            "comment_total": count,
            "song_id": song_id,
        })
    return {"q": q, "count": len(out), "candidates": out}




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



# ==================== 扫码登录 (更便捷方式) ====================



# ==================== 按名称搜索 ====================
@app.get("/api/v1/candidates", tags=["搜索"])
def get_candidates(
    q: str = Query(..., description="名称关键词"),
    type: str = Query("song", description="song/artist/album/playlist"),
    limit: int = Query(10, ge=1, le=30),
):
    """按名称搜索 - 返回重名候选列表, 给前端做二次筛选"""
    from netease163.spiders.search_helpers import search_by_name
    if type not in ("song", "artist", "album", "playlist"):
        raise HTTPException(400, "type 必须为 song/artist/album/playlist")
    try:
        candidates = search_by_name(q, type, limit=limit)
        # song 类型额外加 comment_total (DB 有则用 DB, 无则实时拉)
        if type == "song":
            from netease163.storage.db import get_session
            from netease163.storage.models import Song
            session = get_session()
            try:
                song_ids = [c["id"] for c in candidates if c.get("id")]
                # DB 查
                if song_ids:
                    stmt = select(Song).where(Song.id.in_(song_ids))
                    db_songs = {s.id: s for s in session.execute(stmt).scalars().all()}
                else:
                    db_songs = {}
            finally:
                session.close()
            # 实时拉评论数 (DB 没数据的)
            from netease163.spiders.comment import CommentSpider
            spider = CommentSpider()
            for c in candidates:
                song_id = c.get("id")
                if not song_id:
                    c["comment_total"] = 0
                    continue
                db_s = db_songs.get(song_id)
                if db_s and (db_s.comment_total or 0) > 0:
                    c["comment_total"] = db_s.comment_total
                    c["in_db"] = True
                else:
                    # 实时拉 (仅前 8 个, 后续默认 0)
                    try:
                        cr = spider.safe_fetch(song_id, limit=1)
                        c["comment_total"] = (cr or {}).get("total", 0)
                        c["in_db"] = False
                    except Exception:
                        c["comment_total"] = 0
                        c["in_db"] = False
        return {"q": q, "type": type, "count": len(candidates), "candidates": candidates}
    except Exception as e:
        raise HTTPException(500, "搜索失败, 请稍后再试")




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


# ==================== 关键词管理 (9/4 老杨要求) ====================
BUILTIN_SENSITIVE_WORDS = {
    "政治", "领导人", "国家领导人", "反动", "颠覆", "分裂国家",
    "色情", "裸聊", "约炮", "一夜情", "援交", "卖淫", "嫖娼",
    "恐怖袭击", "爆炸制作", "枪支贩卖", "毒品制作", "冰毒配方",
    "赌博网站", "时时彩", "百家乐", "澳门赌场", "网络诈骗", "电信诈骗",
    "法轮功", "全能神", "观音法门", "华藏宗门",
}


def is_sensitive_keyword(keyword: str) -> bool:
    if not keyword or not isinstance(keyword, str):
        return False
    keyword_lower = keyword.strip().lower()
    for sensitive in BUILTIN_SENSITIVE_WORDS:
        if sensitive in keyword_lower or keyword_lower in sensitive:
            return True
    return False


@app.post("/api/v1/random/keywords", tags=["随机爬取"])
def api_add_keyword(payload: dict = Body(..., example={"keyword": "新歌手"})):
    """手动添加关键词到池（持久化到 DB + 敏感词过滤）"""
    keyword = (payload.get("keyword") or "").strip()
    if not keyword:
        raise HTTPException(400, "keyword 不能为空")
    if len(keyword) > 20:
        raise HTTPException(400, "keyword 长度超过 20")
    if is_sensitive_keyword(keyword):
        raise HTTPException(400, f"敏感词拒绝: {keyword}")
    from netease163.random_crawler.keywords import get_keyword_pool
    pool = get_keyword_pool()
    if keyword in pool.get_all():
        return {"success": True, "action": "skipped", "reason": "已存在", "keyword": keyword, "pool_size": len(pool.get_all())}
    pool.add(keyword)
    try:
        from netease163.storage.db import get_session
        from netease163.storage.models import SearchLog
        session = get_session()
        try:
            session.add(SearchLog(keyword=keyword, source="manual_add"))
            session.commit()
        finally:
            session.close()
    except Exception as e:
        logger.warning(f"关键词持久化失败: {e}")
    return {"success": True, "action": "added", "keyword": keyword, "pool_size": len(pool.get_all())}


@app.delete("/api/v1/random/keywords", tags=["随机爬取"])
def api_remove_keyword(payload: dict = Body(..., example={"keyword": "周杰伦"})):
    """从关键词池删除关键词（不准删内置）"""
    keyword = (payload.get("keyword") or "").strip()
    if not keyword:
        raise HTTPException(400, "keyword 不能为空")
    from netease163.random_crawler.keywords import get_keyword_pool, INITIAL_KEYWORDS
    import netease163.random_crawler.keywords as kw_module
    pool = get_keyword_pool()
    if keyword in INITIAL_KEYWORDS:
        raise HTTPException(400, f"内置关键词不能删除: {keyword}")
    all_keywords = pool.get_all()
    if keyword not in all_keywords:
        return {"success": True, "action": "skipped", "reason": "不存在", "keyword": keyword, "pool_size": len(all_keywords)}
    new_pool = list(set(all_keywords) - {keyword})
    kw_module._pool_instance._pool = new_pool
    return {"success": True, "action": "removed", "keyword": keyword, "pool_size": len(new_pool)}


@app.get("/api/v1/random/keywords/sensitive", tags=["随机爬取"])
def api_sensitive_words():
    """查看内置敏感词库"""
    return {"count": len(BUILTIN_SENSITIVE_WORDS), "words": sorted(list(BUILTIN_SENSITIVE_WORDS))}


# ==================== 触发评论抓取 (9/4) ====================
@app.post("/api/v1/crawl/comment/{song_id}", tags=["爬取"])
def api_trigger_comment_crawl(song_id: int, max_count: int = Query(100, ge=10, le=1000)):
    """触发评论抓取（异步入队，不阻塞）"""
    import threading
    from netease163.storage.db import get_session
    from netease163.storage.models import Song
    session = get_session()
    try:
        song = session.execute(select(Song).where(Song.id == song_id)).scalar_one_or_none()
        if not song:
            raise HTTPException(404, f"歌曲不存在: song_id={song_id}")
        song_name = song.name
        song_comment_count = getattr(song, 'comment_total', 0) or 0
    finally:
        session.close()
    def _crawl_comments_async(sid, mc):
        try:
            from netease163.crawler.comment import CommentCrawler
            crawler = CommentCrawler()
            stats = crawler.crawl_song_comments(sid, max_count=mc)
            logger.info(f"✅ 后台评论抓取完成: song_id={sid}, stats={stats}")
        except Exception as e:
            logger.error(f"❌ 后台评论抓取失败: song_id={sid}, error={e}")
    thread = threading.Thread(target=_crawl_comments_async, args=(song_id, max_count), daemon=True)
    thread.start()
    return {
        "success": True,
        "action": "queued",
        "song_id": song_id,
        "song_name": song_name,
        "current_comment_count": song_comment_count,
        "target_max_count": max_count,
        "thread_alive": thread.is_alive(),
    }


# ==================== 首页排行 (9/4 老杨要求) ====================
@app.get("/api/v1/rankings/hot-songs", tags=["排行"])
def api_hot_songs(
    period: str = Query("week", description="all/week/month"),
    limit: int = Query(20, ge=1, le=100),
):
    """热门歌曲排行 - 按 comment_total + 点赞总数"""
    from netease163.storage.db import get_session
    from netease163.storage.models import Song
    from sqlalchemy import select, desc
    from netease163.storage.models import Comment
    session = get_session()
    try:
        # 真实数据: 聚合 comments 表 (歌的评论数 + 点赞总数)
        stmt = (
            select(
                Song.id, Song.name, Song.artists, Song.album_name, Song.pic_url,
                func.count(Comment.id).label("real_comment_count"),
                func.coalesce(func.sum(Comment.liked_count), 0).label("liked_total"),
            )
            .outerjoin(Comment, Comment.song_id == Song.id)
            .group_by(Song.id, Song.name, Song.artists, Song.album_name, Song.pic_url)
            .having(func.count(Comment.id) > 0)
            .order_by(desc(func.count(Comment.id)))
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return {
            "period": period,
            "count": len(rows),
            "songs": [
                {
                    "rank": i + 1,
                    "id": r[0],
                    "name": r[1],
                    "artists": r[2] or [],
                    "album_name": r[3],
                    "comment_total": int(r[5] or 0),  # 真实评论数
                    "liked_total": int(r[6] or 0),
                    "pic_url": r[4],
                }
                for i, r in enumerate(rows)
            ],
        }
    finally:
        session.close()


@app.get("/api/v1/rankings/hot-comments", tags=["排行"])
def api_hot_comments(limit: int = Query(20, ge=1, le=100)):
    """神评论排行 - 按 liked_count"""
    from netease163.storage.db import get_session
    from netease163.storage.models import Comment, Song
    from sqlalchemy import select, desc
    session = get_session()
    try:
        stmt = (
            select(Comment, Song.name)
            .join(Song, Song.id == Comment.song_id)
            .where(Comment.liked_count > 0)
            .order_by(desc(Comment.liked_count))
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return {
            "count": len(rows),
            "comments": [
                {
                    "rank": i + 1,
                    "comment_id": r[0].comment_id,
                    "song_id": r[0].song_id,
                    "song_name": r[1],
                    "user_nickname": r[0].user_nickname,
                    "content": r[0].content[:200] if r[0].content else "",
                    "liked_count": r[0].liked_count or 0,
                }
                for i, r in enumerate(rows)
            ],
        }
    finally:
        session.close()


@app.get("/api/v1/rankings/trending", tags=["排行"])
def api_trending(period: str = Query("24h", description="24h/7d"), limit: int = Query(20, ge=1, le=100)):
    """24h / 7d 趋势 - 热度增长最快"""
    from netease163.storage.db import get_session
    from netease163.storage.models import SongHotStats, Song
    from sqlalchemy import select, desc
    from datetime import datetime, timedelta
    session = get_session()
    try:
        if period == "7d":
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        else:
            cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        stmt = (
            select(SongHotStats, Song)
            .join(Song, Song.id == SongHotStats.song_id)
            .where(SongHotStats.stat_date >= cutoff, SongHotStats.delta_24h > 0)
            .order_by(desc(SongHotStats.delta_24h))
            .limit(limit)
        )
        rows = session.execute(stmt).all()
        return {
            "period": period,
            "count": len(rows),
            "trending": [
                {
                    "rank": i + 1,
                    "song_id": r[1].id,
                    "song_name": r[1].name,
                    "artists": r[1].artists or [],
                    "hot_score": r[0].hot_score,
                    "delta_24h": r[0].delta_24h,
                    "comment_total": r[0].comment_total,
                }
                for i, r in enumerate(rows)
            ],
        }
    except Exception as e:
        # 表可能没数据
        return {"period": period, "count": 0, "trending": [], "note": f"暂无数据（每日 02:00 跑热度统计）: {e}"}
    finally:
        session.close()


@app.post("/api/v1/admin/run-hot-stats", tags=["排行"])
def api_run_hot_stats():
    """管理员触发：立即跑全量热度统计（默认 02:00 跑）"""
    from netease163.storage.db import get_session
    from netease163.storage.models import Song, Comment, SongHotStats
    from sqlalchemy import select, func, desc, desc
    from datetime import datetime
    from netease163.api.cst_time import now_cst
    session = get_session()
    try:
        today = now_cst().replace(hour=2, minute=0, second=0, microsecond=0)
        # 算每首歌的热度
        stmt = (
            select(
                Song.id,
                Song.comment_total,
                func.coalesce(func.sum(Comment.liked_count), 0).label("liked_total"),
            )
            .outerjoin(Comment, Comment.song_id == Song.id)
            .group_by(Song.id, Song.comment_total)
            .order_by(desc(Song.comment_total))
            .limit(500)
        )
        rows = session.execute(stmt).all()
        inserted = 0
        for i, r in enumerate(rows):
            song_id, comment_total, liked_total = r[0], r[1] or 0, r[2] or 0
            hot_score = comment_total * 10 + int(liked_total) * 5
            # 查上一次分数
            prev = session.execute(
                select(SongHotStats.hot_score)
                .where(SongHotStats.song_id == song_id)
                .order_by(desc(SongHotStats.stat_date))
                .limit(1)
            ).scalar()
            prev_score = prev or 0
            delta = hot_score - prev_score
            session.add(SongHotStats(
                song_id=song_id,
                stat_date=today,
                comment_total=comment_total,
                liked_total=int(liked_total),
                hot_score=hot_score,
                rank_24h=i + 1,
                prev_hot_score=prev_score,
                delta_24h=delta,
            ))
            inserted += 1
        session.commit()
        return {"success": True, "inserted": inserted, "stat_date": today.isoformat()}
    finally:
        session.close()
