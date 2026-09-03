"""
基础测试 - 不依赖外网
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from netease163.utils import get_logger, USER_AGENT, get_db_url
from netease163.spiders import (
    LyricSpider, CommentSpider, SearchSpider, SongSpider,
    PlaylistSpider, ArtistSpider, AlbumSpider, RadioSpider, ToplistSpider,
)
from netease163.spiders.base import BaseSpider
from netease163.storage import init_db, Song as SongModel, Comment as CommentModel
from netease163.storage.models import Song as SongModel, Comment as CommentModel


def test_logger():
    """测试 logger 可用"""
    log = get_logger("test")
    assert log is not None
    assert log.name == "test"


def test_user_agent():
    """测试 UA"""
    assert "Mozilla" in USER_AGENT
    assert "Chrome" in USER_AGENT


def test_db_url_default():
    """测试默认 DB URL"""
    url = get_db_url()
    assert url.startswith("sqlite:")


def test_base_spider():
    """测试 BaseSpider 基类"""
    spider = BaseSpider()
    assert spider.name == "BaseSpider"
    with pytest.raises(NotImplementedError):
        spider.fetch()


def test_toplist_ids():
    """测试 toplist 映射"""
    assert ToplistSpider.get_toplist_ids["hot"] == 3778678
    assert ToplistSpider.get_toplist_ids["soar"] == 19723756
    assert len(ToplistSpider.get_toplist_ids) >= 4


def test_init_db(tmp_path, monkeypatch):
    """测试 init_db 不报错"""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    # 重置 engine cache
    import netease163.storage.db as db_mod
    db_mod._engine = None
    db_mod._SessionLocal = None
    init_db()
    assert (tmp_path / "test.db").exists()


def test_safe_fetch_handles_error():
    """测试 safe_fetch 处理错误不崩"""
    spider = LyricSpider()
    # 传无效 song_id 应该返回 None 不抛错
    result = spider.safe_fetch(song_id=-999999)
    # pyncm 可能返回 None 或抛错, 都应该被 safe_fetch 处理
    assert result is None or isinstance(result, dict)
