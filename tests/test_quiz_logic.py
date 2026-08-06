# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quiz_logic import extract_answer_keys, grade_paper, parse_quiz_options


class QuizLogicTest(unittest.TestCase):
    def test_extract_answer_keys_formats(self):
        cases = [
            ("1. \u6b63\u786e\u7b54\u6848\uff1aB\u3002\n2. \u6b63\u786e\u7b54\u6848\uff1aC\u3002", {1: "B", 2: "C"}),
            ("**1. \u6b63\u786e\u7b54\u6848\uff1aB**", {1: "B"}),
            ("1\uff1a\u6b63\u786e\u7b54\u6848 B", {1: "B"}),
            ("1. B. \u89e3\u6790", {1: "B"}),
            ("1. \u9009B", {1: "B"}),
            ("\u7b2c1\u9898 \u6b63\u786e\u7b54\u6848\uff1aB", {1: "B"}),
            ("1. \u6b63\u786e\u7b54\u6848\uff1a**B**\u3002", {1: "B"}),
            ("\u30101\u3011\u6b63\u786e\u7b54\u6848\uff1aB", {1: "B"}),
        ]
        for text, expected in cases:
            self.assertEqual(extract_answer_keys(text), expected, text)

    def test_extract_judge_and_multi_keys(self):
        cases = [
            ("1. \u7b54\u6848\uff1a\u5bf9", {1: "\u5bf9"}),
            ("1. \u6b63\u786e\u7b54\u6848\uff1a\u6b63\u786e", {1: "\u5bf9"}),
            ("1. \u7b54\u6848\uff1a\u9519", {1: "\u9519"}),
            ("2. \u7b54\u6848\uff1aABD", {2: "ABD"}),
            ("1. \u7b54\u6848\uff1aA\u3001B\u3001D", {1: "ABD"}),
        ]
        for text, expected in cases:
            self.assertEqual(extract_answer_keys(text), expected, text)

    def test_grade_judge_questions(self):
        paper = self._paper(keys={1: "\u5bf9", 2: "\u9519"})
        paper["q"] = "1. q1\n2. q2\n"
        paper["a"] = "1. \u7b54\u6848\uff1a\u5bf9\n2. \u7b54\u6848\uff1a\u9519"
        r = grade_paper(paper, "\u6b63\u786e,\u9519")
        self.assertEqual((r["correct"], r["graded"]), (2, 2))
        r2 = grade_paper(paper, "T,F")
        self.assertEqual(r2["correct"], 2)
        r3 = grade_paper(paper, "\u5bf9,\u5bf9")
        self.assertEqual(r3["wrong_nos"], [2])

    def test_grade_multi_choice(self):
        paper = self._paper(keys={1: "ABD", 2: "C"})
        paper["q"] = "1. q1\nA. a B. b C. c D. d\n2. q2\nA. a B. b C. c\n"
        paper["a"] = "1. \u7b54\u6848\uff1aABD\n2. \u7b54\u6848\uff1aC"
        r = grade_paper(paper, "ABD,C")
        self.assertEqual((r["correct"], r["graded"]), (2, 2))
        r2 = grade_paper(paper, "1:ABD 2:C")
        self.assertEqual(r2["correct"], 2)
        r3 = grade_paper(paper, "ABD,B")
        self.assertEqual(r3["wrong_nos"], [2])

    def test_parse_quiz_options(self):
        q = "1. q\n**A. \u9009\u9879\u7532**\nB\u3001\u9009\u9879\u4e19\nC. \u9009\u9879\u4e01"
        opts = parse_quiz_options(q)
        self.assertEqual([o["key"] for o in opts], ["A", "B", "C"])
        self.assertEqual(opts[0]["text"], "\u9009\u9879\u7532")

    def _paper(self, keys=None):
        return {
            "q": "1. q1\nA. a B. b C. c\n2. q2\nA. a B. b C. c\n3. q3\nA. a B. b C. c\n",
            "a": "1. \u6b63\u786e\u7b54\u6848\uff1aB\u3002\n2. \u6b63\u786e\u7b54\u6848\uff1aA\u3002\n3. \u6b63\u786e\u7b54\u6848\uff1aC\u3002\n",
            "keys": keys if keys is not None else {1: "B", 2: "A", 3: "C"},
        }

    def test_grade_sequential(self):
        r = grade_paper(self._paper(), "B,A,C")
        self.assertTrue(r["ok"])
        self.assertEqual((r["correct"], r["graded"], r["wrong_nos"]), (3, 3, []))

    def test_grade_with_unanswered_placeholder(self):
        r = grade_paper(self._paper(), "B,X,C")
        self.assertTrue(r["ok"])
        self.assertEqual(r["wrong_nos"], [2])
        self.assertIn("\u672a\u4f5c\u7b54", r["reply"])

    def test_grade_pairs_order_independent(self):
        r = grade_paper(self._paper(), "3:C 1:B 2:A")
        self.assertTrue(r["ok"])
        self.assertEqual(r["correct"], 3)

    def test_grade_wrong_answers(self):
        r = grade_paper(self._paper(), "A,A,A")
        self.assertTrue(r["ok"])
        self.assertEqual(r["wrong_nos"], [1, 3])
        self.assertEqual(r["correct"], 1)

    def test_grade_empty_keys_treats_all_as_short(self):
        r = grade_paper(self._paper(keys={}), "B,A,C")
        self.assertTrue(r["ok"])
        self.assertEqual(r["graded"], 0)
        self.assertEqual(len(r["short_items"]), 3)
        self.assertEqual(r["short_items"][0]["user"], "B")

    def test_grade_mixed_choice_and_short(self):
        paper = self._paper()
        paper["q"] += "4. \u7b80\u7b54\u9898\n"
        paper["a"] += "4. \u53c2\u8003\u7b54\u6848\uff1a\u89e3\u6790"
        r = grade_paper(paper, "B,A,C,\u6211\u7684\u7406\u89e3")
        self.assertTrue(r["ok"])
        self.assertEqual(r["graded"], 3)
        self.assertEqual(r["correct"], 3)
        self.assertEqual(len(r["short_items"]), 1)
        self.assertEqual(r["short_items"][0]["no"], 4)

    def test_grade_nothing_submitted(self):
        r = grade_paper(self._paper(), "")
        self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
