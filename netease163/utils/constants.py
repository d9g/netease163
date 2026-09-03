"""
常量模块 - 借鉴 NetCloud Constants 类

集中管理 URL 模板 / 默认 headers / 存储路径
"""
import os

# User-Agent (借鉴 163yinyue setting.py)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 默认请求头 (借鉴 163yinyue setting.py)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": "https://music.163.com/",
    "Host": "music.163.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

# 数据目录 (借鉴 NetCloud USER_CONFIG_FILE_PATH)
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_ROOT, "netease163", "data")
LOGS_DIR = os.path.join(PROJECT_ROOT, "netease163", "logs")

# 默认数据库路径
DEFAULT_DB_URL = f"sqlite:///{DATA_DIR}/netease163.db"

# 网易云 API 模板 (借鉴 163yinyue setting.py)
SONG_LYRIC_URL = "http://music.163.com/api/song/lyric"
PLAYLIST_DETAIL_URL = "https://music.163.com/api/playlist/detail"
ARTIST_URL = "https://music.163.com/artist?id={artist_id}"
