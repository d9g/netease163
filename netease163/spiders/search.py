"""
SearchSpider - 搜索 (借鉴 163yinyue search.py)
支持搜索: 歌曲/歌手/歌单/专辑
"""
from typing import Dict, Any, List
from .base import BaseSpider


class SearchSpider(BaseSpider):
    name = "search"

    # 网易云 search type 映射
    TYPE_MAP = {
        "song": 1,        # 单曲
        "artist": 100,    # 歌手
        "album": 10,      # 专辑
        "playlist": 1000, # 歌单
        "user": 1002,     # 用户
    }

    def fetch(self, keyword: str, search_type: str = "song", limit: int = 20) -> Dict[str, Any]:
        """
        搜索
        :param keyword: 关键词
        :param search_type: song/artist/album/playlist/user
        :param limit: 返回条数
        """
        if search_type not in self.TYPE_MAP:
            raise ValueError(f"search_type 必须为 {list(self.TYPE_MAP.keys())}")

        from pyncm.apis import cloudsearch
        result = cloudsearch.GetSearchResult(keyword, stype=self.TYPE_MAP[search_type], limit=limit)
        if result.get("code") != 200:
            raise RuntimeError(f"API code={result.get('code')}")

        results = result.get("result", {})

        if search_type == "song":
            items = [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "artists": [a["name"] for a in s.get("ar", [])],
                    "album": s.get("al", {}).get("name", ""),
                    "duration": s.get("dt", 0),
                }
                for s in results.get("songs", [])
            ]
        elif search_type == "artist":
            items = [
                {"id": a["id"], "name": a["name"], "alias": a.get("alias", [])}
                for a in results.get("artists", [])
            ]
        elif search_type == "album":
            items = [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "artists": [x["name"] for x in a.get("artists", [])],
                    "publish_time": a.get("publishTime", 0),
                }
                for a in results.get("albums", [])
            ]
        elif search_type == "playlist":
            items = [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "creator": p.get("creator", {}).get("nickname", ""),
                    "track_count": p.get("trackCount", 0),
                }
                for p in results.get("playlists", [])
            ]
        else:
            items = []

        return {
            "keyword": keyword,
            "type": search_type,
            "count": len(items),
            "items": items,
        }
