# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import video_search


class VideoSearchTest(unittest.TestCase):
    def test_search_links_fallback(self):
        links = video_search._search_links("德语语法")
        self.assertEqual(len(links), 3)
        for item in links:
            self.assertIn("search.bilibili.com", item["link"])
            self.assertIn("keyword=", item["link"])


if __name__ == "__main__":
    unittest.main()
