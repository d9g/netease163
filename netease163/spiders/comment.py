"""
pyncm.apis 无内置, 用 WeapiCryptoRequest 自实现 (pyncm 加密已经处理好)
"""
from typing import Dict, Any, List
from .base import BaseSpider


class CommentSpider(BaseSpider):
    name = "comment"

    def fetch(self, song_id: int, limit: int = 20, offset: int = 0, hot_only: bool = False) -> Dict[str, Any]:
        """
        获取歌曲评论
        :param song_id: 网易云歌曲 ID
        :param limit: 单次返回条数
        :param offset: 分页偏移
        :param hot_only: 只取热门评论
        :return: {"song_id": int, "total": int, "hot_comments": List, "comments": List}
        """
        from pyncm import GetCurrentSession
        from pyncm.apis import WeapiEncrypt

        session = GetCurrentSession()
        payload = (
            f'{{"rid":"R_SO_4_{song_id}","offset":{offset},"total":"true",'
            f'"limit":{limit},"csrf_token":""}}'
        )
        resp = session.request(
            "POST",
            f"https://music.163.com/weapi/v1/resource/comments/R_SO_4_{song_id}",
            params={"csrf_token": ""},
            data={**WeapiEncrypt(payload)},
            headers={"Referer": "https://music.163.com"},
        )
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")

        data = resp.json()
        if data.get("code") != 200:
            raise RuntimeError(f"API code={data.get('code')}")

        # 提取评论
        comments = []
        for c in data.get("comments", []):
            comments.append({
                "id": c.get("commentId"),
                "user": c.get("user", {}).get("nickname", "匿名"),
                "content": c.get("content", ""),
                "liked_count": c.get("likedCount", 0),
                "time": c.get("time", 0),
            })

        hot_comments = []
        for c in data.get("hotComments", []):
            hot_comments.append({
                "id": c.get("commentId"),
                "user": c.get("user", {}).get("nickname", "匿名"),
                "content": c.get("content", ""),
                "liked_count": c.get("likedCount", 0),
                "time": c.get("time", 0),
            })

        return {
            "song_id": song_id,
            "total": data.get("total", 0),
            "is_musician": data.get("isMusician", False),
            "hot_comments": hot_comments,
            "comments": [] if hot_only else comments,
        }
