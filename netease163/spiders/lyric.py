"""
"""
from typing import Dict, Any
from .base import BaseSpider


class LyricSpider(BaseSpider):
    name = "lyric"

    def fetch(self, song_id: int) -> Dict[str, Any]:
        """
        获取歌曲歌词
        :param song_id: 网易云歌曲 ID (e.g. 1062642)
        :return: {"song_id": int, "lyric": str, "tlyric": str}
        """
        from pyncm.apis import track

        result = track.GetTrackLyrics(song_id)
        if result.get("code") != 200:
            self.logger.warning(f"⚠️ song_id={song_id} 返回 code={result.get('code')}")

        lrc = result.get("lrc") or {}
        return {
            "song_id": song_id,
            "lyric": lrc.get("lyric", ""),
            "tlyric": lrc.get("tlyric", ""),  # 翻译歌词
        }
