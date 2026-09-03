"""
FastAPI 测试
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from netease163.api.app import app


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    """健康检查"""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"


def test_root(client):
    """根路径"""
    resp = client.get("/")
    assert resp.status_code == 200


def test_toplist_list(client):
    """榜单列表"""
    resp = client.get("/api/v1/toplist")
    assert resp.status_code == 200
    data = resp.json()
    assert "toplists" in data
    assert "hot" in data["toplists"]
    assert "soar" in data["toplists"]


def test_lyric(client):
    """歌词端点"""
    resp = client.get("/api/v1/lyric/1062642")
    assert resp.status_code == 200
    data = resp.json()
    assert data["song_id"] == 1062642


def test_toplist_unknown(client):
    """未知榜单应该 400"""
    resp = client.get("/api/v1/toplist/nonexistent_name_xyz")
    assert resp.status_code == 400


def test_docs(client):
    """Swagger UI"""
    resp = client.get("/docs")
    assert resp.status_code == 200
