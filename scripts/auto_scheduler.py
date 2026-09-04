"""
自动调度器 - 30 分钟轮询 500 评论 (9/4 老杨要求)

风控考虑:
- 30 分钟间隔 (避免请求过频)
- 每批 500 评论 = 20 批 (BATCH_SIZE=25)
- 单批间 sleep 0.5s 防 rate limit
- 总量 ~ 20 批 × 25 条 + 20 × 1s = ~20s 完成
- 全天 48 次 × 500 = 24000 评论/天
- 跟 100 首/天的爬虫不冲突 (评论分析是独立任务)

风控安全分析:
- 网易云 API 单 IP 限制约 1000 req/min (经验值)
- 一次 analyze_pending 约 21 次 API (20 批 + 1 prompt)
- 30 分钟间隔 = 0.7 req/min, 远低于限制
- 爬虫本身 100 首/天 = ~100 req/min 高峰, 但分布全天
- 总计安全阈值内
"""
import asyncio
import time
from datetime import datetime
from sqlalchemy import select, func

from netease163.storage.db import get_session
from netease163.storage.models import Comment
from netease163.ai import get_analyzer
from netease163.utils import get_logger
from netease163.api.cst_time import now_cst

logger = get_logger("netease163.scheduler")

# 配置
POLL_INTERVAL = 30 * 60  # 30 分钟
COMMENTS_PER_BATCH = 500  # 每次 500 评论
MIN_LIKED_DEFAULT = 0  # 全部 (按点赞数倒序, 优先评热门)


async def comment_analyze_scheduler():
    """30 分钟轮询: 分析 500 评论"""
    logger.info(f"🚀 启动评论分析调度器: 每 {POLL_INTERVAL // 60} 分钟跑 {COMMENTS_PER_BATCH} 条")
    analyzer = get_analyzer()
    while True:
        try:
            # 查未评分的评论数
            session = get_session()
            try:
                una = session.execute(
                    select(func.count(Comment.id)).where(Comment.ai_score == -1)
                ).scalar() or 0
                total = session.execute(select(func.count(Comment.id))).scalar() or 0
            finally:
                session.close()
            logger.info(f"⏰ {now_cst().strftime("%Y-%m-%d %H:%M")} 启动分析: 总 {total}, 未评分 {una}")
            if una == 0:
                logger.info("✅ 全部评论已分析, 等下一轮")
            else:
                # 分析 (限 500)
                stats = analyzer.analyze_pending(limit=COMMENTS_PER_BATCH, min_liked=MIN_LIKED_DEFAULT)
                logger.info(f"✅ 分析完成: {stats}")
        except Exception as e:
            logger.error(f"❌ 调度失败: {e}")
        # 等下一轮
        logger.info(f"⏳ 等待 {POLL_INTERVAL // 60} 分钟")
        await asyncio.sleep(POLL_INTERVAL)


def run_scheduler():
    """入口"""
    asyncio.run(comment_analyze_scheduler())


if __name__ == "__main__":
    run_scheduler()