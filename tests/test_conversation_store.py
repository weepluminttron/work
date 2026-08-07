# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import conversation_store


class ConversationStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_file = conversation_store.CONVERSATIONS_FILE
        conversation_store.CONVERSATIONS_FILE = os.path.join(self.tmp, "conversations.json")

    def tearDown(self):
        conversation_store.CONVERSATIONS_FILE = self.orig_file

    def test_create_append_get(self):
        conv = conversation_store.create_conversation()
        self.assertEqual(conv["title"], "\u65b0\u5bf9\u8bdd")
        self.assertTrue(conversation_store.append_messages(conv["id"], "\u4eca\u5929\u5b66\u4e60\u5fb7\u8bed", "\u597d\u7684\uff0c\u6211\u4eec\u5f00\u59cb"))
        got = conversation_store.get_conversation(conv["id"])
        self.assertEqual(len(got["messages"]), 2)
        self.assertEqual(got["title"], "\u4eca\u5929\u5b66\u4e60\u5fb7\u8bed")

    def test_list_sorted_and_append_unknown(self):
        c1 = conversation_store.create_conversation()
        c2 = conversation_store.create_conversation()
        conversation_store.append_messages(c1["id"], "a", "b")
        conversation_store.append_messages(c2["id"], "x", "y")
        convs = conversation_store.list_conversations()
        self.assertEqual(len(convs), 2)
        self.assertFalse(conversation_store.append_messages("not_exist", "a", "b"))
        self.assertIsNone(conversation_store.get_conversation("not_exist"))

    def test_update_title(self):
        conv = conversation_store.create_conversation()
        self.assertTrue(conversation_store.update_title(conv["id"], "德语学习计划"))
        got = conversation_store.get_conversation(conv["id"])
        self.assertEqual(got["title"], "德语学习计划")
        self.assertTrue(got["auto_titled"])
        self.assertFalse(conversation_store.update_title("not_exist", "x"))

    def test_delete_conversation(self):
        conv = conversation_store.create_conversation()
        self.assertTrue(conversation_store.delete_conversation(conv["id"]))
        self.assertIsNone(conversation_store.get_conversation(conv["id"]))
        self.assertFalse(conversation_store.delete_conversation(conv["id"]))


if __name__ == "__main__":
    unittest.main()
