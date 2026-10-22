"""Handlers called by the web layer. Each returns (status, body)."""
from .access import AccessDenied, NotFound, is_admin, load_owned, visible_invoices
from .export import FORMATS
from .store import STATUSES, InvoiceStore

ROLES = ("member", "admin")


def _invoice_id(params) -> int:
    try:
        return int(params.get("id", ""))
    except ValueError:
        raise NotFound(params.get("id")) from None


class InvoicerApp:
    def __init__(self, store: InvoiceStore = None):
        self.store = store or InvoiceStore()

    def handle_create(self, user, params):
        lines = [(d, int(q), int(p)) for d, q, p in params.get("lines", [])]
        inv = self.store.add(user["name"], params.get("customer", "").strip() or "unknown", lines)
        return 201, {"id": inv.id}

    def handle_list(self, user, params):
        return 200, [{"id": i.id, "customer": i.customer, "total": i.total_cents}
                     for i in visible_invoices(self.store, user)]

    def handle_view(self, user, params):
        try:
            inv = load_owned(self.store, user, _invoice_id(params))
        except NotFound:
            return 404, {"error": "not found"}
        return 200, {"id": inv.id, "customer": inv.customer, "lines": inv.lines, "status": inv.status}

    def handle_set_status(self, user, params):
        status = params.get("status")
        if status not in STATUSES:
            return 400, {"error": "invalid status"}
        try:
            inv = load_owned(self.store, user, _invoice_id(params))
            if status == "void" and not is_admin(user):
                raise AccessDenied("only admins can void invoices")
        except NotFound:
            return 404, {"error": "not found"}
        except AccessDenied as exc:
            return 403, {"error": str(exc)}
        inv.status = status
        return 200, {"id": inv.id, "status": inv.status}

    def handle_export(self, user, params):
        fmt = FORMATS.get(params.get("format", "csv"))
        if fmt is None:
            return 400, {"error": "unknown format"}
        if user.get("role") not in ROLES:
            return 403, {"error": "forbidden"}
        try:
            inv = self.store.get(_invoice_id(params))
        except NotFound:
            return 404, {"error": "not found"}
        if inv is None:
            return 404, {"error": "not found"}
        return 200, fmt(inv)
