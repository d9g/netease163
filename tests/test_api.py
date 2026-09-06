"""
FastAPI 测试

P2-11 重写: 9/6 审计发现多个失效用例, 全部修正或删除
- 之前测 /my/recommend /my/fm (路由已删, 必然挂) → 删
- login_phone_wrong_creds 用 URL query 传参 (接口已改 Body) → 改为 body
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


def test_toplist_hot(client):
    """热门榜单"""
    resp = client.get("/api/v1/toplist/hot?limit=10")
    assert resp.status_code == 200


def test_toplist_unknown(client):
    """未知榜单应该 400"""
    resp = client.get("/api/v1/toplist/nonexistent_name_xyz")
    assert resp.status_code == 400


def test_docs(client):
    """Swagger UI"""
    resp = client.get("/docs")
    assert resp.status_code == 200


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


def test_my_playlists_requires_login(client):
    """my/playlists 未登录应 401"""
    resp = client.get("/api/v1/my/playlists")
    assert resp.status_code == 401


def test_login_phone_wrong_creds(client):
    """错误账号密码应 200 + success=false (Pydantic Body 格式)"""
    resp = client.post(
        "/api/v1/login/phone",
        json={"account": "13800000000", "password": "wrong_password_xxxxxx"},
    )
    # 接口可能返 200 success=false, 也可能返 401 (网易云风控)
    # 但绝不应 500 (审计之前 422 因为传 query 不是 body)
    assert resp.status_code in (200, 401, 429)
    if resp.status_code == 200:
        data = resp.json()
        assert data.get("success") is False


def test_logout(client):
    """登出端点 (Pydantic Body 格式)"""
    resp = client.post("/api/v1/login/logout", json={})
    assert resp.status_code in (200, 401)


def test_random_crawl_now_requires_post(client):
    """P2-6 验证: GET /random/crawl/now 应 404 或 405 (仅 POST 注册)"""
    resp = client.get("/api/v1/random/crawl/now")
    assert resp.status_code in (404, 405)


def test_like_escape(client):
    """P2-4 验证: LIKE 关键词 % 转义生效 (不会全表扫描 36696 条)"""
    resp = client.get("/api/v1/comments/search?keyword=%25")
    assert resp.status_code == 200
    data = resp.json()
    # % 转义后只匹配字面 %, 实际评论数应远小于总评论数
    assert data["total"] < 100, f"P2-4 转义失败: % 返回 {data['total']} 条 (应 < 100)"
