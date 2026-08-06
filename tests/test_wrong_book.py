# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wrong_book


class WrongBookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_file = wrong_book.WRONG_BOOK_FILE
        wrong_book.WRONG_BOOK_FILE = os.path.join(self.tmp, "wrong_book.json")

    def tearDown(self):
        wrong_book.WRONG_BOOK_FILE = self.orig_file

    def test_add_list_get_clear(self):
        q = "1. q1\n2. q2\n3. q3"
        a = "1. \u6b63\u786e\u7b54\u6848\uff1aB\n2. \u6b63\u786e\u7b54\u6848\uff1aA\n3. \u6b63\u786e\u7b54\u6848\uff1aC"
        self.assertEqual(wrong_book.add_wrong_paper(3, "\u5fb7\u8bed", q, a, [1, 3]), 2)
        items = wrong_book.get_wrong(3)
        self.assertEqual([it["no"] for it in items], [1, 3])
        self.assertEqual(wrong_book.get_wrong(3)[0]["q"], "1. q1")

        lst = wrong_book.list_wrong()
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["aid"], 3)
        self.assertEqual(lst[0]["count"], 2)

        # 清除指定题号
        self.assertEqual(wrong_book.clear_wrong(3, [1]), 1)
        self.assertEqual([it["no"] for it in wrong_book.get_wrong(3)], [3])
        # 清空整卷
        self.assertEqual(wrong_book.clear_wrong(3), 1)
        self.assertEqual(wrong_book.list_wrong(), [])

    def test_add_wrong_whole_paper_when_no_numbers(self):
        q = "1. q1\n2. q2"
        a = "1. A\n2. B"
        self.assertEqual(wrong_book.add_wrong_paper(7, "S", q, a, None), 2)
        self.assertEqual(len(wrong_book.get_wrong(7)), 2)

    def test_split_numbered_formats(self):
        text = "**1. \u9898\u76ee\u4e00**\n2\uff1a\u9898\u76ee\u4e8c\n3. \u9898\u76ee\u4e09"
        sections = wrong_book.split_numbered(text)
        self.assertEqual([n for n, _ in sections], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
