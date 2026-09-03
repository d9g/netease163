"""
随机爬虫 - 选项 C (榜单 70% + 关键词 30% 混合)

设计:
- 每天目标 100+ 首 (老杨要求)
- 反爬: 随机间隔 8-25 秒
- 增量: 已入库的 song 跳过
- 持久化: songs + comments + crawl_logs

调度策略 (老杨需求):
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

logger = get_logger("netease163.random")

# ==================== 配置 ====================
MIN_INTERVAL = 8  # 最小间隔秒
MAX_INTERVAL = 25  # 最大间隔秒

# 每天目标
DAILY_TARGET = 100  # 老杨要求 100+ 首/天

# 每轮目标
ROUND_TOPLIST_SONGS = 6  # 每轮从榜单取 6 首
ROUND_KEYWORD_SONGS = 4  # 每轮从关键词取 4 首
ROUND_TARGET = ROUND_TOPLIST_SONGS + ROUND_KEYWORD_SONGS  # 10 首/轮

# 跑批轮次 (24h / 30min = 48 轮)
DAILY_ROUNDS = 48


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

    def _save_comments(self, song_id: int, comments: List[Dict]) -> int:
        """保存评论 (去重)"""
        if not comments:
            return 0
        session = get_session()
        saved = 0
        try:
            for c in comments:
                # pyncm 返回: comment_id / user.nickname / content / likedCount / time
                comment = Comment(
                    comment_id=c.get("comment_id", 0),
                    song_id=song_id,
                    user_nickname=c.get("user", c.get("user_nickname", "")),
                    content=c.get("content", ""),
                    liked_count=c.get("liked_count", c.get("likedCount", 0)),
                    comment_time=c.get("comment_time", c.get("time", 0)),
                    is_hot=1 if c.get("is_hot") else 0,
                )
                session.merge(comment)
                saved += 1
            session.commit()
            logger.info(f"💾 入库评论: song={song_id}, 新增 {saved} 条")
            return saved
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

    def _fetch_via_toplist(self, top_n: int = ROUND_TOPLIST_SONGS) -> List[Dict]:
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

    def _fetch_via_keyword(self, top_n: int = ROUND_KEYWORD_SONGS) -> List[Dict]:
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

    def _fetch_comments_for_song(self, song_id: int) -> int:
        """拉一首的评论"""
        try:
            t0 = time.time()
            result = self.comment_spider.fetch(song_id=song_id, limit=20)
            duration = int((time.time() - t0) * 1000)
            if result:
                comments = result.get("comments", []) + result.get("hot_comments", [])
                saved = self._save_comments(song_id, comments)
                self._log_crawl("CommentSpider", song_id, True, "", duration)
                return saved
        except Exception as e:
            logger.warning(f"⚠️  song={song_id} 评论拉取失败: {e}")
            self._log_crawl("CommentSpider", song_id, False, str(e))
        return 0

    def run_one_round(self) -> Dict[str, int]:
        """跑一轮: 6 榜单 + 4 关键词 = 10 首 + 评论"""
        self._reset_if_new_day()
        round_start = time.time()
        logger.info(f"🏃 跑一轮 (今天累计 {self.today_count}/{DAILY_TARGET})")

        stats = {"songs_new": 0, "songs_dup": 0, "comments_new": 0}

        # 1. 榜单抽 6 首
        tl_songs = self._fetch_via_toplist(top_n=ROUND_TOPLIST_SONGS)
        # 2. 关键词抽 4 首
        kw_songs = self._fetch_via_keyword(top_n=ROUND_KEYWORD_SONGS)

        all_songs = tl_songs + kw_songs
        logger.info(f"📦 本轮共 {len(all_songs)} 首待入库")

        # 3. 去重入库 + 拉评论
        for s in all_songs:
            song_id = s.get("id")
            if not song_id:
                continue
            if self._save_song(s):
                stats["songs_new"] += 1
                self.today_count += 1
            else:
                stats["songs_dup"] += 1
            # 每首歌都拉评论
            comments_saved = self._fetch_comments_for_song(song_id)
            stats["comments_new"] += comments_saved
            random_sleep()

        duration = int((time.time() - round_start))
        logger.info(f"✅ 一轮完成: 新入库 {stats['songs_new']}, 重复 {stats['songs_dup']}, 评论 {stats['comments_new']}, 耗时 {duration}s")
        return stats

    def run_until_target(self, target: int = DAILY_TARGET, max_minutes: int = 240):
        """跑到目标数 (老杨的"100+ 首/天"用循环满足)"""
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
