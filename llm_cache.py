# -*- coding: utf-8 -*-
"""LLM 结果缓存：同样的指令/文档重复生成时直接秒回"""
import hashlib
import json
import os
import threading
import time

LLM_CACHE_FILE = os.getenv("LLM_CACHE_FILE", "llm_cache.json")
CACHE_TTL = 7 * 24 * 3600  # 缓存7天
_lock = threading.Lock()


def _current_file() -> str:
    if os.path.isabs(LLM_CACHE_FILE):
        return LLM_CACHE_FILE
    import user_context
    return user_context.scope(LLM_CACHE_FILE)


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


def make_key(*parts) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def cache_get(key: str):
    with _lock:
        data = _load()
    item = data.get(key)
    if not item:
        return None
    if time.time() - item.get("ts", 0) > CACHE_TTL:
        return None
    return item.get("result")


def cache_set(key: str, result):
    with _lock:
        data = _load()
        data[key] = {"result": result, "ts": time.time()}
        # 防止缓存无限膨胀：最多保留 500 条
        if len(data) > 500:
            for old_key in sorted(data, key=lambda k: data[k].get("ts", 0))[: len(data) - 500]:
                data.pop(old_key, None)
        _save(data)


def clear_cache() -> int:
    with _lock:
        data = _load()
        count = len(data)
        _save({})
    return count
