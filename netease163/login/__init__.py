"""
login 模块 - 借鉴 NetCloud NetCloudLogin 类 (简化版)
底层用 pyncm.login 实现加密登录
"""
from .login import LoginManager, login_required

__all__ = ["LoginManager", "login_required"]
