"""
pytest 公共配置

P2-11: 测试需独立于本地登录态 (避免 web 服务的 session 干扰)
跑测试前临时移走 .login_session.json, 跑完还原
"""
import shutil
from pathlib import Path

import pytest

SESSION_FILE = Path("/root/netease163/netease163/logs/.login_session.json")
SESSION_BACKUP = Path("/tmp/.login_session.json.test_bak")


@pytest.fixture(autouse=True)
def isolate_login_session():
    """每个测试前清空 session, 测试后还原"""
    if SESSION_FILE.exists():
        shutil.copy(SESSION_FILE, SESSION_BACKUP)
        SESSION_FILE.unlink()
    yield
    if SESSION_BACKUP.exists():
        shutil.copy(SESSION_BACKUP, SESSION_FILE)
        SESSION_BACKUP.unlink()
