# -*- coding: utf-8 -*-
"""答题记录存储：保存/恢复做题进度（断点续做）"""
import json
import os
import threading
import time

QUIZ_RECORD_FILE = "quiz_records.json"
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(QUIZ_RECORD_FILE):
        return {}
    try:
        with open(QUIZ_RECORD_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    with open(QUIZ_RECORD_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def save_record(archive_id: int, answers: dict, marked: list):
    """保存答题进度：{题号: 答案} + 标记题号列表"""
    aid = str(archive_id)
    clean_answers = {}
    for no, ans in (answers or {}).items():
        s = str(ans or "").strip()
        if s:
            clean_answers[str(no)] = s.upper()
    clean_marked = [int(n) for n in (marked or []) if str(n).isdigit()]
    with _lock:
        data = _load()
        data[aid] = {
            "answers": clean_answers,
            "marked": clean_marked,
            "updated": time.time(),
        }
        _save(data)
    return True


def get_record(archive_id: int) -> dict:
    aid = str(archive_id)
    with _lock:
        data = _load()
    rec = data.get(aid) or {}
    return {
        "answers": rec.get("answers") or {},
        "marked": rec.get("marked") or [],
        "updated": rec.get("updated"),
    }
