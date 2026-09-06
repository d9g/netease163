"""
随机爬虫 - 选项 C (榜单 70% + 关键词 30% 混合)

设计:
- 每天目标 100+ 首 (需求)
- 反爬: 随机间隔 8-25 秒
- 增量: 已入库的 song 跳过
- 持久化: songs + comments + crawl_logs

调度策略 (需求):
- 每 30 分钟跑一轮 (24h × 2 = 48 次/天)
- 每轮: 榜单 5-7 首 + 关键词 3-5 首 ≈ 10 首/轮
- 48 × 10 = 480 次/天 (远大于 100 首/天目标)
"""
import random
import time
import json
from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy import select
from ..storage.db import get_session
from ..storage.models import Song, Comment, CrawlLog
from ..spiders.search import SearchSpider
from ..spiders.song import SongSpider
from ..spiders.comment import CommentSpider
from ..spiders.toplist import ToplistSpider
from ..utils import get_logger
from .keywords import get_keyword_pool
# 2026-09-05 修复: spider.py 行 250 _save_comments 里 now_cst() 未定义 (估计是一直被 try/except 吞了)
from ..api.cst_time import now_cst

logger = get_logger("netease163.random")

# ==================== 配置 ====================
MIN_INTERVAL = 8  # 最小间隔秒
MAX_INTERVAL = 25  # 最大间隔秒

# 每天目标
DAILY_TARGET = 100  # 需求 100+ 首/天

# 每轮目标
# 2026-09-06 删 ROUND_TOPLIST_SONGS / ROUND_KEYWORD_SONGS / ROUND_TARGET 死代码 (外部审计 #10)
# run_one_round 实际用 _fetch_via_toplist(top_n=2) + _fetch_via_keyword(top_n=1)
# 这些常量定义但从未引用, 保留造成"配置与行为不一致"误解

# 跟时间做朋友分配 () 
# 每天 100 首: 50% 重爬(7天前) + 30% 新歌 + 20% 评论增量
# 每轮 10 首: 5 重爬 + 3 新歌 + 2 评论
RE_WEIGHT = 0.50  # 50% 重新更新
NEW_WEIGHT = 0.30  # 30% 新歌
COMMENT_WEIGHT = 0.20  # 20% 评论增量
ROUND_RE_SONGS = 5  # 每轮重爬 5 首
ROUND_NEW_SONGS = 3  # 每轮新歌 3 首
ROUND_COMMENT_SONGS = 2  # 每轮评论增量 2 首
ROUND_FULL_CRAWL_SONGS = 3  # 每轮全量爬 3 首未完成的歌 (9/6 21:03 老杨拍板: 补齐到 API 上限 1500-2000 条)
STALE_DAYS = 7  # 7 天前的歌优先重爬

# 跑批轮次 (24h / 20min = 72 轮)
DAILY_ROUNDS = 72


def random_sleep():
    """反爬: 随机间隔 [8, 25] 秒"""
    delay = random.uniform(MIN_INTERVAL, MAX_INTERVAL)
    logger.debug(f"⏳ 等待 {delay:.1f}s (反爬随机)")
    time.sleep(delay)


class RandomCrawler:
    """随机爬虫主控"""

    def __init__(self):
        self.search_spider = SearchSpider()
        self.song_spider = SongSpider()
        self.comment_spider = CommentSpider()
        self.toplist_spider = ToplistSpider()
        self.kw_pool = get_keyword_pool()

        # 统计
        self.today_count = 0
        self.today_date = datetime.now().date()

    def _reset_if_new_day(self):
        """跨天重置计数"""
        today = datetime.now().date()
        if today != self.today_date:
            logger.info(f"📅 跨天重置计数 (昨天 {self.today_count} 首)")
            self.today_count = 0
            self.today_date = today

    def _is_song_exists(self, song_id: int) -> bool:
        """检查 song 是否已入库"""
        session = get_session()
        try:
            stmt = select(Song.id).where(Song.id == song_id)
            return session.execute(stmt).first() is not None
        finally:
            session.close()

    def _get_priority_targets(self, limit: int) -> List[Dict]:
        """跟时间做朋友: 选 7 天前爬过但有评论的歌 (按 hot_score 排)
        返回: [{song_id, name, last_crawled_at, comment_count}]
        """
        from datetime import datetime, timedelta
        from ..storage.models import SongCrawlStatus
        from sqlalchemy import and_, desc
        cutoff = (datetime.now() - timedelta(days=STALE_DAYS)).isoformat()
        session = get_session()
        try:
            # 找 7 天前爬过的歌, 按 songs.comment_total desc
            stmt = (
                select(Song.id, Song.name, Song.comment_total, SongCrawlStatus.last_crawled_at)
                .join(SongCrawlStatus, SongCrawlStatus.song_id == Song.id)
                .where(SongCrawlStatus.last_crawled_at < cutoff)
                .order_by(desc(Song.id))  # 按 ID 倒序 (近期的优先)
                .limit(limit)
            )
            rows = session.execute(stmt).all()
            return [
                {"song_id": r[0], "name": r[1], "comment_count": r[2] or 0, "last_crawled_at": r[3]}
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"⚠️  查 priority 失败: {e}")
            return []
        finally:
            session.close()

    def _get_comment_targets(self, limit: int) -> List[Dict]:
        """跟时间做朋友: 选热门但 3 天没爬评论的歌"""
        from datetime import datetime, timedelta
        from ..storage.models import SongCrawlStatus
        from sqlalchemy import and_, desc
        cutoff = (datetime.now() - timedelta(days=3)).isoformat()
        session = get_session()
        try:
            # 3 天没爬评论 + 有评论的歌
            stmt = (
                select(Song.id, Song.name, Song.comment_total)
                .outerjoin(
                    SongCrawlStatus,
                    and_(SongCrawlStatus.song_id == Song.id, SongCrawlStatus.last_comment_crawled_at >= cutoff)
                )
                .where(SongCrawlStatus.song_id.is_(None))
                .order_by(desc(Song.id))
                .limit(limit)
            )
            rows = session.execute(stmt).all()
            return [{"song_id": r[0], "name": r[1], "comment_count": r[2] or 0} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️  查 comment 目标失败: {e}")
            return []
        finally:
            session.close()

    def _get_uncompleted_songs(self, limit: int) -> List[Dict]:
        """2026-09-05 断点续传: 选全量未完成的歌

        优先级 (依次 fallback):
        1. 已知 comment_total > 0 且 offset < total (已首爬, 需要续) - 优先 (热门, 必定有评论)
        2. offset == 0 (从未首爬) - 作为后备
        """
        from ..storage.models import SongCrawlStatus
        from sqlalchemy import desc
        session = get_session()
        try:
            # 优先: 已知 total 且 未完成
            # 2026-09-05 修复: 用 SongCrawlStatus.comment_total (不是 Song.comment_total)
            stmt1 = (
                select(Song.id, Song.name,
                       SongCrawlStatus.comment_total,
                       SongCrawlStatus.last_comment_offset,
                       SongCrawlStatus.comments_completed)
                .join(SongCrawlStatus, SongCrawlStatus.song_id == Song.id)
                .where(SongCrawlStatus.comments_completed == 0)
                .where(SongCrawlStatus.comment_total > 0)
                .where(SongCrawlStatus.last_comment_offset < SongCrawlStatus.comment_total)
                .order_by(desc(SongCrawlStatus.comment_total))
                .limit(limit)
            )
            rows = session.execute(stmt1).all()
            if rows:
                return [
                    {
                        "song_id": r[0], "name": r[1],
                        "comment_total": r[2] or 0,
                        "last_comment_offset": r[3] or 0,
                        "comments_completed": r[4] or 0,
                    }
                    for r in rows
                ]
            # 后备: offset=0 未首爬 (但可能 total=0 是没评论的歌)
            stmt2 = (
                select(Song.id, Song.name,
                       SongCrawlStatus.comment_total,
                       SongCrawlStatus.last_comment_offset,
                       SongCrawlStatus.comments_completed)
                .join(SongCrawlStatus, SongCrawlStatus.song_id == Song.id)
                .where(SongCrawlStatus.comments_completed == 0)
                .where(SongCrawlStatus.last_comment_offset == 0)
                .where(SongCrawlStatus.comment_total > 0)  # 排除 total=0 (可能无评论)
                .order_by(desc(SongCrawlStatus.comment_total))
                .limit(limit)
            )
            rows = session.execute(stmt2).all()
            return [
                {
                    "song_id": r[0], "name": r[1],
                    "comment_total": r[2] or 0,
                    "last_comment_offset": r[3] or 0,
                    "comments_completed": r[4] or 0,
                }
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"⚠️  查 uncompleted 目标失败: {e}")
            return []
        finally:
            session.close()

    def _mark_song_crawled(self, song_id: int, comment_crawled: bool = False):
        """记录 song 爬取状态"""
        from datetime import datetime
        from ..storage.models import SongCrawlStatus
        from sqlalchemy import select
        session = get_session()
        try:
            stmt = select(SongCrawlStatus).where(SongCrawlStatus.song_id == song_id)
            row = session.execute(stmt).scalar_one_or_none()
            now = datetime.now()
            if not row:
                row = SongCrawlStatus(
                    song_id=song_id, last_crawled_at=now, crawl_count=1,
                    last_comment_crawled_at=now if comment_crawled else None,
                    comment_crawl_count=1 if comment_crawled else 0,
                )
                session.add(row)
            else:
                row.last_crawled_at = now
                row.crawl_count = (row.crawl_count or 0) + 1
                if comment_crawled:
                    row.last_comment_crawled_at = now
                    row.comment_crawl_count = (row.comment_crawl_count or 0) + 1
            session.commit()
        except Exception as e:
            logger.warning(f"⚠️  标记 song 状态失败: {e}")
            session.rollback()
        finally:
            session.close()

    def _save_song(self, song_data: Dict[str, Any]) -> bool:
        """保存歌曲到 DB"""
        song_id = song_data.get("id")
        if not song_id:
            return False
        if self._is_song_exists(song_id):
            logger.debug(f"⏭️  歌曲已入库: {song_id} - {song_data.get('name')}")
            return False
        session = get_session()
        try:
            song = Song(
                id=song_id,
                name=song_data.get("name", ""),
                artists=song_data.get("artists", []),
                album_id=song_data.get("album_id"),
                album_name=song_data.get("album", ""),
                duration_ms=song_data.get("duration_ms", song_data.get("duration", 0)),
                pic_url=song_data.get("pic_url", ""),
                comment_total=song_data.get("comment_total", 0) or 0,
            )
            session.merge(song)
            session.commit()
            logger.info(f"💾 入库歌曲: {song_id} - {song_data.get('name')}")
            return True
        except Exception as e:
            logger.error(f"❌ 入库失败: {e}")
            session.rollback()
            return False
        finally:
            session.close()

    def _save_comments(self, song_id: int, comments: List[Dict], is_hot: bool = False) -> int:
        """保存评论 (upsert by (song_id, comment_id) - 审计 #7 修复)

        不再用 merge (按主键 id 匹配导致重复), 改用:
        - 先查该 (song_id, comment_id) 是否存在
        - 存在 → 更新 content/liked_count/comment_time/crawled_at
        - 不存在 → 新增
        - ai_score 等 LLM 评分字段保留 (不重置)
        """
        if not comments:
            return 0
        session = get_session()
        inserted = 0
        updated = 0
        try:
            for c in comments:
                # 2026-09-05 老杨反馈 https://163.d9g.com.cn/ 评论数不涨:
                # CommentSpider.fetch() 返回的 dict 用 "id" 字段, 但 ORM 用 "comment_id",
                # 导致 _save_comments 拿不到 comment_id → 全部 continue 跳过.
                # 根因修复: 加 "id" 兑底, 同时保留 "comment_id" 以防其他调用方依赖
                comment_id = c.get("comment_id") or c.get("id") or 0
                if not comment_id:
                    continue  # 没有 comment_id 的跳过
                # 查已存在
                existing = session.execute(
                    select(Comment).where(
                        Comment.song_id == song_id,
                        Comment.comment_id == comment_id,
                    )
                ).scalar_one_or_none()
                if existing:
                    # 更新
                    existing.content = c.get("content", existing.content)
                    existing.user_nickname = c.get("user", c.get("user_nickname", existing.user_nickname))
                    existing.liked_count = c.get("liked_count", c.get("likedCount", existing.liked_count))
                    existing.comment_time = c.get("comment_time", c.get("time", existing.comment_time))
                    existing.is_hot = 1 if c.get("is_hot") else existing.is_hot
                    existing.crawled_at = now_cst()
                    # ai_* 保留, 不动
                    updated += 1
                else:
                    # 新增
                    comment = Comment(
                        comment_id=comment_id,
                        song_id=song_id,
                        user_nickname=c.get("user", c.get("user_nickname", "")),
                        content=c.get("content", ""),
                        liked_count=c.get("liked_count", c.get("likedCount", 0)),
                        comment_time=c.get("comment_time", c.get("time", 0)),
                        is_hot=1 if (c.get("is_hot") or is_hot) else 0,
                    )
                    session.add(comment)
                    inserted += 1
            session.commit()
            logger.info(f"💾 入库评论: song={song_id}, 新增 {inserted} + 更新 {updated} = {inserted + updated} 条")
            return inserted + updated
        except Exception as e:
            logger.error(f"❌ 入库评论失败: {e}")
            session.rollback()
            return 0
        finally:
            session.close()

    def _log_crawl(self, spider_name: str, target_id: int, success: bool, error: str = "", duration_ms: int = 0):
        """写爬取日志"""
        session = get_session()
        try:
            log = CrawlLog(
                spider_name=spider_name,
                target_id=target_id,
                success=1 if success else 0,
                error_msg=error[:500] if error else "",
                duration_ms=duration_ms,
            )
            session.add(log)
            session.commit()
        except Exception as e:
            logger.warning(f"⚠️  写 crawl_log 失败: {e}")
        finally:
            session.close()

    def _fetch_via_toplist(self, top_n: int = 2) -> List[Dict]:
        """榜单方式: 6 个榜单各抽 1-2 首"""
        from ..spiders.toplist import TOPLIST_IDS
        songs = []
        # 6 个榜单: hot/soar/new/original/electronic/ACG
        toplists = list(TOPLIST_IDS.items())
        random.shuffle(toplists)
        for tl_name, tl_id in toplists:
            if len(songs) >= top_n:
                break
            try:
                t0 = time.time()
                result = self.toplist_spider.fetch(toplist_id=tl_id, limit=20)
                duration = int((time.time() - t0) * 1000)
                if result and result.get("tracks"):
                    # 随机抽 1-2 首
                    n = min(random.randint(1, 2), top_n - len(songs), len(result["tracks"]))
                    picks = random.sample(result["tracks"], n)
                    songs.extend(picks)
                    self._log_crawl("ToplistSpider", tl_id, True, "", duration)
                    logger.info(f"📊 {tl_name}榜: 抽 {n} 首")
                random_sleep()
            except Exception as e:
                logger.warning(f"⚠️  {tl_name}榜拉取失败: {e}")
                self._log_crawl("ToplistSpider", tl_id, False, str(e))
        return songs

    def _fetch_via_keyword(self, top_n: int = 1) -> List[Dict]:
        """关键词方式: 随机抽 1-2 个关键词, 每个搜 Top 3"""
        songs = []
        keywords = self.kw_pool.get_random(n=2)
        for kw in keywords:
            if len(songs) >= top_n:
                break
            try:
                t0 = time.time()
                result = self.search_spider.fetch(keyword=kw, search_type="song", limit=3)
                duration = int((time.time() - t0) * 1000)
                if result and result.get("items"):
                    n = min(random.randint(1, 2), top_n - len(songs), len(result["items"]))
                    picks = random.sample(result["items"], n)
                    songs.extend(picks)
                    self._log_crawl("SearchSpider", 0, True, "", duration)
                    # 写 SearchLog (用于次日关键词扩展)
                    session = get_session()
                    try:
                        from ..storage.models import SearchLog
                        session.add(SearchLog(keyword=kw, search_type="song", result_count=len(result["items"])))
                        session.commit()
                    finally:
                        session.close()
                    logger.info(f"🔍 关键词 '{kw}': 抽 {n} 首")
                random_sleep()
            except Exception as e:
                logger.warning(f"⚠️  关键词 '{kw}' 搜失败: {e}")
                self._log_crawl("SearchSpider", 0, False, str(e))
        return songs

    def _fetch_comments_for_song(self, song_id: int, full_crawl: bool = False) -> int:
        """拉一首的评论 (2026-09-05 老杨反馈重写)

        设计:
        - full_crawl=False (默认) 增量: 拉最新一页 (offset=0, limit=100), 不动 offset 状态
        - full_crawl=True 全量: 断点续传, 从 last_comment_offset 开始分页爬到 total 为止
        - hot_comments 一次性爬 (不分页, 15 条固定)
        - (song_id, comment_id) UNIQUE 防止重复

        返回: 本次实际新增条数 (inserted)
        """
        try:
            from datetime import datetime
            from ..storage.models import SongCrawlStatus
            from sqlalchemy import select
            # 查当前爬取状态
            session = get_session()
            try:
                stmt = select(SongCrawlStatus).where(SongCrawlStatus.song_id == song_id)
                status = session.execute(stmt).scalar_one_or_none()
                if status is None:
                    status = SongCrawlStatus(song_id=song_id)
                    session.add(status)
                    session.commit()
                # 准备状态
                last_offset = status.last_comment_offset or 0
                completed = bool(status.comments_completed)
                comment_total = status.comment_total or 0
            finally:
                session.close()

            # 分页参数
            PAGE_SIZE = 100  # 网易云 max
            total_saved_this_run = 0

            # 1. hot_comments 一次性 (永远爬, 每次都同步入库)
            t0 = time.time()
            hot_result = self.comment_spider.fetch(song_id=song_id, limit=1, offset=0, hot_only=True)
            duration_hot = int((time.time() - t0) * 1000)
            if hot_result and hot_result.get("hot_comments"):
                saved_hot = self._save_comments(song_id, hot_result["hot_comments"], is_hot=True)
                total_saved_this_run += saved_hot
                self._log_crawl("CommentSpider.hot", song_id, True, "", duration_hot)

            # 2. 增量模式 (默认) + 全量完成度判定
            # 2026-09-06 老杨反馈: 评论完成度 0/272 → 真根因 total 未回写 + 增量模式不标完成
            # 修法: 拉完第一页就拿 total (即使已知)，实时更新 + 判定完成
            if not full_crawl:
                t0 = time.time()
                result = self.comment_spider.fetch(song_id=song_id, limit=PAGE_SIZE, offset=0)
                duration = int((time.time() - t0) * 1000)
                if result:
                    # 强制回写 total (即使已有, 确保 DB 准确)
                    api_total = result.get("total", 0)
                    if api_total:
                        if api_total != comment_total:
                            self._update_status_field(song_id, "comment_total", api_total)
                            comment_total = api_total
                        # 同步回写 songs.comment_total (9/6 19:48 修复: 网易云官方评论数没入 songs 表)
                        self._update_song_comment_total(song_id, api_total)
                    saved = self._save_comments(song_id, result.get("comments", []))
                    total_saved_this_run += saved
                    self._log_crawl("CommentSpider", song_id, True, "", duration)
                    # 增量模式: 评论数 < PAGE_SIZE 说明已全部爬过 (尾页)
                    page_size = len(result.get("comments", []))
                    if comment_total > 0 and page_size >= comment_total:
                        self._update_status_field(song_id, "comments_completed", 1)
                        self._update_status_field(song_id, "comments_completed_at", "now_cst")
                        self._update_status_field(song_id, "last_comment_offset", comment_total)
                return total_saved_this_run

            # 3. 全量模式 + 断点续传
            # 拿 total (强制 first fetch, 防止 total=0 卡死)
            if not comment_total:
                t0 = time.time()
                first = self.comment_spider.fetch(song_id=song_id, limit=1, offset=0)
                duration_first = int((time.time() - t0) * 1000)
                if first and first.get("total"):
                    comment_total = first["total"]
                    self._update_status_field(song_id, "comment_total", comment_total)
                    self._update_song_comment_total(song_id, comment_total)
                self._log_crawl("CommentSpider.first", song_id, True, "", duration_first)
                if comment_total == 0:
                    # 没评论的歌, 标记完成
                    self._update_status_field(song_id, "comments_completed", 1)
                    self._update_status_field(song_id, "comments_completed_at", "now_cst")
                    return total_saved_this_run

            # 分页爬 (从 last_offset 到 total)
            MAX_PAGES_PER_RUN = 20  # 9/6 21:03 一次最多 20 页 (20*100=2000 条/次, 达到 API 上限)
                                    # 反爬限速靠 random_sleep (8-25s/页) 足以保证安全
            pages_done = 0
            current_offset = last_offset
            hit_api_limit = False  # 9/6 21:03 API offset 返空 (网易云上限 ~1000)
            while pages_done < MAX_PAGES_PER_RUN and current_offset < comment_total:
                t0 = time.time()
                result = self.comment_spider.fetch(
                    song_id=song_id, limit=PAGE_SIZE, offset=current_offset
                )
                duration = int((time.time() - t0) * 1000)
                if not result:
                    break
                page_comments = result.get("comments", [])
                if not page_comments:
                    # 这一页空了 → 9/6 21:03 老杨反馈: 可能是网易云 API 限制
                    # (实测 offset > ~1000 后 API 返空)
                    hit_api_limit = True
                    logger.info(f"⚠️  song={song_id} offset={current_offset} API 返空 (网易云限制 ~1000), 标记完成")
                    break
                saved = self._save_comments(song_id, page_comments)
                total_saved_this_run += saved
                self._log_crawl(
                    "CommentSpider",
                    song_id, True,
                    f"offset={current_offset} total={comment_total}",
                    duration,
                )
                current_offset += PAGE_SIZE
                pages_done += 1
                # 限速 (单页间隔)
                if current_offset < comment_total and pages_done < MAX_PAGES_PER_RUN:
                    random_sleep()
                # 预判下一页会空 → 提前标记完成
                if len(page_comments) < PAGE_SIZE:
                    hit_api_limit = True
                    break

            # 4. 更新爬取状态
            self._update_status_field(song_id, "last_comment_offset", current_offset)
            self._update_status_field(song_id, "comment_crawl_count", "inc")  # +1
            # 判断是否完成 (9/6 21:03: 加 hit_api_limit 判断, API 上限就不再重复轮询)
            if current_offset >= comment_total or hit_api_limit:
                self._update_status_field(song_id, "comments_completed", 1)
                self._update_status_field(song_id, "comments_completed_at", "now_cst")
                if hit_api_limit and current_offset < comment_total:
                    # API 上限, 记录警告
                    pct = current_offset * 100 // max(comment_total, 1)
                    logger.warning(f"⚠️  song={song_id} 爬到 API 上限 ({current_offset}/{comment_total}={pct}%, 剩余 {comment_total - current_offset} 条网易云不返)")
            return total_saved_this_run
        except Exception as e:
            logger.warning(f"⚠️  song={song_id} 评论拉取失败: {e}")
            self._log_crawl("CommentSpider", song_id, False, str(e))
        return 0

    def _update_status_field(self, song_id: int, field: str, value):
        """更新 song_crawl_status 单个字段 (避免 重新加载整个状态)

        value: int / str(虚拟值 "now_cst" 表示 datetime.now())
        """
        from datetime import datetime
        from sqlalchemy import update
        from ..storage.models import SongCrawlStatus
        from ..api.cst_time import now_cst
        session = get_session()
        try:
            if value == "now_cst":
                value = now_cst()
            elif value == "inc":
                # 自增, 单独 query
                stmt = select(SongCrawlStatus).where(SongCrawlStatus.song_id == song_id)
                row = session.execute(stmt).scalar_one_or_none()
                if row:
                    row.comment_crawl_count = (row.comment_crawl_count or 0) + 1
                    session.commit()
                return
            else:
                # 确保 row 存在
                stmt = select(SongCrawlStatus).where(SongCrawlStatus.song_id == song_id)
                row = session.execute(stmt).scalar_one_or_none()
                if row is None:
                    row = SongCrawlStatus(song_id=song_id, **{field: value})
                    session.add(row)
                else:
                    setattr(row, field, value)
                session.commit()
        except Exception as e:
            logger.warning(f"⚠️  更新状态字段 {field}={value} 失败: {e}")
            session.rollback()
        finally:
            session.close()

    def _update_song_comment_total(self, song_id: int, total: int):
        """同步回写 songs.comment_total (9/6 19:48 修复)

        之前只写 song_crawl_status.comment_total, 导致前端显示 0
        现在拉到 total 时同步回写 songs 表
        """
        if not total or total <= 0:
            return
        session = get_session()
        try:
            from ..storage.models import Song
            stmt = select(Song).where(Song.id == song_id)
            row = session.execute(stmt).scalar_one_or_none()
            if row:
                # 只在不一致时更新, 减少写库
                if (row.comment_total or 0) != total:
                    row.comment_total = total
                    session.commit()
        except Exception as e:
            logger.warning(f"⚠️  回写 songs.comment_total={total} 失败: {e}")
            session.rollback()
        finally:
            session.close()

    def run_one_round(self) -> Dict[str, int]:
        """跑一轮: 跟时间做朋友 () 
        5 首重爬(7天前) + 3 首新歌(榜单/关键词) + 2 首评论增量 = 10 首
        """
        # 2026-09-06 P2 #7: 加单实例锁 fcntl.flock, 防止多入口 (scheduler/cron/manual) 并发跑
        # lock_path = /root/netease163/data/.spider.lock
        import fcntl
        from pathlib import Path
        lock_path = Path(__file__).parent.parent.parent / "data" / ".spider.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fp = open(lock_path, "w")
        try:
            try:
                fcntl.flock(lock_fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                logger.warning("⚠️  另一进程已在跑 run_one_round, 本次跳过 (防止重复爬取)")
                return {"songs_new": 0, "songs_recrawl": 0, "songs_dup": 0, "comments_new": 0, "comment_inc": 0, "skipped": 1}
            return self._run_one_round_inner()
        finally:
            try:
                lock_fp.close()
            except Exception:
                pass

    def _run_one_round_inner(self) -> Dict[str, int]:
        """实际跑一轮逻辑 (加锁后调用)"""
        self._reset_if_new_day()
        round_start = time.time()
        logger.info(f"🏃 跑一轮 (今天累计 {self.today_count}/{DAILY_TARGET}) 分配: 重爬 {ROUND_RE_SONGS} + 新歌 {ROUND_NEW_SONGS} + 评论增量 {ROUND_COMMENT_SONGS} + 全量补齐 {ROUND_FULL_CRAWL_SONGS}")

        stats = {"songs_new": 0, "songs_recrawl": 0, "songs_dup": 0, "comments_new": 0, "comment_inc": 0}

        # 1. 重爬 5 首 (7 天前的歌, 跟时间做朋友)
        priority_targets = self._get_priority_targets(limit=ROUND_RE_SONGS)
        for t in priority_targets:
            song_id = t["song_id"]
            comments_saved = self._fetch_comments_for_song(song_id)
            stats["comments_new"] += comments_saved
            self._mark_song_crawled(song_id, comment_crawled=True)
            stats["songs_recrawl"] += 1
            random_sleep()

        # 2. 新歌 3 首 (榜单/关键词)
        # 从 6 榜单 + 4 关键词里取 3 首 (50% 榜单 + 50% 关键词)
        tl_songs = self._fetch_via_toplist(top_n=2)
        kw_songs = self._fetch_via_keyword(top_n=1)
        new_songs = tl_songs + kw_songs
        for s in new_songs:
            song_id = s.get("id")
            if not song_id:
                continue
            if self._save_song(s):
                stats["songs_new"] += 1
                self.today_count += 1
            else:
                stats["songs_dup"] += 1
            self._mark_song_crawled(song_id, comment_crawled=False)
            comments_saved = self._fetch_comments_for_song(song_id)
            stats["comments_new"] += comments_saved
            random_sleep()

        # 3. 评论增量 2 首 (3 天没爬评论的热门歌)
        comment_targets = self._get_comment_targets(limit=ROUND_COMMENT_SONGS)
        for t in comment_targets:
            song_id = t["song_id"]
            comments_saved = self._fetch_comments_for_song(song_id)
            stats["comment_inc"] += comments_saved
            stats["comments_new"] += comments_saved
            self._mark_song_crawled(song_id, comment_crawled=True)
            random_sleep()

        # 4. 全量爬 3 首未完成评论的歌 (9/6 21:03 老杨拍板)
        #    断点续传: 从 last_comment_offset 爬到 comment_total
        #    防反爬: 一次轮 3 首 (每首 20 页 × 8-25s 间隔 = 160-500s, 总 8-25 分钟)
        uncompleted = self._get_uncompleted_songs(limit=ROUND_FULL_CRAWL_SONGS)
        for t in uncompleted:
            song_id = t["song_id"]
            logger.info(f"🎯 全量爬 song={song_id} (offset={t.get('last_comment_offset')}/{t.get('comment_total')}, {(t.get('last_comment_offset') or 0) * 100 // max(t.get('comment_total') or 1, 1)}%)")
            comments_saved = self._fetch_comments_for_song(song_id, full_crawl=True)
            stats["comment_inc"] += comments_saved
            stats["comments_new"] += comments_saved
            self._mark_song_crawled(song_id, comment_crawled=True)
            random_sleep()

        duration = int((time.time() - round_start))
        logger.info(f"✅ 一轮完成: 重爬 {stats['songs_recrawl']} + 新歌 {stats['songs_new']} + 重复 {stats['songs_dup']} + 评论 {stats['comments_new']} (增量 {stats['comment_inc']}) 耗时 {duration}s")
        return stats

    def run_until_target(self, target: int = DAILY_TARGET, max_minutes: int = 240):
        """跑到目标数 ("100+ 首/天"用循环满足)"""
        self._reset_if_new_day()
        start = time.time()
        while self.today_count < target and (time.time() - start) < max_minutes * 60:
            stats = self.run_one_round()
            logger.info(f"📈 累计: {self.today_count}/{target}")
            if self.today_count >= target:
                logger.info(f"🎯 今日目标达成: {self.today_count} 首")
                break
            # 跑完一轮后再随机长间隔
            extra = random.uniform(60, 180)
            logger.info(f"⏰ 下一轮等待 {extra:.0f}s")
            time.sleep(extra)
        return self.today_count

    def extend_keywords_daily(self):
        """每日关键词扩展: 从已有歌曲名提取"""
        session = get_session()
        try:
            stmt = select(Song.name).limit(200)
            names = [r[0] for r in session.execute(stmt).all()]
        finally:
            session.close()
        if names:
            self.kw_pool.extend_from_songs(names, max_new=10)
