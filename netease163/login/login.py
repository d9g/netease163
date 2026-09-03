"""
LoginManager - 借鉴 NetCloud NetCloudLogin 类 (简化版)

NetCloud 原版 947 行, 这里只保留核心:
- 手机/邮箱密码登录
- 二维码登录 (pyncm 内置)
- 登录态 session 管理

底层用 pyncm.apis.login 实现 (pyncm 已经处理好加密)
"""
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
from ..utils import get_logger, LOGS_DIR

logger = get_logger("netease163.login")


class LoginManager:
    """
    登录管理器 - 简化 NetCloud NetCloudLogin
    - 支持手机密码 (LoginViaCellphone)
    - 支持邮箱密码 (LoginViaEmail)
    - 支持二维码 (GetQRCodeLoginState)
    - Session 状态保存到 .login_session.json (持久化)
    """

    SESSION_FILE = Path(LOGS_DIR) / ".login_session.json"

    def __init__(self):
        self.is_logged_in = False
        self.user_id: Optional[int] = None
        self.nickname: Optional[str] = None
        self._load_session()

    def _load_session(self):
        """从本地文件加载登录态 (借鉴 NetCloud 配置文件)"""
        if self.SESSION_FILE.exists():
            try:
                data = json.loads(self.SESSION_FILE.read_text(encoding="utf-8"))
                self.user_id = data.get("user_id")
                self.nickname = data.get("nickname")
                # 设置到 pyncm session
                from pyncm import SetCurrentSession, LoadSessionFromString
                cookie = data.get("cookie", "")
                if cookie:
                    session = LoadSessionFromString(cookie)
                    SetCurrentSession(session)
                    self.is_logged_in = True
                    logger.info(f"✅ 加载本地登录态: {self.nickname} (uid={self.user_id})")
            except Exception as e:
                logger.warning(f"⚠️ 加载登录态失败: {e}")

    def _save_session(self):
        """保存登录态"""
        from pyncm import GetCurrentSession, DumpSessionAsString
        try:
            cookie = DumpSessionAsString(GetCurrentSession())
            self.SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
            self.SESSION_FILE.write_text(
                json.dumps(
                    {
                        "user_id": self.user_id,
                        "nickname": self.nickname,
                        "cookie": cookie,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self.SESSION_FILE.chmod(0o600)
            logger.info(f"✅ 登录态已保存到 {self.SESSION_FILE}")
        except Exception as e:
            logger.error(f"❌ 保存登录态失败: {e}")

    def login_with_phone(self, phone: str, password: str) -> bool:
        """手机号密码登录"""
        from pyncm.apis.login import LoginViaCellphone
        try:
            result = LoginViaCellphone(phone=phone, password=password)
            if result.get("code") == 200:
                self._after_login(result)
                return True
            else:
                logger.error(f"❌ 手机登录失败: {result.get('msg')}")
                return False
        except Exception as e:
            logger.error(f"❌ 手机登录异常: {e}")
            return False

    def login_with_email(self, email: str, password: str) -> bool:
        """邮箱密码登录"""
        from pyncm.apis.login import LoginViaEmail
        try:
            result = LoginViaEmail(email=email, password=password)
            if result.get("code") == 200:
                self._after_login(result)
                return True
            else:
                logger.error(f"❌ 邮箱登录失败: {result.get('msg')}")
                return False
        except Exception as e:
            logger.error(f"❌ 邮箱登录异常: {e}")
            return False

    def logout(self):
        """登出"""
        from pyncm import SetCurrentSession, CreateNewSession
        SetCurrentSession(CreateNewSession())
        if self.SESSION_FILE.exists():
            self.SESSION_FILE.unlink()
        self.is_logged_in = False
        self.user_id = None
        self.nickname = None
        logger.info("✅ 已登出")

    def _after_login(self, result: Dict[str, Any]):
        """登录成功后的处理"""
        profile = result.get("profile", {})
        self.user_id = profile.get("userId")
        self.nickname = profile.get("nickname")
        self.is_logged_in = True
        logger.info(f"✅ 登录成功: {self.nickname} (uid={self.user_id})")
        self._save_session()


def login_required(func):
    """装饰器: 标记需要登录态的 API"""

    def wrapper(*args, **kwargs):
        from .login import LoginManager

        mgr = LoginManager()
        if not mgr.is_logged_in:
            raise PermissionError("需要登录, 请先调用 LoginManager.login_with_*()")
        return func(*args, **kwargs)

    return wrapper
