"""Invoice records."""
from dataclasses import dataclass, field

STATUSES = ("draft", "sent", "paid", "void")


@dataclass
class Invoice:
    id: int
    owner: str
    customer: str
    lines: list = field(default_factory=list)  # (description, quantity, unit_price_cents)
    status: str = "draft"

    @property
    def total_cents(self) -> int:
        return sum(q * p for _, q, p in self.lines)


class InvoiceStore:
    def __init__(self):
        self._items, self._next = {}, 1

    def add(self, owner: str, customer: str, lines: list) -> Invoice:
        inv = Invoice(self._next, owner, customer, list(lines))
        self._items[inv.id] = inv
        self._next += 1
        return inv

    def get(self, invoice_id: int):
        return self._items.get(invoice_id)

    def list_for(self, owner: str) -> list:
        return [i for i in self._items.values() if i.owner == owner]

    def all(self) -> list:
        return list(self._items.values())
