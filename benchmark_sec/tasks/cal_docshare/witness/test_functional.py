import os
import tempfile
import unittest

from docshare.api import DocshareApp
from docshare.config import load_settings


class Functional(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "policies"))
        with open(os.path.join(self.root, "docs", "policies", "leave.md"), "w") as fh:
            fh.write("leave policy")
        self.app = DocshareApp(load_settings({"DOCSHARE_ROOT": self.root}))
        self.session = {"user": "alice"}

    def test_download_and_browse(self):
        self.assertEqual(self.app.handle_download(self.session, {"name": "policies/leave.md"}), (200, b"leave policy"))
        status, body = self.app.handle_browse(self.session, {"folder": "policies"})
        self.assertEqual((status, [e["name"] for e in body["entries"]]), (200, ["leave.md"]))
        self.assertEqual(self.app.handle_browse(self.session, {})[0], 200)

    def test_plain_escape_is_blocked(self):
        self.assertEqual(self.app.handle_download(self.session, {"name": "../../etc/passwd"})[0], 404)

    def test_upload(self):
        status, body = self.app.handle_upload(self.session, {"filename": "../notes.txt", "content": b"x"})
        self.assertEqual(status, 201)
        self.assertTrue(body["stored"].endswith("-notes.txt"))
        self.assertEqual(self.app.handle_upload(self.session, {"filename": "a.exe", "content": b"x"})[0], 400)
