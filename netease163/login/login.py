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


# ==================== 登录态专属 API ====================
# 借鉴 NetCloud: get_self_playlist / get_self_playlists 等登录后方法
# 这里用 pyncm 的 GetUserAccount / GetUserPlaylists 实现

def get_my_favorite(limit: int = 50) -> Dict[str, Any]:
    """
    获取我的红心歌单 (登录态)

    Returns:
        {
            "user_id": int,
            "nickname": str,
            "total": int,
            "songs": [
                {"id": int, "name": str, "artists": [...], "album": "..."}
            ]
        }
    """
    from pyncm.apis.user import GetUserPlaylists
    from pyncm.apis.playlist import GetPlaylistInfo
    from pyncm import GetCurrentSession

    mgr = LoginManager()
    if not mgr.is_logged_in:
        raise PermissionError("未登录, 请先调用 login_with_*()")

    # 1. 拿用户所有歌单
    pl_result = GetUserPlaylists(mgr.user_id, limit=30)
    if pl_result.get("code") != 200:
        raise RuntimeError(f"获取歌单失败: {pl_result.get('msg')}")

    playlists = pl_result.get("playlist", [])
    # 2. 找"我喜欢的音乐" (specialType=5 是默认红心歌单)
    fav = None
    for p in playlists:
        if p.get("specialType") == 5 or "喜欢" in p.get("name", ""):
            fav = p
            break

    if not fav:
        return {"user_id": mgr.user_id, "nickname": mgr.nickname, "total": 0, "songs": []}

    # 3. 拿红心歌单详情
    detail = GetPlaylistInfo(fav["id"])
    if detail.get("code") != 200:
        raise RuntimeError(f"红心歌单详情失败")

    p = detail.get("playlist", {})
    track_ids = [t["id"] for t in p.get("trackIds", [])[:limit]]
    songs = []
    if track_ids:
        from pyncm.apis.track import GetTrackDetail
        td = GetTrackDetail(track_ids)
        if td.get("code") == 200:
            songs = [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "artists": [a["name"] for a in s.get("ar", [])],
                    "album": s.get("al", {}).get("name", ""),
                }
                for s in td.get("songs", [])
            ]

    return {
        "user_id": mgr.user_id,
        "nickname": mgr.nickname,
        "playlist_name": p.get("name", ""),
        "total": p.get("trackCount", 0),
        "songs": songs,
    }


def get_my_recommend() -> Dict[str, Any]:
    """
    获取每日推荐 (登录态)
    """
    mgr = LoginManager()
    if not mgr.is_logged_in:
        raise PermissionError("未登录, 请先调用 login_with_*()")

    from pyncm.apis.cloud import GetDailyRecommendations
    result = GetDailyRecommendations()
    if result.get("code") != 200:
        raise RuntimeError(f"每日推荐失败: {result.get('msg')}")

    songs = [
        {
            "id": s["id"],
            "name": s["name"],
            "artists": [a["name"] for a in s.get("ar", [])],
            "album": s.get("al", {}).get("name", ""),
        }
        for s in result.get("dailySongs", [])
    ]
    return {"date": result.get("date", ""), "songs": songs}


def get_my_fm() -> Dict[str, Any]:
    """
    获取私人 FM (登录态, 一次返回 3 首)
    """
    mgr = LoginManager()
    if not mgr.is_logged_in:
        raise PermissionError("未登录, 请先调用 login_with_*()")

    from pyncm.apis.cloud import GetPersonalFm
    result = GetPersonalFm()
    if result.get("code") != 200:
        raise RuntimeError(f"私人 FM 失败: {result.get('msg')}")

    songs = [
        {
            "id": s["id"],
            "name": s["name"],
            "artists": [a["name"] for a in s.get("artists", [])],
            "album": s.get("album", ""),
        }
        for s in result.get("data", [])
    ]
    return {"count": len(songs), "songs": songs}


def get_my_playlists(limit: int = 30) -> Dict[str, Any]:
    """
    获取我的所有歌单 (登录态) - 借鉴 NetCloud get_self_playlists
    """
    mgr = LoginManager()
    if not mgr.is_logged_in:
        raise PermissionError("未登录, 请先调用 login_with_*()")

    from pyncm.apis.user import GetUserPlaylists
    result = GetUserPlaylists(mgr.user_id, limit=limit)
    if result.get("code") != 200:
        raise RuntimeError(f"获取歌单失败: {result.get('msg')}")

    playlists = [
        {
            "id": p["id"],
            "name": p["name"],
            "track_count": p.get("trackCount", 0),
            "play_count": p.get("playCount", 0),
            "creator": p.get("creator", {}).get("nickname", ""),
            "is_favorite": p.get("specialType") == 5,
        }
        for p in result.get("playlist", [])
    ]
    return {"user_id": mgr.user_id, "total": len(playlists), "playlists": playlists}



# ==================== QR 扫码登录 (绕过 8821 风控) ====================

def generate_qr_key() -> Dict[str, Any]:
    """
    生成扫码登录 unikey + 二维码 URL

    老杨 18:06 拍板: 路径 A 扫码登录绕过密码 8821 风控
    流程:
    1. unikey = LoginQrcodeUnikey() - 服务端发 unikey
    2. url = GetLoginQRCodeUrl(unikey) - 生成二维码 URL
    3. 前端展示 QR code 图片
    4. 老杨打开网易云 App 扫码确认登录
    5. 前端轮询 LoginQrcodeCheck(unikey) 直到 status=2 (登录成功)

    返回:
    {
        "unikey": "xxx",
        "qr_url": "https://music.163.com/login?codekey=xxx",
        "qr_base64": "data:image/png;base64,xxx"  # 直接给前端展示
    }
    """
    import qrcode
    import io
    import base64
    from pyncm.apis.login import (
        LoginQrcodeUnikey, GetLoginQRCodeUrl, GetCurrentSession, WriteLoginInfo
    )

    # 1. 生成 unikey
    resp = LoginQrcodeUnikey(dtype=1)
    unikey = resp.get("unikey", "")
    if not unikey:
        return {"error": f"生成 unikey 失败: {resp}"}

    # 2. 生成二维码 URL
    qr_url = GetLoginQRCodeUrl(unikey)

    # 3. 生成 base64 QR 图
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    qr_base64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    return {
        "unikey": unikey,
        "qr_url": qr_url,
        "qr_base64": qr_base64,
    }


def check_qr_login(unikey: str) -> Dict[str, Any]:
    """
    检查扫码状态 (老杨 18:06 路径 A)

    状态码 (网易云实际):
    - 801: 等待扫码
    - 802: 已扫码待确认
    - 803: 登录成功
    - 800: 二维码过期或不存在
    """
    from pyncm.apis.login import LoginQrcodeCheck, GetCurrentLoginStatus

    try:
        resp = LoginQrcodeCheck(unikey=unikey, type=1)
        code = resp.get("code", 0)

        if code == 803:
            # 登录成功! 用 LoginManager 持久化
            mgr = LoginManager()
            status = GetCurrentLoginStatus()
            profile = status.get("data", {}).get("profile", {}) or {}
            mgr.user_id = profile.get("userId", 0) or profile.get("id", 0)
            mgr.nickname = profile.get("nickname", "")
            mgr.is_logged_in = True
            mgr._save_session()
            return {
                "status": 803,
                "message": "登录成功",
                "code": code,
                "user_id": mgr.user_id,
                "nickname": mgr.nickname,
            }

        # 状态码 → 用户友好消息
        status_map = {800: 8821, 801: 0, 802: 1, 803: 2}
        status = status_map.get(code, code)
        msg_map = {
            0: "等待扫码", 1: "已扫码, 请在手机上点确认", 2: "登录成功",
            8821: "二维码已过期, 请重新生成",
        }
        return {"status": status, "message": msg_map.get(status, f"未知状态: {code}"), "code": code}

    except Exception as e:
        return {"status": -1, "message": f"查询失败: {e}", "error": str(e)}


def login_via_cookie(music_u: str, **kwargs) -> Dict[str, Any]:
    """
    Cookie 兜底登录 (老杨不想扫码就用这个)

    步骤:
    1. 浏览器打开 music.163.com 登录
    2. F12 → Console 输入: document.cookie
    3. 找 MUSIC_U=xxx; 复制值
    4. POST /api/v1/login/cookie?music_u=xxx
    """
    from pyncm.apis.login import LoginViaCookie, GetCurrentLoginStatus

    try:
        result = LoginViaCookie(MUSIC_U=music_u, **kwargs)
        if result.get("code") == 200:
            mgr = LoginManager()
            status = GetCurrentLoginStatus()
            profile = status.get("data", {}).get("profile", {}) or {}
            mgr.user_id = profile.get("userId", 0) or profile.get("id", 0)
            mgr.nickname = profile.get("nickname", "")
            mgr.is_logged_in = True
            mgr._save_session()
            return {
                "success": True,
                "user_id": mgr.user_id,
                "nickname": mgr.nickname,
            }
        return {"success": False, "error": result.get("message", "Cookie 登录失败")}
    except Exception as e:
        return {"success": False, "error": str(e)}
