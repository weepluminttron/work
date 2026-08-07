# -*- coding: utf-8 -*-
"""用户账号：注册 / 登录 / 角色（admin / user）"""
import hashlib
import json
import os
import threading
import time
import uuid

_DATA_ROOT = os.getenv("USER_DATA_ROOT") or os.getenv("DATA_DIR") or os.getcwd()
USERS_FILE = os.getenv("USERS_FILE", os.path.join(_DATA_ROOT, "users.json"))
_lock = threading.Lock()


def _hash(password: str, salt: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


def _load() -> dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def ensure_admin(username: str, password: str):
    """启动时确保管理员账号存在，且密码始终与 .env 同步（.env 为准）"""
    if not username or not password:
        return
    with _lock:
        data = _load()
        if username in data:
            if data[username].get("role") == "admin":
                # 管理员密码以 .env 为准，每次启动同步
                data[username]["salt"] = uuid.uuid4().hex[:8]
                data[username]["hash"] = _hash(password, data[username]["salt"])
                _save(data)
                print("✅管理员密码已按 .env 配置同步")
            return
        salt = uuid.uuid4().hex[:8]
        data[username] = {
            "role": "admin",
            "salt": salt,
            "hash": _hash(password, salt),
            "created": time.time(),
        }
        _save(data)
        print(f"✅管理员账号已创建：{username}")


def register(username: str, password: str) -> tuple:
    """注册普通用户，返回 (ok, msg)"""
    username = (username or "").strip()
    if not (3 <= len(username) <= 20):
        return False, "用户名需3-20个字符"
    if len(password or "") < 6:
        return False, "密码至少6位"
    with _lock:
        data = _load()
        if username in data:
            return False, "用户名已存在"
        salt = uuid.uuid4().hex[:8]
        data[username] = {
            "role": "user",
            "salt": salt,
            "hash": _hash(password, salt),
            "created": time.time(),
        }
        _save(data)
    return True, "注册成功"


def authenticate(username: str, password: str):
    """验证登录，成功返回用户信息，失败返回 None"""
    username = (username or "").strip()
    if not username or not password:
        return None
    with _lock:
        data = _load()
    user = data.get(username)
    if not user:
        return None
    if _hash(password, user["salt"]) != user["hash"]:
        return None
    return {"username": username, "role": user.get("role", "user")}


def set_password(username: str, password: str) -> bool:
    username = (username or "").strip()
    if not username or len(password or "") < 6:
        return False
    with _lock:
        data = _load()
        user = data.get(username)
        if not user:
            return False
        user["salt"] = uuid.uuid4().hex[:8]
        user["hash"] = _hash(password, user["salt"])
        _save(data)
    return True


def change_password(username: str, old_password: str, new_password: str):
    """修改自己的密码"""
    if not authenticate(username, old_password):
        return False, "当前密码错误"
    if len(new_password or "") < 6:
        return False, "新密码至少6位"
    if set_password(username, new_password):
        return True, "密码已修改"
    return False, "修改失败"
