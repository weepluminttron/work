# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 本地测试环境可能没有 requests / apscheduler，先 mock 再导入
fake_requests = types.ModuleType("requests")
fake_requests.post = lambda *a, **k: None
sys.modules["requests"] = fake_requests

aps = types.ModuleType("apscheduler")
schedulers = types.ModuleType("apscheduler.schedulers")
background = types.ModuleType("apscheduler.schedulers.background")


class BackgroundScheduler:
    def __init__(self, **kwargs):
        self.running = False
        self.jobs = []

    def add_job(self, fn, trigger=None, **kwargs):
        self.jobs.append((fn, trigger))

    def start(self):
        self.running = True

    def shutdown(self, **kwargs):
        self.running = False


background.BackgroundScheduler = BackgroundScheduler
schedulers.background = background
aps.schedulers = schedulers
sys.modules["apscheduler"] = aps
sys.modules["apscheduler.schedulers"] = schedulers
sys.modules["apscheduler.schedulers.background"] = background

_TMP = tempfile.mkdtemp()
os.chdir(_TMP)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review_scheduler
from archive_db import init_db
import study_service
from study_service import delete_text, done_text, parse_goal_cmd, progress_text, report_text

init_db()


class FakeDateTime(datetime):
    fixed = datetime(2026, 8, 6, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


class StudyServiceTest(unittest.TestCase):
    def setUp(self):
        review_scheduler.datetime = FakeDateTime
        review_scheduler.init_memory_db()
        with review_scheduler.DB_LOCK:
            conn = review_scheduler.get_db_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM study_progress")
            conn.commit()

    def test_done_text(self):
        review_scheduler.save_daily_tasks(1, "\u5fb7\u8bed", ["\u4efb\u52a1"])
        self.assertIn("\u6253\u5361\u6210\u529f", done_text(1, 1))
        self.assertIn("\u672a\u627e\u5230", done_text(1, 99))

    def test_progress_text_empty(self):
        self.assertIn("\u6682\u65e0\u4efb\u52a1\u8bb0\u5f55", progress_text(9))

    def test_report_text(self):
        review_scheduler.save_daily_tasks(1, "\u5fb7\u8bed", ["\u4efb\u52a1"])
        self.assertIn("\u5fb7\u8bed", report_text())

    def test_delete_text_not_found(self):
        self.assertIn("\u627e\u4e0d\u5230", delete_text("/del id 99999"))

    def test_delete_text_usage(self):
        self.assertIn("/del id", delete_text("/del \u4e71\u5199\u7684\u5185\u5bb9"))

    def test_parse_goal_cmd(self):
        r = parse_goal_cmd("/goal 3\u5e74 \u5fb7\u8bed B2")
        self.assertEqual(r["years"], 3)
        self.assertEqual(r["subject"], "\u5fb7\u8bed")
        self.assertEqual(r["target_level"], "B2")
        self.assertEqual(r["start_level"], "\u96f6\u57fa\u7840")
        r2 = parse_goal_cmd("/goal 2\u5e74 \u82f1\u8bed A2 C1")
        self.assertEqual(r2["start_level"], "A2")
        self.assertEqual(r2["target_level"], "C1")
        self.assertIsNone(parse_goal_cmd("/goal"))

    def test_market_generate_text(self):
        import llm_summary
        def fake_generate(topic):
            return {
                "title": "德语单词对战",
                "icon": "⚔️",
                "desc": "对战记单词",
                "tags": ["语言"],
                "subject": "德语",
                "mode": "对战",
                "skills": ["词性判断", "拼写挑战"],
                "prereq": ["发音"],
                "misconceptions": ["词性记混"],
                "seeds": [{"q": "der Tisch?", "a": "阳性"}],
            }
        orig = llm_summary.generate_skill_package
        llm_summary.generate_skill_package = fake_generate
        try:
            text = study_service.market_generate_text("/market 德语单词对战")
            self.assertIn("已生成", text)
            self.assertIn("德语单词对战", text)
        finally:
            llm_summary.generate_skill_package = orig

    def test_grade_short_answers_text(self):
        def fake_llm(prompt, timeout=60):
            return '[{"no":1,"judge":"\u6b63\u786e","score":90,"comment":"\u5f88\u597d"},{"no":2,"judge":"\u9519\u8bef","score":40,"comment":"\u6f0f\u4e86\u8981\u70b9"}]'
        study_service.llm_request = fake_llm
        items = [
            {"no": 1, "q": "q1", "user": "\u597d\u7b54\u6848", "ref": "\u53c2\u8003\u7b54\u68481"},
            {"no": 2, "q": "q2", "user": "\u5dee\u7b54\u6848", "ref": "\u53c2\u8003\u7b54\u68482"},
        ]
        text, wrong = study_service.grade_short_answers_text(items)
        self.assertEqual(wrong, [2])
        self.assertIn("\u2705", text)
        self.assertIn("\u274c", text)


if __name__ == "__main__":
    unittest.main()
