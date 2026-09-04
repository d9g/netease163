"""
评论质量分析器 - 用 LLM 识别评论级别 (9/4 老杨要求)

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
- 0-1 星: 口水评论 (如 "顶", "好听", "好听", "支持", 单字/单表情)
- 2-3 星: 中等评论 (表达感受但无深度, 如 "好听到哭", "想家了")
- 4-5 星: 高质量评论 (有故事/有情感深度/有见解/有文采, ≥30字且言之有物)

请严格按 JSON 数组返回, 每条评论对应一个对象:
[{{"id": 1, "score": 4, "label": "高质量", "reason": "作者讲述母亲去世的真情实感"}}, ...]

评论列表:
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
        """默认 LLM 调用: 优先 Anthropic (跟 bidding-tool 一致), fallback OpenAI"""
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
        """Anthropic 兼容 API (默认走 MiniMax minimax 兼容端点, 跟 bidding 一致)"""
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
                    "max_tokens": 4096,
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
        """构造 prompt"""
        items = [
            {"id": i + 1, "content": c["content"][:300], "liked": c.get("liked_count", 0)}
            for i, c in enumerate(comments)
        ]
        return PROMPT_TEMPLATE.format(
            n=len(comments),
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
        """分析一批评论 (同步)
        Args:
            comments: [{"id": db_id, "content": "...", "liked_count": 0}, ...]
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
        # 合并
        out = []
        for i, r in enumerate(results):
            if i < len(comments):
                out.append({
                    "id": comments[i]["id"],
                    "score": r["score"],
                    "label": r["label"],
                    "reason": r["reason"],
                })
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
            # 找未评分的评论
            stmt = (
                select(Comment.id, Comment.content, Comment.liked_count)
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
                {"id": r[0], "content": r[1], "liked_count": r[2] or 0}
                for r in rows[i:i + BATCH_SIZE]
            ]
            logger.info(f"🤖 分析批 {n_batches + 1}: {len(batch)} 条评论")
            results = self.analyze_batch(batch)
            all_results.extend(results)
            n_batches += 1
            # 防 rate limit
            time.sleep(0.5)
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
