# -*- coding: utf-8 -*-
"""长期学习目标存储：目标 + 阶段里程碑 + 进度"""
import json
import os
import threading
import time
import uuid

GOALS_FILE = os.getenv("GOALS_FILE", "goals.json")
_lock = threading.Lock()


def _load() -> dict:
    if not os.path.exists(GOALS_FILE):
        return {}
    try:
        with open(GOALS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data: dict):
    with open(GOALS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def create_goal(subject: str, target_level: str, start_level: str, years: int, milestones: list) -> dict:
    gid = uuid.uuid4().hex[:8]
    now = time.time()
    goal = {
        "id": gid,
        "subject": subject,
        "target_level": target_level,
        "start_level": start_level,
        "years": int(years),
        "milestones": milestones,
        "created": now,
    }
    with _lock:
        data = _load()
        data[gid] = goal
        _save(data)
    return goal


def _summary(goal: dict) -> dict:
    milestones = goal.get("milestones", []) or []
    done = sum(1 for m in milestones if m.get("done"))
    return {
        "id": goal.get("id"),
        "subject": goal.get("subject"),
        "target_level": goal.get("target_level"),
        "start_level": goal.get("start_level"),
        "years": goal.get("years"),
        "progress": done,
        "total": len(milestones),
        "created": goal.get("created"),
    }


def list_goals() -> list:
    with _lock:
        data = _load()
    out = [_summary(g) for g in data.values()]
    out.sort(key=lambda x: x.get("created") or 0, reverse=True)
    return out


def get_goal(gid: str) -> dict:
    with _lock:
        data = _load()
    return data.get(gid)


def mark_phase_done(gid: str, phase_no: int) -> bool:
    with _lock:
        data = _load()
        goal = data.get(gid)
        if not goal:
            return False
        milestones = goal.get("milestones", []) or []
        for m in milestones:
            if m.get("phase") == phase_no:
                m["done"] = True
                _save(data)
                return True
        return False
