import subprocess
import unittest
from unittest import mock

from mediakit.api import MediaApp


def fake_run(args, **kwargs):
    return subprocess.CompletedProcess(args, 0, stdout="PNG image data\n", stderr="")


class Functional(unittest.TestCase):
    def test_inspect_and_thumbnail(self):
        app = MediaApp()
        with mock.patch("subprocess.run", side_effect=fake_run):
            self.assertEqual(app.handle_inspect({}, {"filename": "cover.png"}),
                             (200, {"name": "cover.png", "type": "PNG image data"}))
            self.assertEqual(app.handle_inspect({}, {"filename": "a;b.png"})[0], 400)
            self.assertEqual(app.handle_thumbnail({}, {"filename": "cover.png", "size": "320x240"})[0], 201)
            self.assertEqual(app.handle_thumbnail({}, {"filename": "cover.png", "size": "big"})[0], 400)
            self.assertEqual(app.handle_status({}, {})[0], 200)
