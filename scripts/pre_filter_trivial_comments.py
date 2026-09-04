"""
程序预筛 - 一次性把口水评论 (字数<5 + 点赞<100) 标记为 0 星

逻辑 (老杨 15:44 反馈):
- 字数 < 5 字 (UTF-8 字符数) 且 点赞数 < 100 → 直接置 0 星
- 实测: 13,561 条评论, 2,055 条符合 → 100% 未评 (LLM 不会评)
- 预筛后: LLM 只评 9126 条 (13561 - 2055 - 1455 - 已评的 167/248/342/368/290/40)
- 减少 LLM 调用 ~15%

老杨原话: "字数少于5个字的, 点赞数少于100的, 99%都是口水评论. 通过程序把评论的评级直接置成0"
"""
import sys
from sqlalchemy import update, select, func
from netease163.storage.db import get_session
from netease163.storage.models import Comment
from netease163.ai.comment_analyzer import AI_SCORE_THRESHOLDS
from netease163.utils import get_logger
from netease163.api.cst_time import now_cst

logger = get_logger("netease163.pre_filter")

MIN_LEN = 5  # 最短字符数 (UTF-8 字符, 排除"顶""好听"等)
MIN_LIKED = 100  # 最低点赞 (排除 1-99 赞的口水)


def pre_filter_trivial():
    """一次性预筛: 把字数<5 且 赞<100 的评论置 0 星"""
    session = get_session()
    try:
        # 1. 统计总数
        from sqlalchemy import func
        total = session.execute(select(func.count(Comment.id))).scalar() or 0
        una = session.execute(
            select(func.count(Comment.id)).where(Comment.ai_score == -1)
        ).scalar() or 0

        # 2. 找出符合条件的未评评论
        # SQLite 里 length() 是字符数 (UTF-8 也算字符)
        stmt = (
            select(Comment.id, Comment.content, Comment.liked_count)
            .where(Comment.ai_score == -1)
            .where(func.length(Comment.content) < MIN_LEN)
            .where(Comment.liked_count < MIN_LIKED)
        )
        rows = session.execute(stmt).all()
        if not rows:
            logger.info("✅ 无评论符合预筛条件")
            return {"filtered": 0, "total": total, "una": una}

        # 3. 批量置 0 星
        ids = [r[0] for r in rows]
        now = now_cst()
        session.execute(
            update(Comment)
            .where(Comment.id.in_(ids))
            .values(
                ai_score=0,
                ai_label="程序预筛·口水",
                ai_reason=f"[program_pre_filter] 字数<{MIN_LEN}且赞<{MIN_LIKED}, 99% 为无意义评论",
                ai_analyzed_at=now,
            )
        )
        session.commit()

        logger.info(f"✅ 预筛 {len(ids)} 条 (字数<{MIN_LEN} + 赞<{MIN_LIKED}) → 置 0 星")
        logger.info(f"   总 {total} 条, 评前 {una} → 评后 {una - len(ids)} 条")
        return {"filtered": len(ids), "total": total, "una_before": una, "una_after": una - len(ids)}
    finally:
        session.close()


def main():
    logger.info(f"🚀 启动程序预筛 @ {now_cst()}")
    result = pre_filter_trivial()
    logger.info(f"📊 结果: {result}")


if __name__ == "__main__":
    main()