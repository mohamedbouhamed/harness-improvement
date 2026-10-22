import unittest

from teamnotes.api import TeamNotesApp
from teamnotes.db import connect, create_user


class Functional(unittest.TestCase):
    def setUp(self):
        self.conn = connect()
        self.app = TeamNotesApp(self.conn)
        self.alice = {"user_id": create_user(self.conn, "alice", "Alice")}
        self.bob = {"user_id": create_user(self.conn, "bob", "Bob")}
        self.app.handle_create_note(self.alice, {"title": "Plan", "body": "q3 plan"})
        self.app.handle_create_note(self.alice, {"title": "apples", "body": "50% off"})
        self.app.handle_create_note(self.bob, {"title": "Bob only", "body": "x"})

    def test_list_search_sort(self):
        self.assertEqual(len(self.app.handle_list(self.alice, {})[1]["notes"]), 2)
        titles = [n["title"] for n in self.app.handle_list(self.alice, {"sort": "title"})[1]["notes"]]
        self.assertEqual(titles, ["apples", "Plan"])
        self.assertEqual([n["title"] for n in self.app.handle_search(self.alice, {"q": "50%"})[1]["notes"]], ["apples"])
        self.assertEqual(self.app.handle_update_profile(self.alice, {"sort": "id; --"})[0], 400)

    def test_activity(self):
        self.assertEqual(self.app.handle_activity(self.alice, {})[1]["count"], 2)
        self.app.handle_update_profile(self.alice, {"display_name": "Alice M"})
        self.assertEqual(self.app.handle_activity(self.alice, {})[1]["titles"], ["Plan", "apples"])
