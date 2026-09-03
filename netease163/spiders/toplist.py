"""
ToplistSpider - 排行榜 (借鉴 163yinyue top_list.py)
网易云官方榜单 ID (常用 6 个)
"""
from typing import Dict, Any, List
from .base import BaseSpider


# 网易云官方排行榜 ID 映射 (来自 163yinyue top_list.py setting.py)
# 注: 网易云 2019 后改过榜单 ID, 现以 2026 实测为准
TOPLIST_IDS = {
    "hot": 3778678,         # 热歌榜 (2026 实测)
    "soar": 19723756,       # 飙升榜 (2026 实测)
    "new": 3779629,         # 新歌榜
    "original": 2884035,    # 原创榜
    "electronic": 10520166, # 电音榜
    "ACG": 71385702,        # ACG 榜
}


class ToplistSpider(BaseSpider):
    name = "toplist"

    def fetch(self, toplist_id: int, limit: int = 50) -> Dict[str, Any]:
        """
        获取排行榜歌曲 (实际是歌单)
        """
        from pyncm.apis import playlist
        result = playlist.GetPlaylistInfo(toplist_id)
        if result.get("code") != 200:
            raise RuntimeError(f"API code={result.get('code')}")

        p = result.get("playlist", {})
        track_ids = [t["id"] for t in p.get("trackIds", [])[:limit]]

        tracks = []
        if track_ids:
            from pyncm.apis import track
            detail = track.GetTrackDetail(track_ids)
            if detail.get("code") == 200:
                tracks = [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "artists": [a["name"] for a in s.get("ar", [])],
                        "score": p.get("trackIds", [])[i].get("score", 0) if i < len(p.get("trackIds", [])) else 0,
                    }
                    for i, s in enumerate(detail.get("songs", []))
                ]

        return {
            "toplist_id": toplist_id,
            "name": p.get("name", ""),
            "update_time": p.get("updateTime", 0),
            "tracks": tracks,
        }

    # 公开榜单 ID 映射供外部调用
    get_toplist_ids = TOPLIST_IDS
