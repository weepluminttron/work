# -*- coding: utf-8 -*-
"""用户上下文：线程级当前用户 + 按用户隔离的数据文件路径"""
import os
import shutil
import threading

DATA_ROOT = os.getenv("USER_DATA_ROOT") or os.getenv("DATA_DIR") or os.getcwd()
_local = threading.local()


def set_current_user(username: str):
    _local.user = username or "default"


def current_user() -> str:
    return getattr(_local, "user", None) or "default"


def scope(filename: str) -> str:
    """返回带用户名前缀的文件路径，如 admin__archive.db"""
    return os.path.join(DATA_ROOT, f"{current_user()}__{filename}")


def docs_dir() -> str:
    """每个用户独立的文档归档目录"""
    d = scope("study_docs")
    os.makedirs(d, exist_ok=True)
    return d


def migrate_legacy(admin_user: str):
    """把旧版全局数据迁移到管理员名下（首次启动执行一次）"""
    names = [
        "archive.db", "archive.db-shm", "archive.db-wal",
        "memory_spaced_review.db", "memory_spaced_review.db-shm", "memory_spaced_review.db-wal",
        "wrong_book.json", "quiz_records.json", "chat_memory.json",
        "conversations.json", "goals.json", "llm_cache.json",
        "study_docs", "chroma_study_kb",
    ]
    for name in names:
        scoped = os.path.join(DATA_ROOT, f"{admin_user}__{name}")
        legacy = os.path.join(DATA_ROOT, name)
        if os.path.exists(legacy) and not os.path.exists(scoped):
            try:
                shutil.move(legacy, scoped)
                print(f"📦迁移旧数据：{name} -> {admin_user}__{name}")
            except Exception as e:
                print(f"⚠️迁移失败 {name}: {e}")
    # 服务器旧版向量库可能位于 /data/chroma_study_kb（云硬盘）
    extra_chroma = "/data/chroma_study_kb"
    scoped_chroma = os.path.join(DATA_ROOT, f"{admin_user}__chroma_study_kb")
    if os.path.exists(extra_chroma) and not os.path.exists(scoped_chroma):
        try:
            shutil.move(extra_chroma, scoped_chroma)
            print(f"📦迁移旧数据：{extra_chroma} -> {scoped_chroma}")
        except Exception as e:
            print(f"⚠️迁移失败 {extra_chroma}: {e}")
