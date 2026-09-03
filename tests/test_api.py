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


# ==================== 登录态 API 测试 ====================

def test_login_status_unauthenticated(client):
    """未登录状态"""
    resp = client.get("/api/v1/login/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["logged_in"] is False
    assert data["user_id"] is None


def test_my_favorite_requires_login(client):
    """my/favorite 未登录应 401"""
    resp = client.get("/api/v1/my/favorite")
    assert resp.status_code == 401


def test_my_recommend_requires_login(client):
    """my/recommend 未登录应 401"""
    resp = client.get("/api/v1/my/recommend")
    assert resp.status_code == 401


def test_my_fm_requires_login(client):
    """my/fm 未登录应 401"""
    resp = client.get("/api/v1/my/fm")
    assert resp.status_code == 401


def test_my_playlists_requires_login(client):
    """my/playlists 未登录应 401"""
    resp = client.get("/api/v1/my/playlists")
    assert resp.status_code == 401


def test_login_phone_wrong_creds(client):
    """错误账号密码应返回 success=false"""
    resp = client.post("/api/v1/login/phone?account=13800000000&password=wrong_password_xxxxxx")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False


def test_logout(client):
    """登出端点"""
    resp = client.post("/api/v1/login/logout")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
