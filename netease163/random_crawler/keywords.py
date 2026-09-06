"""
关键词池 - 随机爬取用

设计:
- 内置 60+ 初始关键词（人名/曲风/年代/情绪）
- 每天从已有 songs.name / comments.content 提取新词扩展
- 持久化在 SQLite 表 keyword_pool (auto-managed)
"""
import random
from typing import List
from sqlalchemy import select
from ..storage.db import get_session
from ..storage.models import SearchLog
from ..utils import get_logger

logger = get_logger("netease163.keywords")

# ==================== 初始关键词池 (60+) ====================
INITIAL_KEYWORDS = [
    # ============== 经典歌手 ==============
    "林俊杰", "周杰伦", "陈奕迅", "薛之谦", "张学友", "刘德华", "邓紫棋", "张靓颖",
    "李宇春", "王菲", "那英", "莫文蔚", "梁静茹", "孙燕姿", "蔡依林", "田馥甄",
    "五月天", "苏打绿", "信乐团", "F.I.R.", "SHE", "蔡健雅", "杨丞琳", "罗大佑",
    "李宗盛", "朴树", "许巍", "汪峰", "崔健", "窦唯", "Beyond", "陈奕迅",

    # ============== 流量艺人 ==============
    "华晨宇", "毛不易", "周深", "张杰", "黄绮珊", "韩红", "邓伦", "王源",
    "易烊千玺", "肖战", "王一博", "蔡徐坤", "刘雨昕", "虞书欣", "鞠婧祎", "刘耀文",

    # ============== 曲风 ==============
    "民谣", "摇滚", "电子", "古风", "R&B", "嘻哈", "爵士", "蓝调",
    "新世纪", "ACG", "纯音乐", "轻音乐", "后摇", "金属", "朋克", "雷鬼",

    # ============== 年代 ==============
    "80后", "90后", "千禧年", "怀旧金曲", "经典老歌", "童年回忆", "学生时代", "青春",

    # ============== 情绪/场景 ==============
    "治愈", "伤感", "励志", "失恋", "告白", "婚礼", "毕业", "深夜",
    "下雨天", "一个人", "想家", "思念", "孤独", "快乐", "旅行", "运动",

    # ============== 热词 (扩展性) ==============
    "国风", "戏腔", "古诗词", "网红", "BGM", "抖音热歌", "影视原声", "综艺",
]


class KeywordPool:
    """关键词池管理器"""

    def __init__(self):
        # 内存池: 启动时从 DB 加载 + 合并初始池
        self._pool: List[str] = []
        self._load_from_db()

    def _load_from_db(self):
        """从 DB 加载已用过的关键词 + 合并初始池

        P2-5 修复: 过滤掉已软删除的关键词 (deleted_at IS NOT NULL)
        """
        try:
            session = get_session()
            try:
                # 拿最近 30 天搜索过的关键词作为扩展源, 排除被删的
                stmt = select(SearchLog.keyword).distinct().where(SearchLog.deleted_at.is_(None)).limit(100)
                rows = session.execute(stmt).all()
                used_keywords = [r[0] for r in rows]
                # 合并: 初始池 + DB 中已用 (去重)
                self._pool = list(set(INITIAL_KEYWORDS) | set(used_keywords))
                logger.info(f"📚 关键词池加载: {len(self._pool)} 个 (初始 {len(INITIAL_KEYWORDS)} + DB {len(used_keywords)})")
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"⚠️  从 DB 加载关键词失败, 用初始池: {e}")
            self._pool = list(INITIAL_KEYWORDS)

    def get_random(self, n: int = 5) -> List[str]:
        """随机抽 n 个不重复的关键词"""
        if n > len(self._pool):
            n = len(self._pool)
        return random.sample(self._pool, n)

    def get_all(self) -> List[str]:
        return list(self._pool)

    def add(self, keyword: str):
        """扩展关键词池 (启动时初始化 + 每日扩展调用)"""
        keyword = keyword.strip()
        if keyword and keyword not in self._pool and len(keyword) <= 20:
            self._pool.append(keyword)
            logger.info(f"➕ 关键词扩展: {keyword} (池大小 {len(self._pool)})")

    def extend_from_songs(self, song_names: List[str], max_new: int = 10):
        """从歌曲名提取新关键词扩展池 (随机筛一些短词)"""
        import re
        new_words = []
        for name in song_names:
            # 提取 2-4 字的中文词 (粗筛)
            words = re.findall(r"[\u4e00-\u9fa5]{2,4}", name)
            new_words.extend(words)
        random.shuffle(new_words)
        for word in new_words[:max_new * 2]:
            self.add(word)
            if len(self._pool) - len(INITIAL_KEYWORDS) > 100:  # 限上限
                break
        logger.info(f"📈 关键词扩展完成: {len(self._pool)} (DB 扩展 {len(new_words[:max_new])})")


# 单例
_pool_instance: KeywordPool = None


def get_keyword_pool() -> KeywordPool:
    global _pool_instance
    if _pool_instance is None:
        _pool_instance = KeywordPool()
    return _pool_instance
