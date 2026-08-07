# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import skill_market_store


class SkillMarketStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_file = skill_market_store.CUSTOM_SKILLS_FILE
        skill_market_store.CUSTOM_SKILLS_FILE = os.path.join(self.tmp, "custom_skills.json")

    def tearDown(self):
        skill_market_store.CUSTOM_SKILLS_FILE = self.orig_file

    def test_add_and_list(self):
        pkg = skill_market_store.add_skill({"title": "德语单词对战", "desc": "对战记单词", "skills": ["词性", "拼写"]})
        self.assertTrue(pkg["id"].startswith("custom_"))
        skills = skill_market_store.list_skills()
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["title"], "德语单词对战")


if __name__ == "__main__":
    unittest.main()
