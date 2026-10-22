import unittest

from harness_mvp.agent import extract_code, syntax_error
from harness_mvp.runner import parse_unittest_output


class ExtractCodeTests(unittest.TestCase):
    def test_single_python_fence(self):
        code, status = extract_code("```python\ndef f():\n    return 1\n```")
        self.assertEqual(status, "fenced")
        self.assertEqual(code, "def f():\n    return 1\n")

    def test_fence_with_surrounding_prose(self):
        code, status = extract_code("Here you go:\n```py\nx = 1\n```\nHope it helps.")
        self.assertEqual((code, status), ("x = 1\n", "fenced"))

    def test_untagged_fence(self):
        code, status = extract_code("```\nimport os\n```")
        self.assertEqual((code, status), ("import os\n", "fenced"))

    def test_multiple_fences_prefers_python_and_longest(self):
        text = "```text\nnot code at all, a long explanation here\n```\n```python\na = 1\n```\n```python\na = 1\nb = 2\n```"
        code, status = extract_code(text)
        self.assertEqual(status, "multiple_fences_longest")
        self.assertEqual(code, "a = 1\nb = 2\n")

    def test_unterminated_fence(self):
        code, status = extract_code("```python\ndef f():\n    return 2\n")
        self.assertEqual(status, "unterminated_fence")
        self.assertIn("return 2", code)

    def test_raw_module_without_fence(self):
        code, status = extract_code("def f(x):\n    return x\n")
        self.assertEqual(status, "raw")
        self.assertEqual(code, "def f(x):\n    return x\n")

    def test_prose_only_is_no_code(self):
        self.assertEqual(extract_code("I would sort the list first."), (None, "no_code"))
        self.assertEqual(extract_code(""), (None, "no_code"))
        self.assertEqual(extract_code("```python\n```"), (None, "no_code"))

    def test_syntax_error_detected_without_execution(self):
        self.assertIsNone(syntax_error("x = 1\n"))
        self.assertIn("line 1", syntax_error("def f(:\n  pass\n"))


class UnittestOutputTests(unittest.TestCase):
    def test_parse_failures(self):
        out = ("test_a (public_tests.PublicTests.test_a) ... ok\n"
               "======\nFAIL: test_b (public_tests.PublicTests.test_b)\n"
               "ERROR: test_c (public_tests.PublicTests.test_c)\n"
               "Ran 3 tests in 0.001s\n\nFAILED (failures=1, errors=1)\n")
        ran, failed = parse_unittest_output(out)
        self.assertEqual(ran, 3)
        self.assertEqual(failed, ["public_tests.PublicTests.test_b", "public_tests.PublicTests.test_c"])


if __name__ == "__main__":
    unittest.main()
