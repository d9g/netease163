"""
login 模块 - 借鉴 NetCloud NetCloudLogin 类 (简化版)
底层用 pyncm.login 实现加密登录

导出:
- LoginManager: 登录态管理
- login_required: 装饰器
- get_my_favorite: 我的红心歌单
- get_my_recommend: 每日推荐
- get_my_fm: 私人 FM
- get_my_playlists: 我的所有歌单
- generate_qr_key: 生成扫码登录二维码 (老杨 18:06 路径 A)
- check_qr_login: 检查扫码状态
- login_via_cookie: Cookie 兜底登录
"""
from .login import (
    LoginManager, login_required,
    get_my_favorite, get_my_recommend, get_my_fm, get_my_playlists,
    generate_qr_key, check_qr_login, login_via_cookie,
)

__all__ = [
    "LoginManager", "login_required",
    "get_my_favorite", "get_my_recommend", "get_my_fm", "get_my_playlists",
    "generate_qr_key", "check_qr_login", "login_via_cookie",
]
