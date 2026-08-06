# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import chat_memory


class ChatMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_file = chat_memory.CHAT_MEMORY_FILE
        chat_memory.CHAT_MEMORY_FILE = os.path.join(self.tmp, "chat_memory.json")

    def tearDown(self):
        chat_memory.CHAT_MEMORY_FILE = self.orig_file

    def test_add_and_get_history(self):
        chat_memory.add_turn("u1", "user", "你好")
        chat_memory.add_turn("u1", "assistant", "你好，有什么可以帮你？")
        history = chat_memory.get_history("u1")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[1]["text"], "你好，有什么可以帮你？")

    def test_history_trimmed(self):
        for i in range(20):
            chat_memory.add_turn("u2", "user", f"q{i}")
            chat_memory.add_turn("u2", "assistant", f"a{i}")
        history = chat_memory.get_history("u2")
        self.assertLessEqual(len(history), chat_memory.MAX_TURNS * 2)

    def test_clear_history(self):
        chat_memory.add_turn("u3", "user", "x")
        removed = chat_memory.clear_history("u3")
        self.assertEqual(removed, 1)
        self.assertEqual(chat_memory.get_history("u3"), [])


if __name__ == "__main__":
    unittest.main()
