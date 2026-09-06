#!/usr/bin/env python3
"""
backfill_comment_total.py - 修复 9/6 完成度 0/272 bug

真根因: 9/5 14:51 断点续传只更新了 last_comment_offset, 没回写 comment_total
导致已完成爬取的歌曲 comment_total=0, 永远无法触发 current_offset >= comment_total 完成条件

修法: 一次性 backfill - 对所有 last_offset > 0 AND comment_total = 0 的歌曲
       用 API 拉一次首页拿 total 写回 DB
       已实际爬到 N 条, 理论 total >= N
"""
import sys
import time
sys.path.insert(0, '/root/netease163')

from sqlalchemy import select, and_, or_
from sqlalchemy.orm import Session
from netease163.storage.db import get_session
from netease163.storage.models import Song, SongCrawlStatus
from netease163.spiders.comment import CommentSpider
from netease163.api.cst_time import now_cst
from netease163.utils import get_logger

logger = get_logger("netease163.backfill")


def main():
    session = get_session()
    try:
        # 找需要 backfill 的歌曲:
        # 2026-09-06 真根因: 86 首 offset=0 但 total>0 (首爬已记录 total 但未增量)
        # 这 86 首实际从未增量, 但 total 已知, 应该标完成
        # 同时 5 首 last_offset > 0 但 total=0 的也要 backfill total
        stmt = (
            select(SongCrawlStatus.song_id, Song.name, SongCrawlStatus.last_comment_offset,
                   SongCrawlStatus.comment_total, SongCrawlStatus.comments_completed)
            .join(Song, Song.id == SongCrawlStatus.song_id)
            .where(SongCrawlStatus.comments_completed == 0)
            .where(
                or_(
                    # 情况 1: offset > 0 但 total=0 (需要回填 total)
                    and_(
                        SongCrawlStatus.last_comment_offset > 0,
                        or_(SongCrawlStatus.comment_total == 0,
                            SongCrawlStatus.comment_total.is_(None))
                    ),
                    # 情况 2: offset=0 但 total>0 (首爬已记录, 但从未被 spider 增量, total 已知应标完成)
                    and_(
                        SongCrawlStatus.last_comment_offset == 0,
                        SongCrawlStatus.comment_total > 0
                    ),
                )
            )
            .limit(300)
        )
        targets = session.execute(stmt).all()
        logger.info(f"📊 待 backfill: {len(targets)} 首 (last_offset > 0 但 total=0)")

        if not targets:
            return

        spider = CommentSpider()
        fixed = 0
        completed_now = 0
        for i, (song_id, name, last_offset, old_total, completed) in enumerate(targets, 1):
            try:
                # 情况 2 (offset=0, total>0): DB 已有 total, 不需要 API, 直接看评论数判断
                if last_offset == 0 and (old_total or 0) > 0:
                    # 拉首页看实际评论数
                    result = spider.fetch(song_id=song_id, limit=100, offset=0)
                    if not result:
                        logger.warning(f"  [{i}/{len(targets)}] song={song_id} {name} - API 无返回")
                        continue
                    page_size = len(result.get("comments", []))
                    api_total = result.get("total", 0)
                    # 如果 api_total 与 old_total 不同, 更新
                    if api_total != old_total:
                        scs = session.execute(
                            select(SongCrawlStatus).where(SongCrawlStatus.song_id == song_id)
                        ).scalar_one_or_none()
                        if scs:
                            scs.comment_total = api_total
                            session.commit()
                    # 关键判定: api_total < PAGE_SIZE (100) → 全部已在前 100 条内 → 标完成
                    # 或者 api_total <= page_size → 全部已爬完
                    if api_total <= page_size:
                        scs = session.execute(
                            select(SongCrawlStatus).where(SongCrawlStatus.song_id == song_id)
                        ).scalar_one_or_none()
                        if scs:
                            scs.comments_completed = 1
                            scs.comments_completed_at = now_cst()
                            scs.last_comment_offset = api_total
                            session.commit()
                            completed_now += 1
                            logger.info(f"  [{i}/{len(targets)}] song={song_id} {name} - "
                                        f"total={api_total}, page_size={page_size}, 全部在前 1 页内, 标完成 ✅")
                    else:
                        # total > 100 说明需要多页, 留着增量
                        logger.info(f"  [{i}/{len(targets)}] song={song_id} {name} - "
                                    f"total={api_total}, page_size={page_size}, 需要 {api_total // 100 + 1} 页, 不标完成")
                    time.sleep(0.3)
                    continue

                # 情况 1 (offset > 0, total=0): 必须 API 重新拉首页
                result = spider.fetch(song_id=song_id, limit=1, offset=0)
                if not result:
                    logger.warning(f"  [{i}/{len(targets)}] song={song_id} {name} - API 无返回")
                    continue
                api_total = result.get("total", 0)
                if api_total == 0:
                    # 没评论的歌, 标完成
                    scs = session.execute(
                        select(SongCrawlStatus).where(SongCrawlStatus.song_id == song_id)
                    ).scalar_one_or_none()
                    if scs:
                        scs.comments_completed = 1
                        scs.comments_completed_at = now_cst()
                        scs.comment_total = 0
                        session.commit()
                        completed_now += 1
                        logger.info(f"  [{i}/{len(targets)}] song={song_id} {name} - 无评论, 标完成")
                    continue
                # 写回 total
                scs = session.execute(
                    select(SongCrawlStatus).where(SongCrawlStatus.song_id == song_id)
                ).scalar_one_or_none()
                if scs:
                    scs.comment_total = api_total
                    # 如果 offset 已经 >= total, 直接标完成
                    if last_offset >= api_total:
                        scs.comments_completed = 1
                        scs.comments_completed_at = now_cst()
                        completed_now += 1
                        logger.info(f"  [{i}/{len(targets)}] song={song_id} {name} - "
                                    f"offset={last_offset} >= total={api_total}, 标完成 ✅")
                    else:
                        logger.info(f"  [{i}/{len(targets)}] song={song_id} {name} - "
                                    f"total={api_total} (offset={last_offset}, 还差 {api_total - last_offset})")
                    session.commit()
                    fixed += 1
                # 限速
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f"  [{i}/{len(targets)}] song={song_id} {name} - 失败: {e}")
                session.rollback()
        logger.info(f"🎉 完成: 修复 total={fixed}, 新标完成={completed_now}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
