import unittest

from candidate import parse_duration


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_hours_and_minutes(self):
        self.assertEqual(parse_duration("1h30m"), 5400)

    def test_seconds_only(self):
        self.assertEqual(parse_duration("45s"), 45)

    def test_number_without_unit_rejected(self):
        with self.assertRaises(ValueError):
            parse_duration("1h30")

    def test_all_units(self):
        self.assertEqual(parse_duration("1d2h3m4s"), 86400 + 7200 + 180 + 4)

    def test_leading_zeros_and_zero(self):
        self.assertEqual(parse_duration("05m"), 300)
        self.assertEqual(parse_duration("0s"), 0)

    def test_large_number(self):
        self.assertEqual(parse_duration("90m"), 5400)

    def test_empty_rejected(self):
        with self.assertRaises(ValueError):
            parse_duration("")

    def test_out_of_order_rejected(self):
        with self.assertRaises(ValueError):
            parse_duration("30m1h")

    def test_repeated_unit_rejected(self):
        with self.assertRaises(ValueError):
            parse_duration("1h1h")

    def test_unit_without_number_rejected(self):
        with self.assertRaises(ValueError):
            parse_duration("h")

    def test_invalid_characters_rejected(self):
        for text in (" 1h", "1h ", "-5s", "+5s", "1H", "1w", "1.5h", "1h 30m"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_duration(text)


if __name__ == "__main__":
    unittest.main()
