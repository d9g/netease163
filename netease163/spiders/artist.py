"""
ArtistSpider - 歌手详情 (借鉴 163yinyue singer.py + NetCloud get_singer_id_by_name)
"""
from typing import Dict, Any
from .base import BaseSpider


class ArtistSpider(BaseSpider):
    name = "artist"

    def fetch(self, artist_id: int) -> Dict[str, Any]:
        """
        获取歌手详情
        """
        from pyncm.apis import artist
        result = artist.GetArtistDetails(artist_id)
        if result.get("code") != 200:
            raise RuntimeError(f"API code={result.get('code')}")

        a = result.get("data", {}).get("artist") or result.get("artist") or {}
        if not a:
            raise ValueError(f"歌手 {artist_id} 不存在")

        return {
            "id": a["id"],
            "name": a["name"],
            "alias": a.get("alias", []),
            "pic_url": a.get("picUrl", ""),
            "mv_count": a.get("mvSize", 0),
            "album_count": a.get("albumSize", 0),
            "music_count": a.get("musicSize", 0),
            "brief_desc": a.get("briefDesc", ""),
        }
