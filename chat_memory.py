# -*- coding: utf-8 -*-
"""会话记忆：保存每个用户最近的对话，供 RAG 问答保持上下文（对齐 Coze 的会话服务）"""
import json
import os
import threading

CHAT_MEMORY_FILE = os.getenv("CHAT_MEMORY_FILE", "chat_memory.json")
MAX_TURNS = 6  # 保留最近 6 轮（12 条消息）
_lock = threading.Lock()


def _current_file() -> str:
    if os.path.isabs(CHAT_MEMORY_FILE):
        return CHAT_MEMORY_FILE
    import user_context
    return user_context.scope(CHAT_MEMORY_FILE)


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


def get_history(user_key: str) -> list:
    """返回该用户的最近对话 [{role, text}, ...]"""
    with _lock:
        data = _load()
    return data.get(user_key, []) or []


def add_turn(user_key: str, role: str, text: str):
    """追加一轮消息，超长自动裁剪"""
    with _lock:
        data = _load()
        history = data.get(user_key, []) or []
        history.append({"role": role, "text": (text or "")[:2000]})
        if len(history) > MAX_TURNS * 2:
            history = history[-(MAX_TURNS * 2):]
        data[user_key] = history
        _save(data)


def clear_history(user_key: str) -> int:
    """清空该用户记忆，返回删除的条数"""
    with _lock:
        data = _load()
        removed = len(data.get(user_key, []) or [])
        data.pop(user_key, None)
        _save(data)
    return removed
