"""
Helper 工具类 - 借鉴 NetCloud Helper 设计

- get_logger() 统一 logger
- config() 加载 .env 配置
- get_db_url() 解析 DATABASE_URL (默认 SQLite)
"""
import os
import sys
import logging
from pathlib import Path
from typing import Optional


def get_logger(name: str = "netease163") -> logging.Logger:
    """
    借鉴 NetCloud Helper.get_logger()
    统一 logger 入口, 确保所有模块日志格式一致
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File handler (借鉴 NetCloud NetCloud.log)
    from .constants import LOGS_DIR
    Path(LOGS_DIR).mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(
        f"{LOGS_DIR}/netease163.log",
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    logger.propagate = False
    return logger


def config(key: str, default: Optional[str] = None) -> Optional[str]:
    """从环境变量 / .env 读配置"""
    val = os.getenv(key)
    if val is not None:
        return val
    return default


def get_db_url() -> str:
    """获取数据库 URL (DATABASE_URL 环境变量, 默认 SQLite)"""
    return os.getenv("DATABASE_URL") or "sqlite:///data/netease163.db"
