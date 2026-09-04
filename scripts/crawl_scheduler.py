"""
爬虫调度器 - 每天跑 100 首 (每天爬取分配)

设计:
- 90 分钟一轮 = 16 轮/天
- 每轮 ~7 首 (100 首/天)
- 配合跟时间做朋友 (5重爬+3新歌+2评论)
"""
import asyncio
import time
from netease163.random_crawler.spider import RandomCrawler
from netease163.utils import get_logger
from netease163.api.cst_time import now_cst

logger = get_logger("netease163.crawl_scheduler")

# 配置
RUN_INTERVAL = 90 * 60  # 90 分钟一轮 = 每天 16 轮
TARGET_PER_RUN = 7  # 每轮 ~7 首 (100 首/天)


async def crawl_scheduler():
    """90 分钟轮询: 爬 7 首"""
    logger.info(f"🚀 启动爬虫调度器: 每 {RUN_INTERVAL // 60} 分钟跑 {TARGET_PER_RUN} 首")
    crawler = RandomCrawler()
    while True:
        try:
            logger.info(f"⏰ {now_cst().strftime("%Y-%m-%d %H:%M")} 启动爬虫")
            # 用 to_thread 包同步函数
            def _crawl():
                return crawler.run_until_target(
                    target=TARGET_PER_RUN, max_minutes=5
                )
            count = await asyncio.get_event_loop().run_in_executor(None, _crawl)
            logger.info(f"✅ 爬虫完成: 累计 {count} 首")
        except Exception as e:
            logger.error(f"❌ 爬虫失败: {e}")
        await asyncio.sleep(RUN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(crawl_scheduler())