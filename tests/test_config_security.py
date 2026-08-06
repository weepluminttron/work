# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


class ConfigSecurityTest(unittest.TestCase):
    def setUp(self):
        self.orig_admin = config.ADMIN_PASSWORD
        self.orig_web = config.WEB_PASSWORD

    def tearDown(self):
        config.ADMIN_PASSWORD = self.orig_admin
        config.WEB_PASSWORD = self.orig_web

    def test_admin_password_priority(self):
        config.WEB_PASSWORD = "web123"
        config.ADMIN_PASSWORD = "admin456"
        self.assertTrue(config.check_admin_password("admin456"))
        self.assertFalse(config.check_admin_password("web123"))
        self.assertFalse(config.check_admin_password(""))

    def test_fallback_to_web_password(self):
        config.ADMIN_PASSWORD = ""
        config.WEB_PASSWORD = "web123"
        self.assertTrue(config.check_admin_password("web123"))
        self.assertFalse(config.check_admin_password("nope"))


if __name__ == "__main__":
    unittest.main()
