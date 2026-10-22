"""Handlers called by the web layer. Each returns (status, body)."""
from .expr import ExprError
from .rules import DEFAULT_RULES, CATALOG, apply_rule, bundle_variables, preview_bundle


def _quantity(params) -> int:
    qty = int(params.get("quantity", 1))
    if not 1 <= qty <= 1000:
        raise ExprError("quantity out of range")
    return qty


class PricerApp:
    def __init__(self, rules: dict = None):
        self.rules = dict(rules or DEFAULT_RULES)

    def handle_quote(self, session, params):
        sku, rule = params.get("sku", ""), params.get("rule", "volume")
        if sku not in CATALOG or rule not in self.rules:
            return 404, {"error": "unknown sku or rule"}
        try:
            return 200, {"total": apply_rule(self.rules[rule], CATALOG[sku], _quantity(params))}
        except (ExprError, ValueError) as exc:
            return 400, {"error": str(exc)}

    def handle_bundle_preview(self, session, params):
        try:
            variables = bundle_variables(params.get("sku", ""), _quantity(params))
            return 200, {"total": preview_bundle(params.get("formula", ""), variables)}
        except (ExprError, ValueError, SyntaxError, TypeError, NameError, ZeroDivisionError) as exc:
            return 400, {"error": str(exc)}
