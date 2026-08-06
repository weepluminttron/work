# -*- coding: utf-8 -*-
"""自测题纯逻辑：答案提取、选项解析、批改（不依赖 Flask，便于单元测试）"""
import re

from wrong_book import split_numbered


def extract_answer_keys(answer_text: str) -> dict:
    """从答案文本中提取正确答案：{题号: 选项字母/多选组合/对错}（兼容多种AI输出格式）"""
    text = answer_text or ""
    keys = {}
    # 按编号切段：支持 1. / 1、 / 1． / 1: / 1： / **1.** / 【1】
    sec_pat = re.compile(r"(?m)^\s*(?:\*\*)?\s*\[?(\d{1,3})\]?\s*[.、．:：]\s*(.*)$")
    sections = [(int(m.group(1)), m.group(2)) for m in sec_pat.finditer(text)]
    value_pat = r"([A-Da-d]{1,4}(?:\s*[、,，和与]\s*[A-Da-d]{1,4})*|对|错|正确|错误|√|×|[TF])"
    if not sections:
        # 全文宽松匹配：第1题 正确答案 B / 1 答案：B 等
        loose = re.compile(
            r"(?:第\s*|[【\[])?\s*(\d{1,3})\s*(?:题|[】\]])?\s*[.、．:：]?\s*"
            r"(?:正确答案|答案|答案为|选|选择)\s*[是为：:]?\s*\*{0,2}" + value_pat
        )
        for m in loose.finditer(text):
            keys.setdefault(int(m.group(1)), _normalize_answer(m.group(2)))
        return keys
    for no, sec in sections:
        s = sec.replace("*", "").strip()
        m = re.search(r"(?:正确答案|答案|答案为|选|选择)\s*[是为：:]?\s*" + value_pat, s)
        if m:
            keys.setdefault(no, _normalize_answer(m.group(1)))
            continue
        m = re.match(r"([A-Da-d]{1,4}|对|错|正确|错误|√|×|[TF])\s*[.、．:：)）。]\s*", s)
        if m:
            keys.setdefault(no, _normalize_answer(m.group(1)))
            continue
        m = re.search(r"[（(]\s*([A-Da-d])\s*[）)]", s)
        if m:
            keys.setdefault(no, m.group(1).upper())
    return keys


def _normalize_answer(raw) -> str:
    """统一答案格式：判断题同义词归一、多选去分隔符排序、单选转大写"""
    s = str(raw or "").strip().upper()
    if s in ("正确", "对", "T", "TRUE", "√"):
        return "对"
    if s in ("错误", "错", "F", "FALSE", "×", "X"):
        return "错"
    letters = re.sub(r"[^A-D]", "", s)
    if letters:
        return "".join(sorted(set(letters)))
    return s


def parse_quiz_options(q_text: str) -> list:
    """从题目文本中提取 A/B/C/D 选项"""
    options = []
    for m in re.finditer(r"(?m)^\s*(?:\*\*)?\s*([A-Da-d])\s*[.、．)）]\s*(.*)$", q_text):
        options.append({"key": m.group(1).upper(), "text": m.group(2).strip().rstrip("*").strip()})
    return options


def grade_paper(paper: dict, raw_answers: str) -> dict:
    """批改试卷（纯逻辑，不写错题本）。
    返回 {ok, reply, graded, correct, wrong_nos, short_items}
    short_items: 需要AI批改的简答题 [{no, q, user, ref}, ...]
    """
    keys = (paper or {}).get("keys") or {}
    raw = (raw_answers or "").strip()
    if not raw:
        return {"ok": False, "reply": "请输入你的答案，例如：B,A,C,D", "graded": 0, "correct": 0, "wrong_nos": [], "short_items": []}
    q_nos = [no for no, _ in split_numbered(paper.get("q") or "")]
    q_map = {no: txt for no, txt in split_numbered(paper.get("q") or "")}
    a_map = {no: txt for no, txt in split_numbered(paper.get("a") or "")}

    # 格式1：1:B 2:A / 1.B / 第1题 B（按题号对应，顺序无关）
    pair_map = {}
    for m in re.finditer(r"(?:第)?\s*(\d{1,3})\s*[题\.、．:：]?\s*([A-Da-d]{1,4}|对|错|正确|错误|√|×|[TF])", raw):
        pair_map.setdefault(int(m.group(1)), m.group(2).upper())
    if len(pair_map) >= 2:
        answer_map = pair_map
    else:
        # 格式2：顺序答案 B,A,C,D / B A C D / BACD
        tokens = re.split(r"[,，\s、;；]+", raw)
        if len(tokens) == 1 and len(tokens[0]) > 1 and all(ch.upper() in "ABCD" for ch in tokens[0]):
            tokens = list(tokens[0])
        answer_map = {}
        for i, no in enumerate(q_nos):
            if i < len(tokens):
                t = tokens[i].strip()
                answer_map[no] = "未作答" if (not t or t.upper() == "X") else t.upper()
        if not answer_map:
            return {"ok": False, "reply": "没看懂你的答案格式，请用逗号分隔，例如：B,A,C,D", "graded": 0, "correct": 0, "wrong_nos": [], "short_items": []}

    # 简答题：不在选择题答案表中的题目，且学生已作答
    short_items = []
    for no in q_nos:
        if no in keys:
            continue
        user_ans = answer_map.get(no, "未作答")
        if user_ans and user_ans != "未作答":
            short_items.append({
                "no": no,
                "q": q_map.get(no, ""),
                "user": user_ans,
                "ref": a_map.get(no, ""),
            })

    lines = []
    correct = 0
    graded = 0
    wrong_nos = []
    for no in q_nos:
        if no not in keys:
            continue
        correct_key = keys[no]
        user_key = answer_map.get(no, "未作答")
        graded += 1
        if user_key != "未作答" and _normalize_answer(user_key) == _normalize_answer(correct_key):
            correct += 1
            lines.append(f"✅ 第{no}题：你的答案 {user_key} 正确")
        else:
            wrong_nos.append(no)
            lines.append(f"❌ 第{no}题：你的答案 {user_key}，正确答案 {correct_key}")
        exp = a_map.get(no, "")
        if exp:
            exp_short = re.sub(r"^\s*\d{1,3}\s*[.、．]\s*", "", exp).strip()
            lines.append(f"   📖 {exp_short[:150]}")
    if graded == 0:
        if not short_items:
            return {
                "ok": False,
                "reply": "没有可批改的内容：请确认已作答选择题或简答题",
                "graded": 0,
                "correct": 0,
                "wrong_nos": [],
                "short_items": [],
            }
    else:
        lines.append(f"\n🎯 客观题 {correct}/{graded} 正确")
    return {
        "ok": True,
        "reply": "\n".join(lines),
        "graded": graded,
        "correct": correct,
        "wrong_nos": wrong_nos,
        "short_items": short_items,
    }
