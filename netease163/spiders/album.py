"""
AlbumSpider - 专辑详情 (借鉴 NetCloud album 模块)
"""
from typing import Dict, Any, List
from .base import BaseSpider


class AlbumSpider(BaseSpider):
    name = "album"

    def fetch(self, album_id: int) -> Dict[str, Any]:
        """
        获取专辑详情 (含歌曲列表)
        """
        from pyncm.apis import album, track
        result = album.GetAlbumInfo(album_id)
        if result.get("code") != 200:
            raise RuntimeError(f"API code={result.get('code')}")

        a = result.get("album", {})
        songs_data = result.get("songs", [])

        tracks = [
            {
                "id": s["id"],
                "name": s["name"],
                "artists": [x["name"] for x in s.get("ar", [])],
                "duration_ms": s.get("dt", 0),
            }
            for s in songs_data
        ]

        return {
            "id": a["id"],
            "name": a["name"],
            "artists": [{"id": x["id"], "name": x["name"]} for x in a.get("artists", [])],
            "cover_url": a.get("picUrl", ""),
            "publish_time": a.get("publishTime", 0),
            "track_count": a.get("size", 0),
            "description": a.get("description", ""),
            "tracks": tracks,
        }
