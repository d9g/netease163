"""
调度器 - 用 APScheduler 集成到 FastAPI 服务

设计:
- 在 FastAPI lifespan 启动时一起启动
- 主调度: 每 30 分钟跑一轮 RandomCrawler.run_one_round()
- 凌晨任务: 02:00 跑关键词扩展 + 全量评论增量
- 跑批 cron 兜底: systemd netease163.service 挂了还有 OpenClaw cron 接管
"""
import threading
import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from .spider import RandomCrawler, DAILY_TARGET
from ..utils import get_logger

logger = get_logger("netease163.scheduler")

_scheduler_instance: BackgroundScheduler = None
_crawler_instance: RandomCrawler = None


def get_crawler() -> RandomCrawler:
    global _crawler_instance
    if _crawler_instance is None:
        _crawler_instance = RandomCrawler()
    return _crawler_instance


def job_run_round():
    """每 30 分钟跑一轮"""
    try:
        logger.info("⏰ cron 触发: 跑一轮随机爬取")
        stats = get_crawler().run_one_round()
        logger.info(f"⏰ cron 完成: {stats}")
    except Exception as e:
        logger.error(f"❌ 跑轮失败: {e}")


def job_extend_keywords():
    """每日 02:00 扩展关键词池"""
    try:
        logger.info("⏰ cron 触发: 每日关键词扩展")
        get_crawler().extend_keywords_daily()
        logger.info("⏰ 关键词扩展完成")
    except Exception as e:
        logger.error(f"❌ 扩展失败: {e}")


def job_daily_summary():
    """每日 23:50 统计当天爬了多少"""
    try:
        c = get_crawler()
        logger.info(f"📊 今日累计: {c.today_count} 首 (目标 {DAILY_TARGET})")
    except Exception as e:
        logger.error(f"❌ 统计失败: {e}")


def get_scheduler() -> BackgroundScheduler:
    """单例 scheduler"""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = BackgroundScheduler(timezone="Asia/Shanghai")
        # 每 20 分钟跑一轮 (每天 72 轮)
        _scheduler_instance.add_job(
            job_run_round,
            CronTrigger.from_crontab("*/20 * * * *"),
            id="run_round",
            name="随机爬取 - 每 20 分钟",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        # 每日 02:00 关键词扩展
        _scheduler_instance.add_job(
            job_extend_keywords,
            CronTrigger.from_crontab("0 2 * * *"),
            id="extend_keywords",
            name="关键词扩展 - 每日 02:00",
            replace_existing=True,
        )
        # 每日 23:50 日报
        _scheduler_instance.add_job(
            job_daily_summary,
            CronTrigger.from_crontab("50 23 * * *"),
            id="daily_summary",
            name="日报 - 每日 23:50",
            replace_existing=True,
        )
        logger.info("📅 调度器已配置: 3 个 cron 任务")
    return _scheduler_instance


def start_scheduler_in_thread() -> BackgroundScheduler:
    """在独立线程启动 scheduler (供 FastAPI lifespan 用)"""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("✅ Scheduler 启动成功")
    return scheduler


def run_scheduler_standalone():
    """独立运行模式 (CLI 用)"""
    logger.info("🚀 独立模式启动 scheduler")
    scheduler = start_scheduler_in_thread()
    logger.info("按 Ctrl+C 退出...")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("👋 收到 Ctrl+C, 退出")
        scheduler.shutdown(wait=False)
