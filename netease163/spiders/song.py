"""
"""
from typing import Dict, Any
from .base import BaseSpider


class SongSpider(BaseSpider):
    name = "song"

    def fetch(self, song_id: int) -> Dict[str, Any]:
        """
        获取歌曲详情
        :param song_id: 歌曲 ID
        """
        from pyncm.apis import track
        result = track.GetTrackDetail([song_id])
        if result.get("code") != 200:
            raise RuntimeError(f"API code={result.get('code')}")

        songs = result.get("songs", [])
        if not songs:
            raise ValueError(f"song_id={song_id} 不存在")

        s = songs[0]
        return {
            "id": s["id"],
            "name": s["name"],
            "artists": [{"id": a["id"], "name": a["name"]} for a in s.get("ar", [])],
            "album": {
                "id": s.get("al", {}).get("id"),
                "name": s.get("al", {}).get("name", ""),
                "pic_url": s.get("al", {}).get("picUrl", ""),
            },
            "duration_ms": s.get("dt", 0),
            "publish_time": s.get("publishTime", 0),
            "fee": s.get("fee", 0),  # 0=免费, 8=VIP
        }
