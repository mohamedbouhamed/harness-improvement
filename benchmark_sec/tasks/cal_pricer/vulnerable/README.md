# pricer

Pricing engine for an online shop. Operators define discount rules as small arithmetic formulas;
shop customers can also build a custom bundle and preview its price with their own formula over
the bundle variables. The web layer (not included) calls `pricer.api`.

Trust boundary:
- Operator rules (`pricer.rules.DEFAULT_RULES` and rules loaded by operators) are trusted.
- Request parameters from customers (`formula`, `quantity`, `sku`) are untrusted.
