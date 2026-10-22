"""A small arithmetic evaluator: numbers, variables, + - * / // % **, comparisons, min/max/round."""
import ast
import operator

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
        ast.Eq: operator.eq, ast.NotEq: operator.ne}
_FUNCS = {"min": min, "max": max, "round": round}


class ExprError(ValueError):
    pass


def evaluate(source: str, variables: dict):
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise ExprError(f"syntax error: {exc.msg}") from None
    return _eval(tree.body, variables)


def _eval(node, env):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise ExprError(f"unknown variable {node.id}")
        return env[node.id]
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        right = _eval(node.right, env)
        if isinstance(node.op, ast.Pow) and abs(right) > 4:
            raise ExprError("exponent too large")
        return _BIN[type(node.op)](_eval(node.left, env), right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_eval(node.operand, env)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and type(node.ops[0]) in _CMP:
        return _CMP[type(node.ops[0])](_eval(node.left, env), _eval(node.comparators[0], env))
    if isinstance(node, ast.IfExp):
        return _eval(node.body, env) if _eval(node.test, env) else _eval(node.orelse, env)
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and not node.keywords):
        return _FUNCS[node.func.id](*[_eval(a, env) for a in node.args])
    raise ExprError(f"unsupported expression: {type(node).__name__}")
