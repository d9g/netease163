"""
网易云官方榜单 ID (常用 6 个)
"""
from typing import Dict, Any, List
from .base import BaseSpider


# 网易云官方排行榜 ID 映射
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
                # P2-7 修复: 之前 track 缺 album / pic_url / duration_ms, 入库后封面空白专辑名为空
                # 现在补齐, 跟 SongSpider 返回结构对齐
                tracks = [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "artists": [a["name"] for a in s.get("ar", [])],  # 字符串数组 (ToplistSpider 风格)
                        "album_id": (s.get("al") or {}).get("id"),
                        "album_name": (s.get("al") or {}).get("name", ""),
                        "pic_url": (s.get("al") or {}).get("picUrl", ""),
                        "duration_ms": s.get("dt", 0),
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
    # P3-5 修复: 类属性命名像方法易误读, 但保留 get_toplist_ids 别名 (向后兼容)
    TOPLIST_IDS_ATTR = TOPLIST_IDS
    get_toplist_ids = TOPLIST_IDS  # 别名, 兼容现有调用
