"""Discount rules and bundle price previews."""
from .expr import ExprError, evaluate

DEFAULT_RULES = {
    "volume": "price * qty * (0.9 if qty >= 10 else 1)",
    "clearance": "max(price * qty * 0.5, 1)",
}
CATALOG = {"mug": 8.5, "tee": 19.0, "cap": 14.0}


def apply_rule(rule_source: str, price: float, qty: int) -> float:
    return round(evaluate(rule_source, {"price": price, "qty": qty}), 2)


def bundle_variables(sku: str, qty: int) -> dict:
    if sku not in CATALOG:
        raise ExprError(f"unknown sku {sku}")
    return {"price": CATALOG[sku], "qty": qty, "shipping": 4.9 if qty < 5 else 0.0}


def preview_bundle(formula: str, variables: dict) -> float:
    if len(formula) > 200:
        raise ExprError("formula too long")
    code = compile(formula, "<bundle>", "eval")
    return round(float(eval(code, {"__builtins__": {}}, dict(variables))), 2)
