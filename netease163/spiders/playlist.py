"""
PlaylistSpider - 歌单详情 (借鉴 163yinyue song_sheet.py)
"""
from typing import Dict, Any, List
from .base import BaseSpider


class PlaylistSpider(BaseSpider):
    name = "playlist"

    def fetch(self, playlist_id: int) -> Dict[str, Any]:
        """
        获取歌单详情 (含歌曲列表)
        """
        from pyncm.apis import playlist
        result = playlist.GetPlaylistInfo(playlist_id)
        if result.get("code") != 200:
            raise RuntimeError(f"API code={result.get('code')}")

        p = result.get("playlist", {})
        if not p:
            raise ValueError(f"歌单 {playlist_id} 不存在")

        # 提取歌曲
        track_ids = p.get("trackIds", [])
        ids = [t["id"] for t in track_ids[:100]]  # 前 100 首

        tracks = []
        if ids:
            from pyncm.apis import track
            detail = track.GetTrackDetail(ids)
            if detail.get("code") == 200:
                tracks = [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "artists": [a["name"] for a in s.get("ar", [])],
                        "duration_ms": s.get("dt", 0),
                    }
                    for s in detail.get("songs", [])
                ]

        return {
            "id": p["id"],
            "name": p["name"],
            "creator": (p.get("creator") or {}).get("nickname", ""),
            "cover_url": p.get("coverImgUrl", ""),
            "description": p.get("description", ""),
            "track_count": p.get("trackCount", 0),
            "play_count": p.get("playCount", 0),
            "tracks": tracks,
        }
