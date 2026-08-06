# -*- coding: utf-8 -*-
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import user_auth
import user_context


class UserAuthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.orig_file = user_auth.USERS_FILE
        user_auth.USERS_FILE = os.path.join(self.tmp, "users.json")

    def tearDown(self):
        user_auth.USERS_FILE = self.orig_file

    def test_ensure_admin_and_auth(self):
        user_auth.ensure_admin("admin", "admin123")
        user = user_auth.authenticate("admin", "admin123")
        self.assertIsNotNone(user)
        self.assertEqual(user["role"], "admin")
        self.assertIsNone(user_auth.authenticate("admin", "wrong"))

    def test_register(self):
        ok, _ = user_auth.register("alice", "pass123")
        self.assertTrue(ok)
        ok2, _ = user_auth.register("alice", "pass123")
        self.assertFalse(ok2)
        user = user_auth.authenticate("alice", "pass123")
        self.assertEqual(user["role"], "user")
        self.assertIsNone(user_auth.authenticate("alice", "bad"))

    def test_scope_per_user(self):
        orig_root = user_context.DATA_ROOT
        user_context.DATA_ROOT = self.tmp
        try:
            user_context.set_current_user("alice")
            self.assertEqual(user_context.scope("archive.db"), os.path.join(self.tmp, "alice__archive.db"))
            user_context.set_current_user("bob")
            self.assertEqual(user_context.scope("archive.db"), os.path.join(self.tmp, "bob__archive.db"))
        finally:
            user_context.DATA_ROOT = orig_root


if __name__ == "__main__":
    unittest.main()
