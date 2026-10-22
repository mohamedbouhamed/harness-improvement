import unittest

from candidate import parse_duration


class PublicTests(unittest.TestCase):
    def test_hours_and_minutes(self):
        self.assertEqual(parse_duration("1h30m"), 5400)

    def test_seconds_only(self):
        self.assertEqual(parse_duration("45s"), 45)

    def test_number_without_unit_rejected(self):
        with self.assertRaises(ValueError):
            parse_duration("1h30")


if __name__ == "__main__":
    unittest.main()
