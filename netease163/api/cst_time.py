"""
东八区时间工具 - 老杨 9:30 修 S6 UTC 误判后抽出

bidding-tool 已有同款: /root/bidding-tool/api/cst_time.py
netease163 项目也需要统一时间, 复制同款逻辑
"""
from datetime import datetime, timedelta, timezone

# 东八区 timezone
CST = timezone(timedelta(hours=8))


def now_cst() -> datetime:
    """返回东八区时间 (naive datetime, 跟 DB schema 一致)"""
    return datetime.now(CST).replace(tzinfo=None)


def today_cst() -> str:
    """返回东八区今天日期字符串 YYYY-MM-DD"""
    return now_cst().strftime("%Y-%m-%d")


def date_offset_cst(days: int = 0) -> str:
    """返回东八区 ±N 天日期字符串 YYYY-MM-DD"""
    return (now_cst() + timedelta(days=days)).strftime("%Y-%m-%d")