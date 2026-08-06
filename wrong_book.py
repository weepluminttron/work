# -*- coding: utf-8 -*-
"""错题本：自测题自评后记录错题，支持复习与清除（网页版/飞书版共用）"""
import json
import os
import re
import threading
import time

WRONG_BOOK_FILE = "wrong_book.json"
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(WRONG_BOOK_FILE):
        return {}
    try:
        with open(WRONG_BOOK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    with open(WRONG_BOOK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def split_numbered(text: str):
    """把带编号的文本切分成 [(编号, 内容), ...]"""
    # 兼容 1. / 1、 / 1． / 1: / 1： / **1.** 等常见格式
    pattern = re.compile(r"(?m)^\s*(?:\*\*)?\s*\[?(\d{1,3})\]?\s*[.、．:：]\s*")
    matches = list(pattern.finditer(text or ""))
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((int(m.group(1)), text[m.start():end].strip()))
    return sections


def add_wrong_paper(archive_id: int, subject: str, question_text: str, answer_text: str, numbers=None) -> int:
    """把自测题中做错的题加入错题本；numbers 传题号列表，不传则整卷记录"""
    aid = str(archive_id)
    q_sections = split_numbered(question_text)
    if not q_sections:
        q_sections = [(0, question_text or "")]
    selected = q_sections
    if numbers:
        wanted = {int(n) for n in numbers if str(n).isdigit()}
        if wanted:
            selected = [s for s in q_sections if s[0] in wanted]
    if not selected:
        return 0

    a_map = {no: txt for no, txt in split_numbered(answer_text)}
    with _lock:
        data = _load()
        book = data.setdefault(aid, {"subject": subject or "未分类", "items": []})
        now = time.time()
        for no, q in selected:
            book["items"] = [it for it in book["items"] if it["no"] != no]
            book["items"].append({"no": no, "q": q, "a": a_map.get(no, ""), "added": now})
        book["items"].sort(key=lambda it: it["no"])
        _save(data)
    return len(selected)


def list_wrong() -> list:
    """错题本概览：[{aid, subject, count, added}]"""
    with _lock:
        data = _load()
    result = []
    for aid, book in data.items():
        items = book.get("items") or []
        if not items:
            continue
        result.append({
            "aid": int(aid),
            "subject": book.get("subject", "未分类"),
            "count": len(items),
            "added": max(it.get("added", 0) for it in items),
        })
    result.sort(key=lambda x: x["aid"])
    return result


def get_wrong(archive_id: int) -> list:
    aid = str(archive_id)
    with _lock:
        data = _load()
        book = data.get(aid)
        if not book:
            return []
        return sorted(book.get("items") or [], key=lambda it: it["no"])


def clear_wrong(archive_id: int, numbers=None) -> int:
    """清除错题；numbers 传题号列表，不传则清空该归档全部错题"""
    aid = str(archive_id)
    with _lock:
        data = _load()
        book = data.get(aid)
        if not book:
            return 0
        items = book.get("items") or []
        if not numbers:
            removed = len(items)
            del data[aid]
        else:
            wanted = {int(n) for n in numbers if str(n).isdigit()}
            keep = [it for it in items if it["no"] not in wanted]
            removed = len(items) - len(keep)
            if keep:
                book["items"] = keep
            else:
                del data[aid]
        _save(data)
    return removed
