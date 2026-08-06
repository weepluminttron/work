# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import llm_cache


class LlmCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_file = llm_cache.LLM_CACHE_FILE
        llm_cache.LLM_CACHE_FILE = os.path.join(self.tmp, "llm_cache.json")

    def tearDown(self):
        llm_cache.LLM_CACHE_FILE = self.orig_file

    def test_get_set_roundtrip(self):
        key = llm_cache.make_key("test", 3, True, "内容")
        self.assertIsNone(llm_cache.cache_get(key))
        llm_cache.cache_set(key, {"q": "题目", "a": "答案"})
        self.assertEqual(llm_cache.cache_get(key), {"q": "题目", "a": "答案"})

    def test_key_differs_by_content(self):
        k1 = llm_cache.make_key("test", 3, True, "内容A")
        k2 = llm_cache.make_key("test", 3, True, "内容B")
        self.assertNotEqual(k1, k2)

    def test_clear_cache(self):
        llm_cache.cache_set(llm_cache.make_key("a"), "1")
        llm_cache.cache_set(llm_cache.make_key("b"), "2")
        self.assertEqual(llm_cache.clear_cache(), 2)
        self.assertIsNone(llm_cache.cache_get(llm_cache.make_key("a")))


if __name__ == "__main__":
    unittest.main()
