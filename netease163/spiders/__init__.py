"""
spiders 包 - 借鉴 163yinyue 12 个爬虫模块
基于 pyncm 库重写 (避免自维护加密/风控)
"""
from .base import BaseSpider
from .comment import CommentSpider
from .lyric import LyricSpider
from .search import SearchSpider
from .song import SongSpider
from .playlist import PlaylistSpider
from .artist import ArtistSpider
from .album import AlbumSpider
from .radio import RadioSpider
from .toplist import ToplistSpider

__all__ = [
    "BaseSpider",
    "CommentSpider",
    "LyricSpider",
    "SearchSpider",
    "SongSpider",
    "PlaylistSpider",
    "ArtistSpider",
    "AlbumSpider",
    "RadioSpider",
    "ToplistSpider",
]
