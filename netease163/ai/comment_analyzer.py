"""
评论质量分析器 - 用 LLM 识别评论级别 () 

设计:
- 批量 25 条/次 (平衡效率 vs 成本, 单次 ~ 4000 tokens)
- 0-5 星评分: 0-1=口水, 2-3=中等, 4-5=高质量
- 异步: 后台线程跑, 不阻塞爬虫
- 增量: 只分析未评分的评论 (ai_score = -1)
"""
import json
import time
import threading
from typing import List, Dict, Optional
from sqlalchemy import select
from ..storage.db import get_session
from ..storage.models import Comment
from ..utils import get_logger

logger = get_logger("netease163.ai")

# ==================== 配置 ====================
BATCH_SIZE = 25  # 每批 25 条评论 (单次 prompt ~ 4000 tokens)
AI_SCORE_THRESHOLDS = {
    "口水": (0, 1),
    "中等": (2, 3),
    "高质量": (4, 5),
}
PROMPT_TEMPLATE = """你是网易云音乐评论质量分析专家。请对以下 {n} 条评论按 0-5 星评分:

评分标准:
- 0 星: 完全口水 (单字/单表情/无意义反复, 如 "顶", "哈哈哈", "哥", "啊啊啊", "路过", "💗")
- 1 星: 基本口水 (口语化、无深度的感叹, 如 "好听", "支持", "喜欢", "哭了", "笑死")
- 2-3 星: 中等评论 (表达感受但无深度/故事, 如 "好听到哭", "想家了", "上头了")
- 4-5 星: 高质量评论 (有故事/有情感深度/有见解/有文采, ≥30字且言之有物)

请严格按 JSON 数组返回, 每条评论对应一个对象, **必须使用以下列表中的真实 comment_id** (不要重新编号!):
[{{"comment_id": {sample_ids}, "score": 4, "label": "高质量", "reason": "..."}}, ...]

评论列表 (id=comment_id, song_id=song_id):
{comments_json}

只返回 JSON 数组, 不要其他文字。"""


class CommentAnalyzer:
    """评论质量分析器"""

    def __init__(self, llm_caller=None):
        """
        Args:
            llm_caller: 可调用的 LLM 函数 async (prompt: str) -> str
                       如果 None, 用 OpenAI 兼容 API (从 .env 读)
        """
        self.llm_caller = llm_caller or self._default_llm_caller
        self.total_analyzed = 0
        self.total_cost_tokens = 0
        self.last_run_stats = {}

    def _default_llm_caller(self, prompt: str) -> str:
        """默认 LLM 调用: 优先 Anthropic 兼容 API, fallback OpenAI 兼容"""
        import os
        import httpx
        # 优先 Anthropic
        anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if anthropic_key:
            return self._call_anthropic(prompt, anthropic_key)
        # Fallback OpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            return self._call_openai(prompt, api_key)
        raise ValueError("未配置 ANTHROPIC_API_KEY 或 OPENAI_API_KEY")

    def _call_anthropic(self, prompt: str, api_key: str) -> str:
        """Anthropic 兼容 API (默认走 MiniMax minimax 兼容端点, 使用兼容协议)"""
        import os, httpx
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
        model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{base_url}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 16384,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self.total_cost_tokens += data.get("usage", {}).get("output_tokens", 0) * 5 + data.get("usage", {}).get("input_tokens", 0)
            return data["content"][0]["text"]

    def _call_openai(self, prompt: str, api_key: str) -> str:
        """OpenAI 兼容 API"""
        import httpx
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        with httpx.Client(timeout=60.0) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            self.total_cost_tokens += data.get("usage", {}).get("total_tokens", 0)
            return data["choices"][0]["message"]["content"]

    def _build_prompt(self, comments: List[Dict]) -> str:
        """构造 prompt (审计 #6 修复: 使用真实 comment_id 而非序号)"""
        items = [
            {"id": c["comment_id"], "song_id": c["song_id"], "content": c["content"][:300], "liked": c.get("liked_count", 0)}
            for c in comments
        ]
        # 给 LLM 示例 comment_id (从本批取前 3 个)
        sample_ids = ", ".join(str(c["comment_id"]) for c in comments[:3])
        return PROMPT_TEMPLATE.format(
            n=len(comments),
            sample_ids=sample_ids,
            comments_json=json.dumps(items, ensure_ascii=False, indent=2),
        )

    def _parse_response(self, response: str, n_expected: int) -> List[Dict]:
        """解析 LLM 返回的 JSON"""
        # 有时 LLM 返回带 ```json``` 包裹
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        try:
            result = json.loads(response)
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON 解析失败: {e}, response 前 200: {response[:200]}")
            return []
        if isinstance(result, dict) and "data" in result:
            result = result["data"]
        if not isinstance(result, list):
            logger.error(f"❌ 返回不是 list: {type(result)}")
            return []
        # 校验
        valid = []
        for item in result[:n_expected]:
            if isinstance(item, dict) and "score" in item:
                try:
                    score = max(0, min(5, int(item["score"])))
                    valid.append({
                        "score": score,
                        "label": item.get("label", self._score_to_label(score)),
                        "reason": str(item.get("reason", ""))[:200],
                    })
                except (ValueError, TypeError):
                    continue
        return valid

    def _score_to_label(self, score: int) -> str:
        """0-5 星转 label"""
        for label, (lo, hi) in AI_SCORE_THRESHOLDS.items():
            if lo <= score <= hi:
                return label
        return "中等"

    def analyze_batch(self, comments: List[Dict]) -> List[Dict]:
        """分析一批评论 (同步, 审计 #6 修复: 按 comment_id 匹配回写)

        Args:
            comments: [{"id": db_id, "comment_id": 网易云id, "song_id": ..., "content": "...", "liked_count": 0}, ...]
        Returns:
            [{"id": db_id, "score": 4, "label": "高质量", "reason": "..."}, ...]
        """
        if not comments:
            return []
        prompt = self._build_prompt(comments)
        try:
            response = self.llm_caller(prompt)
        except Exception as e:
            logger.error(f"❌ LLM 调用失败: {e}")
            return []
        results = self._parse_response(response, len(comments))

        # 按 comment_id 建字典 (审计 #6: 不再用位置 i 映射, 按真实 comment_id 匹配)
        comments_by_cid = {c["comment_id"]: c for c in comments if c.get("comment_id")}
        out = []
        matched = 0
        for r in results:
            cid = r.get("comment_id") or r.get("id")  # 兼容老 prompt
            if cid and cid in comments_by_cid:
                src = comments_by_cid[cid]
                out.append({
                    "id": src["id"],
                    "score": r["score"],
                    "label": r["label"],
                    "reason": r["reason"],
                })
                matched += 1
            else:
                logger.warning(f"⚠️ LLM 返回的 comment_id={cid} 不在批内, 丢弃")
        if matched < len(comments):
            logger.warning(f"⚠️ LLM 返回 {len(results)} 条, 匹配 {matched}/{len(comments)} 条")
        return out

    def analyze_pending(self, limit: int = 100, min_liked: int = 0) -> Dict:
        """分析未评分评论 (主入口)
        Args:
            limit: 最多分析多少条
            min_liked: 最低点赞数 (0 = 全部, 10 = 至少 10 赞)
        Returns:
            stats: {analyzed, batches, total_tokens}
        """
        from datetime import datetime
        from ..api.cst_time import now_cst
        session = get_session()
        try:
            # 找未评分的评论 (审计 #6 修复: 拉 comment_id + song_id 用于准确回写)
            stmt = (
                select(Comment.id, Comment.comment_id, Comment.song_id, Comment.content, Comment.liked_count)
                .where(Comment.ai_score == -1)
                .where(Comment.content.isnot(None))
                .where(Comment.content != "")
            )
            if min_liked > 0:
                stmt = stmt.where(Comment.liked_count >= min_liked)
            stmt = stmt.order_by(Comment.liked_count.desc()).limit(limit)
            rows = session.execute(stmt).all()
        finally:
            session.close()
        if not rows:
            return {"analyzed": 0, "batches": 0, "total_tokens": 0, "msg": "没有待评分的评论"}
        all_results = []
        n_batches = 0
        for i in range(0, len(rows), BATCH_SIZE):
            batch = [
                {"id": r[0], "comment_id": r[1], "song_id": r[2], "content": r[3], "liked_count": r[4] or 0}
                for r in rows[i:i + BATCH_SIZE]
            ]
            logger.info(f"🤖 分析批 {n_batches + 1}: {len(batch)} 条评论")
            results = self.analyze_batch(batch)
            all_results.extend(results)
            n_batches += 1
            # 防 rate limit (从调度器读 BATCH_SLEEP, 默认 0.3)
            sleep_sec = globals().get('BATCH_SLEEP', 0.3)
            time.sleep(sleep_sec)
        # 写库
        analyzed = 0
        session = get_session()
        try:
            now = now_cst()
            for r in all_results:
                c = session.get(Comment, r["id"])
                if c:
                    c.ai_score = r["score"]
                    c.ai_label = r["label"]
                    c.ai_reason = r["reason"]
                    c.ai_analyzed_at = now
                    analyzed += 1
            session.commit()
        except Exception as e:
            logger.error(f"❌ 写库失败: {e}")
            session.rollback()
        finally:
            session.close()
        self.total_analyzed += analyzed
        stats = {
            "analyzed": analyzed,
            "batches": n_batches,
            "total_tokens": self.total_cost_tokens,
            "comments_pool": len(rows),
        }
        self.last_run_stats = stats
        logger.info(f"✅ 分析完成: {analyzed} 条, {n_batches} 批, 累计 token {self.total_cost_tokens}")
        return stats

    def analyze_pending_async(self, limit: int = 100, min_liked: int = 0):
        """异步分析 (不阻塞)"""
        def _run():
            try:
                self.analyze_pending(limit=limit, min_liked=min_liked)
            except Exception as e:
                logger.error(f"❌ 异步分析失败: {e}")
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return {"started": True, "thread_alive": t.is_alive()}


# 单例
_analyzer_instance: Optional[CommentAnalyzer] = None


def get_analyzer() -> CommentAnalyzer:
    global _analyzer_instance
    if _analyzer_instance is None:
        _analyzer_instance = CommentAnalyzer()
    return _analyzer_instance
