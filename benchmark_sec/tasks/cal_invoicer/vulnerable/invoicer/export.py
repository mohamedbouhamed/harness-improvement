"""Export formats."""
import csv
import io


def as_csv(invoice) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["invoice", "customer", "description", "quantity", "unit_price", "status"])
    for desc, qty, price in invoice.lines:
        writer.writerow([invoice.id, invoice.customer, desc, qty, f"{price / 100:.2f}", invoice.status])
    return buf.getvalue()


def as_text(invoice) -> str:
    rows = [f"Invoice #{invoice.id} for {invoice.customer} ({invoice.status})"]
    rows += [f"  {desc}: {qty} x {price / 100:.2f}" for desc, qty, price in invoice.lines]
    rows.append(f"Total: {invoice.total_cents / 100:.2f}")
    return "\n".join(rows)


FORMATS = {"csv": as_csv, "text": as_text}
