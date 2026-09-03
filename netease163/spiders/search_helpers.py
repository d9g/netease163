"""
search_helpers - 按名称搜各 entity, 返回候选列表
按名称搜各 entity 通用 helper

type 映射 (跟 SearchSpider.TYPE_MAP 一致):
- song: 单曲
- artist: 歌手
- album: 专辑
- playlist: 歌单
"""
from typing import Dict, Any, List
from .search import SearchSpider


def search_by_name(name: str, search_type: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    按名称搜索, 返回候选列表 (前端用于重名筛选)

    返回格式 (统一):
    [
        {"id": 123, "name": "歌曲名", "subtitle": "歌手", "extra": "专辑", "duration": 225013},
        ...
    ]
    """
    spider = SearchSpider()
    result = spider.safe_fetch(name, search_type=search_type, limit=limit)
    if not result:
        return []

    items = result.get("items", [])
    candidates = []
    for it in items:
        if search_type == "song":
            artists = it.get("artists", [])
            artist_names = [a.get("name", "") if isinstance(a, dict) else a for a in artists]
            candidates.append({
                "id": it.get("id"),
                "name": it.get("name", ""),
                "subtitle": " · ".join([n for n in artist_names if n]),
                "extra": it.get("album", ""),
                "duration": it.get("duration", 0),
            })
        elif search_type == "artist":
            candidates.append({
                "id": it.get("id"),
                "name": it.get("name", ""),
                "subtitle": " · ".join(it.get("alias", []) or []),
                "extra": "",
                "duration": 0,
            })
        elif search_type == "album":
            artists = it.get("artists", [])
            artist_names = [a.get("name", "") if isinstance(a, dict) else a for a in artists]
            candidates.append({
                "id": it.get("id"),
                "name": it.get("name", ""),
                "subtitle": " · ".join([n for n in artist_names if n]),
                "extra": "",
                "duration": 0,
            })
        elif search_type == "playlist":
            candidates.append({
                "id": it.get("id"),
                "name": it.get("name", ""),
                "subtitle": f"by {it.get('creator', '')}",
                "extra": f"{it.get('track_count', 0)} 首",
                "duration": 0,
            })
        else:
            candidates.append({
                "id": it.get("id"),
                "name": it.get("name", ""),
                "subtitle": "",
                "extra": "",
                "duration": 0,
            })
    return candidates