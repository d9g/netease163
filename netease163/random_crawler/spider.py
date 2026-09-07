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

# 2026-09-07 v2 升级: 自适应节奏 7 段 profile (跟时间做朋友, 更像人)
# 设计原则:
#   凌晨用户少 + 网易云 API 限流最严 → 间隔大 / 每轮少
#   早盘竞价 + 开盘 → 间隔小 / 每轮多 / 优先级提高
#   午休 + 午后 + 晚间 → 中等节奏
# 7 段 profile 按小时 (0-23) 映射到不同的 MIN/MAX 间隔 + 每轮入歌数
# 风控降速 (T4) 会动态调整当前 profile 的 MIN/MAX, 但 profile 表本身不变
TIME_PROFILES = {
    # (start_hour, end_hour_exclusive): {min_interval, max_interval, round_songs, name}
    (0, 7):   {"min": 15, "max": 35, "round": 2, "name": "deep_night"},
    (7, 10):  {"min": 12, "max": 28, "round": 3, "name": "pre_market"},
    (10, 12): {"min": 5,  "max": 15, "round": 4, "name": "morning_peak"},
    (12, 13): {"min": 10, "max": 22, "round": 3, "name": "noon"},
    (13, 15): {"min": 8,  "max": 18, "round": 3, "name": "afternoon"},
    (15, 24): {"min": 12, "max": 25, "round": 3, "name": "evening_night"},
}

# 间隔下限 (风控降速不会低于这个) (T4)
MIN_INTERVAL_FLOOR = 3
MAX_INTERVAL_FLOOR = 8

# 预留并发参数 (T1)
# 默认 1 = 串行, 保持安全
# 改 2 = 轻度并发, 风险中等, 风控降速需加严
# 改 3+ = 高度并发, 必须配 Redis/DB 锁防重复 (目前 fcntl.flock 不够)
# MAX_WORKERS=1 时 _fetch_workers_concurrent 函数被跳过, 改 >=2 才会调用
MAX_WORKERS = 1

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
ROUND_LLM_PRIORITY = 2  # 2026-09-07: LLM 优先级, 每轮优先 2 首 (T2)
ROUND_FULL_CRAWL_SONGS = 3  # 每轮全量爬 3 首未完成的歌
STALE_DAYS = 7  # 7 天前的歌优先重爬

# 2026-09-06 P3-4: _update_status_field 虚拟值常量 (避免魔法字符串)
NOW_CST_MARK = "now_cst"  # 表示调用 now_cst() 取当前时间
INC_MARK = "inc"  # 表示字段 +1

# 跑批轮次 (24h / 20min = 72 轮)
DAILY_ROUNDS = 72


def _get_current_profile():
    """按当前小时返回 TIME_PROFILES 配置 (T1)
    返回: {min, max, round, name}
    """
    from .spider import now_cst
    hour = now_cst().hour
    for (start, end), profile in TIME_PROFILES.items():
        if start <= hour < end:
            return profile
    # fallback (不可能走这里, TIME_PROFILES 覆盖 0-24)
    return {"min": 12, "max": 25, "round": 3, "name": "fallback"}


def random_sleep():
    """反爬: 随机间隔, 按当前 profile 动态选
    2026-09-07 v2: 不再读模块级 MIN_INTERVAL, 改读 TIME_PROFILES
    注: random_sleep 是模块级函数, 不访问实例, 所以风控调整后的 profile 不会反映
    实际生效在 _run_one_round_inner 里调用 self._sleep_with_health() (T4)
    """
    p = _get_current_profile()
    delay = random.uniform(p["min"], p["max"])
    logger.debug(f"⏳ 等待 {delay:.1f}s (profile={p['name']} [{p['min']}-{p['max']}s])")
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
        self.today_date = now_cst().date()

        # 2026-09-07 v2: 风控降速 计数器 (T4)
        self._failure_count = 0  # 连续 429/timeout 计数
        self._success_count = 0  # 连续 200 OK 计数
        self._last_profile_name = None  # profile 切换检测

    def _reset_if_new_day(self):
        """跨天重置计数"""
        today = now_cst().date()
        if today != self.today_date:
            logger.info(f"📅 跨天重置计数 (昨天 {self.today_count} 首)")
            self.today_count = 0
            self.today_date = today
            # 跨天重置风控计数器 (新一天从干净状态起)
            self._failure_count = 0
            self._success_count = 0

    def _get_current_profile(self) -> Dict:
        """返回当前小时的 profile dict (T1)
        包含调整后的 min/max (风控降速会乘系数)
        """
        hour = now_cst().hour
        for (start, end), profile in TIME_PROFILES.items():
            if start <= hour < end:
                # 检测 profile 切换, 重置风控计数器
                if self._last_profile_name != profile["name"]:
                    logger.info(f"🕐 切换到 profile: {profile['name']} (时段 {start}-{end}h, 间隔 {profile['min']}-{profile['max']}s)")
                    self._last_profile_name = profile["name"]
                    self._failure_count = 0
                    self._success_count = 0
                return profile
        return {"min": 12, "max": 25, "round": 3, "name": "fallback"}

    def _record_failure(self, err_type: str = "unknown"):
        """记录一次失败 (429/timeout) (T4)
        连续 3 次失败 → MIN/MAX 各乘 1.5 (但不低于 floor)
        """
        self._failure_count += 1
        self._success_count = 0
        if self._failure_count >= 3:
            # 调整当前 profile (不是 TIME_PROFILES 原值, 只影响本轮 + 后续轮次)
            old_profile = self._get_current_profile()
            if old_profile["min"] < MIN_INTERVAL_FLOOR * 2:
                logger.warning(f"⚠️  风控降速: 连续 {self._failure_count} 次失败 ({err_type}), profile {old_profile['name']} 间隔从 [{old_profile['min']}-{old_profile['max']}s] 放大")
                # 注: TIME_PROFILES 是只读常量, 调整值存在本实例的 _adjusted_profiles
                if not hasattr(self, '_adjusted_profiles'):
                    self._adjusted_profiles = {}
                key = old_profile["name"]
                adj = self._adjusted_profiles.get(key, {"min": old_profile["min"], "max": old_profile["max"]})
                adj["min"] = min(int(adj["min"] * 1.5), MIN_INTERVAL_FLOOR * 4)  # 上限 12s
                adj["max"] = min(int(adj["max"] * 1.3), MAX_INTERVAL_FLOOR * 4)  # 上限 32s
                self._adjusted_profiles[key] = adj
                self._failure_count = 0  # 重置, 避免连续累加

    def _record_success(self):
        """记录一次成功 (200 OK) (T4)
        连续 20 次成功 → 恢复间隔 × 0.95 (但不低于 TIME_PROFILES 原值)
        """
        self._success_count += 1
        if self._success_count >= 20:
            if hasattr(self, '_adjusted_profiles') and self._adjusted_profiles:
                old_profile = self._get_current_profile()
                key = old_profile["name"]
                if key in self._adjusted_profiles:
                    adj = self._adjusted_profiles[key]
                    # 获取原 profile 值 (不能低于)
                    for (start, end), prof in TIME_PROFILES.items():
                        if prof["name"] == key:
                            new_min = max(int(adj["min"] * 0.95), prof["min"])
                            new_max = max(int(adj["max"] * 0.95), prof["max"])
                            if new_min != adj["min"] or new_max != adj["max"]:
                                adj["min"] = new_min
                                adj["max"] = new_max
                                self._adjusted_profiles[key] = adj
                                logger.info(f"✅ 风控恢复: profile {key} 间隔 [{adj['min']}-{adj['max']}s]")
                            break
            self._success_count = 0

    def _fetch_workers_concurrent(self, items, fetch_fn, max_workers: int = None):
        """并发爬取骨架 (T1 预留, 默认不调用)
        用法: results = self._fetch_workers_concurrent(song_ids, lambda sid: self._fetch_comments_for_song(sid), max_workers=2)
        当前 MAX_WORKERS=1, 走串行
        改 MAX_WORKERS=2 时启用, 需注意:
          1. fcntl.flock 已加 (run_one_round 入口), 并发安全
          2. Comment UNIQUE 约束 (song_id, comment_id) 防重
          3. 风控降速 (T4) 需乘 2 倍谨慎系数
          4. DB 连接池需 >= max_workers (现 get_session() 每次新建, 问题不大但需监控)
        """
        workers = max_workers or MAX_WORKERS
        if workers <= 1:
            # 串行 fallback
            return [fetch_fn(item) for item in items]
        # 并发骨架 (未实现, 留给将来)
        from concurrent.futures import ThreadPoolExecutor
        logger.warning(f"⚠️  并发爬取未完整实现 (workers={workers}), 走串行")
        return [fetch_fn(item) for item in items]

    def _sleep_with_health(self):
        """实例版 random_sleep (T4)
        考虑风控降速后的 _adjusted_profiles
        与模块级 random_sleep() 区别: 读取 self._adjusted_profiles
        """
        p = self._get_current_profile()
        adj = getattr(self, '_adjusted_profiles', {}).get(p["name"], p)
        delay = random.uniform(adj["min"], adj["max"])
        logger.debug(f"⏳ 等待 {delay:.1f}s (profile={p['name']} [{adj['min']}-{adj['max']}s], health=fail={self._failure_count}/succ={self._success_count})")
        time.sleep(delay)

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

        P2-8 修复: 之前 order_by(desc(Song.id)) 用 song.id (网易云源 ID) 排序
        跟入库时间没关系, 优先级基本随机
        改为 order_by(Song.created_at) (入库时间早的优先重爬)
        """
        from datetime import datetime, timedelta
        from ..storage.models import SongCrawlStatus
        from sqlalchemy import and_, desc, asc
        cutoff = (now_cst() - timedelta(days=STALE_DAYS)).isoformat()
        session = get_session()
        try:
            # 找 7 天前爬过的歌, 按 created_at ASC (最久没重爬的优先)
            stmt = (
                select(Song.id, Song.name, Song.comment_total, SongCrawlStatus.last_crawled_at)
                .join(SongCrawlStatus, SongCrawlStatus.song_id == Song.id)
                .where(SongCrawlStatus.last_crawled_at < cutoff)
                .order_by(asc(Song.created_at))  # 入库时间最早的优先 (避免一直重复爬新歌)
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
        """跟时间做朋友: 选热门但 3 天没爬评论的歌
        T3 修复: 之前 order_by(desc(Song.id)) 用 song.id (网易云源 ID) 排序, 跟入库时间无关
        改为 order_by(asc(SongCrawlStatus.last_comment_crawled_at)), 优先重爬 3 天没动的歌
        """
        from datetime import datetime, timedelta
        from ..storage.models import SongCrawlStatus
        from sqlalchemy import and_, asc
        cutoff = (now_cst() - timedelta(days=3)).isoformat()
        session = get_session()
        try:
            # 3 天没爬评论的歌, 按 last_comment_crawled_at ASC (最久的优先)
            stmt = (
                select(Song.id, Song.name, Song.comment_total, SongCrawlStatus.last_comment_crawled_at)
                .outerjoin(
                    SongCrawlStatus,
                    and_(SongCrawlStatus.song_id == Song.id, SongCrawlStatus.last_comment_crawled_at >= cutoff)
                )
                .where(SongCrawlStatus.song_id.is_(None))
                .order_by(asc(SongCrawlStatus.last_comment_crawled_at))
                .limit(limit)
            )
            rows = session.execute(stmt).all()
            return [{"song_id": r[0], "name": r[1], "comment_count": r[2] or 0, "last_comment_crawled_at": r[3]} for r in rows]
        except Exception as e:
            logger.warning(f"⚠️  查 comment 目标失败: {e}")
            return []
        finally:
            session.close()

    def _get_llm_priority_targets(self, limit: int) -> List[Dict]:
        """T2: LLM 优先级重爬
        选满足门槛的歌 (ai_score >= 4 AND liked_count >= 50) 优先重爬评论
        设计: 不入新歌, 仅为已有"高价值"评论的歌补新评论 + 重评
        门槛来源: 9/7 设计
        """
        from ..storage.models import Comment
        from sqlalchemy import and_, desc, func
        session = get_session()
        try:
            # 选有"高价值"评论的歌, 按 max(ai_score) + sum(liked_count) 排
            stmt = (
                select(
                    Song.id, Song.name, Song.comment_total,
                    func.max(Comment.ai_score).label("max_ai"),
                    func.max(Comment.liked_count).label("max_liked"),
                    func.count(Comment.id).label("high_value_count"),
                )
                .join(Comment, Comment.song_id == Song.id)
                .where(and_(Comment.ai_score >= 4, Comment.liked_count >= 50))
                .group_by(Song.id)
                .order_by(desc("max_ai"), desc("max_liked"))
                .limit(limit)
            )
            rows = session.execute(stmt).all()
            results = [
                {
                    "song_id": r[0], "name": r[1], "comment_total": r[2] or 0,
                    "max_ai_score": r[3], "max_liked": r[4], "high_value_count": r[5],
                }
                for r in rows
            ]
            if results:
                logger.info(f"🎯 LLM 优先级: 选出 {len(results)} 首高价值歌 (ai>=4 AND liked>=50), 首首 max_ai={results[0]['max_ai_score']:.0f}/max_liked={results[0]['max_liked']}")
            return results
        except Exception as e:
            logger.warning(f"⚠️  查 LLM 优先级目标失败: {e}")
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
            now = now_cst()
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
                # 评论数不涨问题修复:
                # CommentSpider.fetch() 返回的 dict 用 "id" 字段, 但 ORM 用 "comment_id",
                # 导致 _save_comments 拿不到 comment_id → 全部 continue 跳过.
                # 根因修复: 加 "id" 兑底, 同时保留 "comment_id" 以防其他调用方依赖
                comment_id = c.get("comment_id") or c.get("id") or 0
                if not comment_id:
                    continue  # 没有 comment_id 的跳过
                # P1-5 修复: 用 INSERT ... ON CONFLICT DO UPDATE 代替先 SELECT 再 INSERT
                # 避免 scheduler 线程与 API 线程并发写同一首歌时丢数据
                # 或重复行 (老库可能补迁移后才唯一, 测了 uq_comments_song_comment 已存在)
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert
                comment_dict = {
                    "comment_id": comment_id,
                    "song_id": song_id,
                    "user_nickname": c.get("user", c.get("user_nickname", "")),
                    "content": c.get("content", ""),
                    "liked_count": c.get("liked_count", c.get("likedCount", 0)),
                    "comment_time": c.get("comment_time", c.get("time", 0)),
                    "is_hot": 1 if (c.get("is_hot") or is_hot) else 0,
                    "crawled_at": now_cst(),
                }
                stmt = sqlite_insert(Comment).values(comment_dict)
                # ON CONFLICT (song_id, comment_id) DO UPDATE SET content, user_nickname, liked_count, ...
                # ai_* 字段不覆盖 (保留上次评分)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["song_id", "comment_id"],
                    set_={
                        "content": stmt.excluded.content,
                        "user_nickname": stmt.excluded.user_nickname,
                        "liked_count": stmt.excluded.liked_count,
                        "comment_time": stmt.excluded.comment_time,
                        "is_hot": stmt.excluded.is_hot,
                        "crawled_at": stmt.excluded.crawled_at,
                    },
                )
                result = session.execute(stmt)
                if result.rowcount > 0:
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
        """拉一首的评论

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
            # 评论完成度 0/272 → total 未回写 + 增量模式不标完成
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
                    # 这一页空了 → 可能是网易云 API 限制
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
            if value == NOW_CST_MARK:
                value = now_cst()
            elif value == INC_MARK:
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
        # P1-8 修复: fcntl 在 Windows 不可用, try-import 降级为无锁
        from pathlib import Path
        try:
            import fcntl
            HAS_FCNTL = True
        except ImportError:
            HAS_FCNTL = False
            logger.warning("⚠️  fcntl 不可用 (Windows 平台), 跳过文件锁")
        lock_path = Path(__file__).parent.parent.parent / "data" / ".spider.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fp = open(lock_path, "w")
        try:
            if HAS_FCNTL:
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
        """实际跑一轮逻辑 (加锁后调用)
        2026-09-07 v2: 7 段 profile + LLM 优先级 + 风控降速 (T1/T2/T4)
        顺序: LLM 优先级 → 重爬 → 新歌 → 评论增量 → 全量补齐
        """
        self._reset_if_new_day()
        round_start = time.time()
        profile = self._get_current_profile()
        logger.info(f"🏃 跑一轮 (profile={profile['name']}, 间隔 {profile['min']}-{profile['max']}s) 今天累计 {self.today_count}/{DAILY_TARGET}")

        stats = {"songs_new": 0, "songs_recrawl": 0, "songs_dup": 0, "comments_new": 0, "comment_inc": 0, "llm_priority": 0}

        # 0. T2: LLM 优先级重爬 (2 首, 最高优先级)
        llm_targets = self._get_llm_priority_targets(limit=ROUND_LLM_PRIORITY)
        for t in llm_targets:
            song_id = t["song_id"]
            logger.info(f"🎯 LLM 优先级重爬: song={song_id} ({t['name']}) max_ai={t['max_ai_score']:.0f}")
            comments_saved = self._fetch_comments_for_song(song_id, full_crawl=False)
            stats["comment_inc"] += comments_saved
            stats["comments_new"] += comments_saved
            stats["llm_priority"] += 1
            self._mark_song_crawled(song_id, comment_crawled=True)
            self._record_success()  # 成功
            self._sleep_with_health()

        # 1. 重爬 5 首 (7 天前的歌, 跟时间做朋友)
        priority_targets = self._get_priority_targets(limit=ROUND_RE_SONGS)
        for t in priority_targets:
            song_id = t["song_id"]
            try:
                comments_saved = self._fetch_comments_for_song(song_id)
                self._record_success()
            except Exception as e:
                self._record_failure("priority")
                logger.warning(f"⚠️  重爬 {song_id} 失败: {e}")
                continue
            stats["comments_new"] += comments_saved
            self._mark_song_crawled(song_id, comment_crawled=True)
            stats["songs_recrawl"] += 1
            self._sleep_with_health()

        # 2. 新歌 3 首 (榜单/关键词)
        # 从 6 榜单 + 4 关键词里取 3 首 (50% 榜单 + 50% 关键词)
        try:
            tl_songs = self._fetch_via_toplist(top_n=2)
        except Exception as e:
            self._record_failure("toplist")
            tl_songs = []
            logger.warning(f"⚠️  榜单拉取失败: {e}")
        try:
            kw_songs = self._fetch_via_keyword(top_n=1)
        except Exception as e:
            self._record_failure("keyword")
            kw_songs = []
            logger.warning(f"⚠️  关键词拉取失败: {e}")
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
            try:
                comments_saved = self._fetch_comments_for_song(song_id)
                self._record_success()
            except Exception as e:
                self._record_failure("new_song_comment")
                logger.warning(f"⚠️  新歌 {song_id} 评论拉取失败: {e}")
                comments_saved = 0
            stats["comments_new"] += comments_saved
            self._sleep_with_health()

        # 3. 评论增量 2 首 (3 天没爬评论的热门歌, T3 排序已修)
        comment_targets = self._get_comment_targets(limit=ROUND_COMMENT_SONGS)
        for t in comment_targets:
            song_id = t["song_id"]
            try:
                comments_saved = self._fetch_comments_for_song(song_id)
                self._record_success()
            except Exception as e:
                self._record_failure("comment_inc")
                logger.warning(f"⚠️  评论增量 {song_id} 失败: {e}")
                continue
            stats["comment_inc"] += comments_saved
            stats["comments_new"] += comments_saved
            self._mark_song_crawled(song_id, comment_crawled=True)
            self._sleep_with_health()

        # 4. 全量爬 3 首未完成评论的歌
        #    断点续传: 从 last_comment_offset 爬到 comment_total
        #    防反爬: 一次轮 3 首 (每首 20 页 × 8-25s 间隔 = 160-500s, 总 8-25 分钟)
        uncompleted = self._get_uncompleted_songs(limit=ROUND_FULL_CRAWL_SONGS)
        for t in uncompleted:
            song_id = t["song_id"]
            logger.info(f"🎯 全量爬 song={song_id} (offset={t.get('last_comment_offset')}/{t.get('comment_total')}, {(t.get('last_comment_offset') or 0) * 100 // max(t.get('comment_total') or 1, 1)}%)")
            try:
                comments_saved = self._fetch_comments_for_song(song_id, full_crawl=True)
                self._record_success()
            except Exception as e:
                self._record_failure("full_crawl")
                logger.warning(f"⚠️  全量爬 {song_id} 失败: {e}")
                continue
            stats["comment_inc"] += comments_saved
            stats["comments_new"] += comments_saved
            self._mark_song_crawled(song_id, comment_crawled=True)
            self._sleep_with_health()

        duration = int((time.time() - round_start))
        logger.info(f"✅ 一轮完成: LLM优先 {stats['llm_priority']} + 重爬 {stats['songs_recrawl']} + 新歌 {stats['songs_new']} + 重复 {stats['songs_dup']} + 评论 {stats['comments_new']} (增量 {stats['comment_inc']}) 耗时 {duration}s")
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
