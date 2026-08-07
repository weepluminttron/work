# -*- coding: utf-8 -*-
"""自定义技能存储：AI 生成的新技能包持久化，供技能市场展示"""
import json
import os
import threading
import time
import uuid

CUSTOM_SKILLS_FILE = os.getenv("CUSTOM_SKILLS_FILE", "custom_skills.json")
_lock = threading.Lock()


def _current_file() -> str:
    if os.path.isabs(CUSTOM_SKILLS_FILE):
        return CUSTOM_SKILLS_FILE
    import user_context
    return user_context.scope(CUSTOM_SKILLS_FILE)


def _load() -> dict:
    path = _current_file()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    with open(_current_file(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_skill(package: dict) -> dict:
    """保存一个自定义技能包，返回带 id 的副本"""
    pid = "custom_" + uuid.uuid4().hex[:8]
    package = dict(package)
    package["id"] = pid
    package["type"] = "package"
    package.setdefault("custom", True)
    package.setdefault("created", time.time())
    with _lock:
        data = _load()
        data[pid] = package
        _save(data)
    return package


def list_skills() -> list:
    with _lock:
        data = _load()
    out = []
    for pid, pkg in data.items():
        pkg = dict(pkg)
        pkg["id"] = pid
        out.append(pkg)
    out.sort(key=lambda x: x.get("created") or 0, reverse=True)
    return out
