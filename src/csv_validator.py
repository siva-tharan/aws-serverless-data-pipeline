"""Validation for incoming order CSV files.

Pure Python with no AWS dependencies, so it can be unit-tested locally.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

REQUIRED_COLUMNS = ("order_id", "customer_name", "product", "quantity", "unit_price", "order_date")
MAX_ROWS = 10_000
MAX_PRICE = Decimal("99999999.99")


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_name: str
    product: str
    quantity: int
    unit_price: Decimal
    order_date: date


class CsvFormatError(ValueError):
    """The file as a whole can't be processed (no header, missing columns, too many rows)."""


def parse_orders(text: str) -> tuple[list[Order], list[str]]:
    """Parse CSV text into valid orders plus a list of human-readable row errors.

    Bad rows are reported and skipped, so one typo doesn't reject a whole file.
    Problems with the file itself raise CsvFormatError.
    """
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CsvFormatError("file is empty")

    header = [(name or "").strip().lower() for name in reader.fieldnames]
    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise CsvFormatError(f"missing column(s): {', '.join(missing)}")
    reader.fieldnames = header

    orders: list[Order] = []
    errors: list[str] = []
    seen_ids: set[str] = set()

    for row_count, row in enumerate(reader, start=1):
        if row_count > MAX_ROWS:
            raise CsvFormatError(f"more than {MAX_ROWS} data rows")
        line = reader.line_num
        try:
            order = _parse_row(row)
        except ValueError as exc:
            errors.append(f"line {line}: {exc}")
            continue
        if order.order_id in seen_ids:
            errors.append(f"line {line}: duplicate order_id {order.order_id}")
            continue
        seen_ids.add(order.order_id)
        orders.append(order)

    return orders, errors


def _parse_row(row: dict) -> Order:
    values = {col: (row.get(col) or "").strip() for col in REQUIRED_COLUMNS}

    for col, value in values.items():
        if not value:
            raise ValueError(f"{col} is empty")

    _check_length(values, "order_id", 32)
    _check_length(values, "customer_name", 100)
    _check_length(values, "product", 100)

    try:
        quantity = int(values["quantity"])
    except ValueError:
        raise ValueError(f"quantity '{values['quantity']}' is not a whole number") from None
    if quantity <= 0:
        raise ValueError("quantity must be greater than 0")

    try:
        unit_price = Decimal(values["unit_price"])
    except InvalidOperation:
        raise ValueError(f"unit_price '{values['unit_price']}' is not a number") from None
    if not unit_price.is_finite() or unit_price < 0 or unit_price > MAX_PRICE:
        raise ValueError(f"unit_price '{values['unit_price']}' is out of range")

    try:
        order_date = date.fromisoformat(values["order_date"])
    except ValueError:
        raise ValueError(f"order_date '{values['order_date']}' must be YYYY-MM-DD") from None

    return Order(
        order_id=values["order_id"],
        customer_name=values["customer_name"],
        product=values["product"],
        quantity=quantity,
        unit_price=unit_price.quantize(Decimal("0.01")),
        order_date=order_date,
    )


def _check_length(values: dict, col: str, limit: int) -> None:
    if len(values[col]) > limit:
        raise ValueError(f"{col} is longer than {limit} characters")
