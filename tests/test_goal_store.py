# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import goal_store


class GoalStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_file = goal_store.GOALS_FILE
        goal_store.GOALS_FILE = os.path.join(self.tmp, "goals.json")

    def tearDown(self):
        goal_store.GOALS_FILE = self.orig_file

    def test_create_list_get_mark(self):
        milestones = [
            {"phase": 1, "name": "A1", "months": 6, "focus": "发音", "evaluation": "测试", "done": False},
            {"phase": 2, "name": "A2", "months": 6, "focus": "词汇", "evaluation": "测试", "done": False},
        ]
        goal = goal_store.create_goal("德语", "B2", "零基础", 3, milestones)
        self.assertTrue(goal["id"])
        self.assertEqual(goal_store.list_goals()[0]["total"], 2)
        self.assertEqual(goal_store.list_goals()[0]["progress"], 0)
        self.assertTrue(goal_store.mark_phase_done(goal["id"], 1))
        self.assertEqual(goal_store.list_goals()[0]["progress"], 1)
        self.assertFalse(goal_store.mark_phase_done(goal["id"], 9))
        self.assertIsNone(goal_store.get_goal("not_exist"))


if __name__ == "__main__":
    unittest.main()
