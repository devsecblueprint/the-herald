"""
A small evaluator for the DynamoDB condition expressions this feature uses.

The in-memory table double is only worth having if it actually enforces the
guards the production code relies on -- the claim race, the roster's
revision check, the state machine's status guards -- so it parses and
evaluates them for real rather than waving them through.
"""

import re
from typing import Any, Dict, Mapping, Optional

TOKEN_RE = re.compile(r"\s*(<>|<=|>=|=|<|>|\(|\)|,|[#:\w.\[\]-]+)")

COMPARISONS = {
    "=": lambda left, right: left == right,
    "<>": lambda left, right: left != right,
    "<": lambda left, right: left < right,
    "<=": lambda left, right: left <= right,
    ">": lambda left, right: left > right,
    ">=": lambda left, right: left >= right,
}

MISSING = object()


def tokenize(expression: str):
    """Split a condition expression into tokens."""
    tokens = []
    position = 0
    while position < len(expression):
        match = TOKEN_RE.match(expression, position)
        if not match:
            if expression[position].isspace():
                position += 1
                continue
            raise ValueError(f"Cannot tokenize condition at {expression[position:]!r}")
        tokens.append(match.group(1))
        position = match.end()
    return tokens


class ConditionEvaluator:
    """Recursive-descent evaluation of a DynamoDB condition expression."""

    def __init__(
        self,
        item: Optional[Mapping[str, Any]],
        names: Optional[Mapping[str, str]] = None,
        values: Optional[Mapping[str, Any]] = None,
    ):
        self.item = item if item is not None else {}
        self.exists = item is not None
        self.names = dict(names or {})
        self.values = dict(values or {})
        self.tokens = []
        self.position = 0

    def evaluate(self, expression: str) -> bool:
        """Evaluate an expression against the item."""
        self.tokens = tokenize(expression)
        self.position = 0
        result = self._or()
        if self.position != len(self.tokens):
            raise ValueError(
                f"Trailing tokens in condition: {self.tokens[self.position:]}"
            )
        return result

    # -- grammar -----------------------------------------------------------

    def _or(self) -> bool:
        result = self._and()
        while self._peek_upper() == "OR":
            self.position += 1
            # No short-circuit: the right side must still parse.
            result = self._and() or result
        return result

    def _and(self) -> bool:
        result = self._not()
        while self._peek_upper() == "AND":
            self.position += 1
            result = self._not() and result
        return result

    def _not(self) -> bool:
        if self._peek_upper() == "NOT":
            self.position += 1
            return not self._not()
        return self._primary()

    def _primary(self) -> bool:
        token = self._peek()
        if token == "(":
            self.position += 1
            result = self._or()
            self._expect(")")
            return result

        if token and token.lower() in ("attribute_exists", "attribute_not_exists"):
            self.position += 1
            self._expect("(")
            path = self._resolve_name(self._next())
            self._expect(")")
            present = self.exists and path in self.item
            return present if token.lower() == "attribute_exists" else not present

        left = self._operand()
        operator = self._next()
        if operator not in COMPARISONS:
            raise ValueError(f"Unsupported operator {operator!r}")
        right = self._operand()

        if left is MISSING or right is MISSING:
            return False
        try:
            return COMPARISONS[operator](left, right)
        except TypeError:
            return False

    def _operand(self):
        token = self._next()
        if token.startswith(":"):
            if token not in self.values:
                raise ValueError(f"Undefined expression value {token}")
            return self.values[token]
        path = self._resolve_name(token)
        if not self.exists or path not in self.item:
            return MISSING
        return self.item[path]

    # -- helpers -----------------------------------------------------------

    def _resolve_name(self, token: str) -> str:
        if token.startswith("#"):
            if token not in self.names:
                raise ValueError(f"Undefined expression name {token}")
            return self.names[token]
        return token

    def _peek(self) -> Optional[str]:
        if self.position >= len(self.tokens):
            return None
        return self.tokens[self.position]

    def _peek_upper(self) -> Optional[str]:
        token = self._peek()
        return token.upper() if token else None

    def _next(self) -> str:
        token = self._peek()
        if token is None:
            raise ValueError("Unexpected end of condition expression")
        self.position += 1
        return token

    def _expect(self, expected: str) -> None:
        token = self._next()
        if token != expected:
            raise ValueError(f"Expected {expected!r} in condition, got {token!r}")


def evaluate_condition(
    expression: str,
    item: Optional[Mapping[str, Any]],
    names: Optional[Mapping[str, str]] = None,
    values: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Evaluate a condition expression against an item (None = absent)."""
    return ConditionEvaluator(item, names, values).evaluate(expression)


def apply_update(
    item: Dict[str, Any],
    expression: str,
    names: Optional[Mapping[str, str]] = None,
    values: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply a ``SET a = :v, b = :w`` update expression to an item."""
    stripped = expression.strip()
    if not stripped.upper().startswith("SET "):
        raise ValueError(f"Only SET updates are supported, got {expression!r}")

    names = dict(names or {})
    values = dict(values or {})

    for assignment in stripped[4:].split(","):
        target, _, source = assignment.partition("=")
        target = target.strip()
        source = source.strip()
        attribute = names.get(target, target) if target.startswith("#") else target
        if not source.startswith(":"):
            raise ValueError(
                f"Only value assignments are supported, got {assignment!r}"
            )
        if source not in values:
            raise ValueError(f"Undefined expression value {source}")
        item[attribute] = values[source]

    return item
