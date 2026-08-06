# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta

# 在导入 review_scheduler 前 mock apscheduler（本地测试环境可能未安装）
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

# 本地测试环境可能没有 requests，mock 掉（review_scheduler 仅用于飞书推送）
fake_requests = types.ModuleType("requests")
fake_requests.post = lambda *a, **k: None
sys.modules["requests"] = fake_requests

# 在临时目录导入，避免污染项目数据库
_TMP = tempfile.mkdtemp()
os.chdir(_TMP)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import review_scheduler


class FakeDateTime(datetime):
    fixed = datetime(2026, 8, 6, 12, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


class ReviewSchedulerTest(unittest.TestCase):
    def setUp(self):
        review_scheduler.datetime = FakeDateTime
        review_scheduler.init_memory_db()
        with review_scheduler.DB_LOCK:
            conn = review_scheduler.get_db_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM study_progress")
            conn.commit()

    def _set_complete_date(self, archive_id, day_no, date_str):
        with review_scheduler.DB_LOCK:
            conn = review_scheduler.get_db_conn()
            cur = conn.cursor()
            cur.execute(
                "UPDATE study_progress SET finished=1, complete_date=? WHERE archive_id=? AND day_no=?",
                (date_str, archive_id, day_no),
            )
            conn.commit()

    def test_streak_consecutive_days(self):
        review_scheduler.save_daily_tasks(1, "\u5fb7\u8bed", ["\u4efb\u52a11"])
        review_scheduler.save_daily_tasks(2, "\u82f1\u8bed", ["\u4efb\u52a12"])
        today = FakeDateTime.fixed.date().isoformat()
        yesterday = (FakeDateTime.fixed.date() - timedelta(days=1)).isoformat()
        self._set_complete_date(1, 1, yesterday)
        self._set_complete_date(2, 1, today)
        self.assertEqual(review_scheduler.get_study_streak(), 2)

    def test_streak_gap_breaks(self):
        review_scheduler.save_daily_tasks(1, "\u5fb7\u8bed", ["\u4efb\u52a11"])
        review_scheduler.save_daily_tasks(2, "\u82f1\u8bed", ["\u4efb\u52a12"])
        today = FakeDateTime.fixed.date().isoformat()
        day_before_yesterday = (FakeDateTime.fixed.date() - timedelta(days=2)).isoformat()
        self._set_complete_date(1, 1, day_before_yesterday)
        self._set_complete_date(2, 1, today)
        self.assertEqual(review_scheduler.get_study_streak(), 1)

    def test_report_contains_subject_and_rate(self):
        review_scheduler.save_daily_tasks(1, "\u5fb7\u8bed", ["\u4efb\u52a11", "\u4efb\u52a12"])
        review_scheduler.mark_task_finished(1, 1)
        report = review_scheduler.get_study_report()
        self.assertIn("\u5fb7\u8bed", report)
        self.assertIn("50%", report)
        self.assertIn("\u8bca\u65ad\u5efa\u8bae", report)


if __name__ == "__main__":
    unittest.main()
