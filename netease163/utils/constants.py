"""

集中管理 URL 模板 / 默认 headers / 存储路径
"""
import os

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://music.163.com/",
    "Host": "music.163.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_ROOT, "netease163", "data")
LOGS_DIR = os.path.join(PROJECT_ROOT, "netease163", "logs")

# 默认数据库路径
DEFAULT_DB_URL = f"sqlite:///{DATA_DIR}/netease163.db"

SONG_LYRIC_URL = "http://music.163.com/api/song/lyric"
PLAYLIST_DETAIL_URL = "https://music.163.com/api/playlist/detail"
ARTIST_URL = "https://music.163.com/artist?id={artist_id}"
