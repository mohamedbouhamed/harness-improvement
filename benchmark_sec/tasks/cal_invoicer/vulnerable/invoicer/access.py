"""Access rules: members act on their own invoices, admins on all."""


class AccessDenied(Exception):
    pass


class NotFound(Exception):
    pass


def is_admin(user: dict) -> bool:
    return user.get("role") == "admin"


def load_owned(store, user: dict, invoice_id: int):
    invoice = store.get(invoice_id)
    if invoice is None:
        raise NotFound(invoice_id)
    if invoice.owner != user["name"] and not is_admin(user):
        raise NotFound(invoice_id)  # do not reveal that the invoice exists
    return invoice


def visible_invoices(store, user: dict) -> list:
    return store.all() if is_admin(user) else store.list_for(user["name"])
