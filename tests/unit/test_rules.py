import json
from pathlib import Path
import unittest

from backend.app.gt.rules import classify, density_from_count, level_from_values

ROOT = Path(__file__).resolve().parents[2]


class RuleContractTests(unittest.TestCase):
    def test_business_fixtures(self):
        cases = json.loads((ROOT / "tests/fixtures/rule_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            with self.subTest(case=case["id"]):
                operation = case["operation"]
                if operation == "level":
                    result = level_from_values(**case["input"])
                elif operation == "density_from_count":
                    result = density_from_count(**case["input"])
                else:
                    result = classify(case["input"], operation)
                for key, expected in case["expected"].items():
                    self.assertEqual(result[key], expected)

    def test_non_finite_values_never_classify(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            for quantity in ("density", "speed"):
                with self.subTest(value=value, quantity=quantity):
                    self.assertEqual(classify(value, quantity)["reason"], "NON_FINITE_VALUE")


if __name__ == "__main__":
    unittest.main()
