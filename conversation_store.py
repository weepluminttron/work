# -*- coding: utf-8 -*-
"""多会话存储：保存每个对话的消息，退出/重启后仍可继续"""
import json
import os
import threading
import time
import uuid

CONVERSATIONS_FILE = os.getenv("CONVERSATIONS_FILE", "conversations.json")
_lock = threading.Lock()
TRASH_KEY = "_trash"


def _current_file() -> str:
    if os.path.isabs(CONVERSATIONS_FILE):
        return CONVERSATIONS_FILE
    import user_context
    return user_context.scope(CONVERSATIONS_FILE)


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


def list_conversations() -> list:
    """返回对话列表（按更新时间倒序）"""
    with _lock:
        data = _load()
    out = []
    for cid, conv in data.items():
        if cid == TRASH_KEY:
            continue
        out.append({
            "id": cid,
            "title": conv.get("title", "新对话"),
            "updated": conv.get("updated", 0),
            "count": len(conv.get("messages", []) or []) // 2,
        })
    out.sort(key=lambda x: x["updated"] or 0, reverse=True)
    return out


def create_conversation() -> dict:
    cid = uuid.uuid4().hex[:12]
    now = time.time()
    conv = {"title": "新对话", "messages": [], "created": now, "updated": now, "auto_titled": False}
    with _lock:
        data = _load()
        data[cid] = conv
        _save(data)
    return {"id": cid, "title": conv["title"], "updated": now, "count": 0}


def get_conversation(cid: str) -> dict:
    with _lock:
        data = _load()
    if cid == TRASH_KEY:
        return None
    conv = data.get(cid)
    if not conv:
        return None
    return {
        "id": cid,
        "title": conv.get("title", "新对话"),
        "messages": conv.get("messages", []) or [],
        "auto_titled": conv.get("auto_titled", False),
    }


def append_messages(cid: str, user_text: str, assistant_text: str) -> bool:
    """追加一轮对话；首轮自动用用户消息生成标题"""
    with _lock:
        data = _load()
        conv = data.get(cid)
        if conv is None:
            return False
        messages = conv.setdefault("messages", [])
        messages.append({"role": "user", "text": user_text})
        messages.append({"role": "assistant", "text": assistant_text})
        if len(messages) <= 2 and conv.get("title", "新对话") == "新对话":
            title = (user_text or "").strip().replace("\n", " ")[:30]
            conv["title"] = title if title else "新对话"
        conv["updated"] = time.time()
    _save(data)
    return True


def update_title(cid: str, title: str) -> bool:
    """更新对话标题（AI 自动命名）"""
    title = (title or "").strip()
    if not title:
        return False
    with _lock:
        data = _load()
        conv = data.get(cid)
        if conv is None:
            return False
        conv["title"] = title[:30]
        conv["auto_titled"] = True
        conv["updated"] = time.time()
        _save(data)
    return True


def delete_conversation(cid: str) -> bool:
    """删除对话（软删除：移入回收站，可恢复）"""
    with _lock:
        data = _load()
        if cid not in data:
            return False
        conv = data.pop(cid)
        conv["deleted_at"] = time.time()
        data.setdefault(TRASH_KEY, {})[cid] = conv
        _save(data)
    return True


def restore_conversation(cid: str) -> bool:
    """从回收站恢复对话"""
    with _lock:
        data = _load()
        trash = data.get(TRASH_KEY, {})
        if cid not in trash:
            return False
        conv = trash.pop(cid)
        conv.pop("deleted_at", None)
        data[cid] = conv
        if not trash:
            data.pop(TRASH_KEY, None)
        _save(data)
    return True
