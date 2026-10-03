"""T02 executable reference contract; no review, GT publication or T11 integration."""
from bisect import bisect_right
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "rules_v1.json"


def load_rules(path=CONFIG_PATH):
    return json.loads(Path(path).read_text(encoding="utf-8"), parse_float=Decimal)


def measurement(value, *, positive=False, integer=False):
    if value is None:
        return {"value": None, "status": "UNKNOWN", "reason": "MISSING_VALUE"}
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return {"value": None, "status": "INVALID", "reason": "NON_NUMERIC_VALUE"}
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return {"value": None, "status": "INVALID", "reason": "NON_NUMERIC_VALUE"}
    reason = None
    if not number.is_finite():
        reason = "NON_FINITE_VALUE"
    elif positive and number <= 0:
        reason = "AREA_NOT_POSITIVE"
    elif number < 0:
        reason = "NEGATIVE_VALUE"
    elif integer and number != number.to_integral_value():
        reason = "COUNT_NOT_INTEGER"
    return {"value": None if reason else number,
            "status": "INVALID" if reason else "VALID", "reason": reason}


def classify(value, quantity, rules=None):
    rules = rules or load_rules()
    result = measurement(value)
    result["class"] = None
    if result["status"] == "VALID":
        profile = rules[quantity]
        limits = [Decimal(str(v)) for v in profile["thresholds"]]
        result["class"] = profile["classes"][bisect_right(limits, result["value"])]
    return result


def density_from_count(count, area_m2):
    count_result = measurement(count, integer=True)
    area_result = measurement(area_m2, positive=True)
    for state in ("INVALID", "UNKNOWN"):
        for result in (count_result, area_result):
            if result["status"] == state:
                return result
    return measurement(count_result["value"] / area_result["value"])


def level_from_values(density, speed, rules=None):
    rules = rules or load_rules()
    d = classify(density, "density", rules)
    s = classify(speed, "speed", rules)
    if d["status"] != "VALID":
        return {"value": None, "status": d["status"], "reason": d["reason"], "possible_levels": []}
    # Invalid supplied speed is a data error, distinct from an absent speed.
    if s["status"] == "INVALID":
        return {"value": None, "status": "INVALID", "reason": s["reason"], "possible_levels": []}
    row = rules["level_matrix"][d["class"]]
    options = sorted(set(row.values())) if s["class"] is None else [row[s["class"]]]
    known = len(options) == 1
    return {"value": options[0] if known else None, "status": "VALID" if known else "UNKNOWN",
            "reason": None if known else "MISSING_VALUE", "possible_levels": options}
