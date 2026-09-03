import csv
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

from unbundle.record_types import Adjustment, BankLine, Order, Payment, Settlement
from unbundle.money import parse_amount

Record = TypeVar("Record")


@dataclass(frozen=True, slots=True)
class Loaded:
    orders: tuple[Order, ...]
    payments: tuple[Payment, ...]
    settlements: tuple[Settlement, ...]
    bank_lines: tuple[BankLine, ...]
    adjustments: tuple[Adjustment, ...]

# An export is edited by hand before a run reads the file, so a row can be missing a column or have a value no field accepts and the file name and 
# row number name the damaged row
class RowError(Exception):
    def __init__(self, path: Path, number: int, reason: str) -> None:
        super().__init__(f"{path.name} row {number}, {reason}")

# An empty cell is a null and not a value, a missing settlement id means the payment has not settled and an empty string would quietly become a real id
def _optional(cell: str) -> str | None:
    return cell or None

def load(directory: Path) -> Loaded:
    return Loaded(
        orders=_read(directory / "orders.csv", _order),
        payments=_read(directory / "payments.csv", _payment),
        settlements=_read(directory / "settlements.csv", _settlement),
        bank_lines=_read(directory / "bank_statement.csv", _bank_line),
        adjustments=_read(directory / "adjustments.csv", _adjustment),
    )

# A damaged row stops the run rather than being skipped, the rule parse_amount follows
def _read(path: Path, build: Callable[[Mapping[str, str]], Record]) -> tuple[Record, ...]:
    records = []
    for number, row in _rows(path):
        try:
            records.append(build(row))
        except KeyError as error:
            raise RowError(path, number, f"no column named {error}") from error
        except ValueError as error:
            raise RowError(path, number, str(error)) from error
    return tuple(records)

def _rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        # The header is row 1, and counting from 2 matches what a spreadsheet shows
        for number, row in enumerate(reader, start=2):
            # A short row is padded with None and a long row's extra values land under the None key, and a padded settlement_id is indistinguishable from an
            # empty cell, so a payment that lost its last column would load as a payment that never settled
            if None in row:
                raise RowError(path, number, "more values than the header names")
            missing = [name for name, value in row.items() if value is None]
            if missing:
                raise RowError(path, number, f"no value for {', '.join(missing)}")
            yield number, row

def _order(row: Mapping[str, str]) -> Order:
    return Order(
        order_ref=row["order_ref"],
        order_id=_optional(row["order_id"]),
        receipt=_optional(row["receipt"]),
        placed_at=datetime.fromisoformat(row["placed_at"]),
        amount=parse_amount(row["amount"]),
        status=row["status"],
        attempts=int(row["attempts"]),
    )

def _payment(row: Mapping[str, str]) -> Payment:
    return Payment(
        payment_id=row["payment_id"],
        order_id=row["order_id"],
        order_receipt=_optional(row["order_receipt"]),
        happened_at=datetime.fromisoformat(row["happened_at"]),
        status=row["status"],
        method=row["method"],
        card_network=_optional(row["card_network"]),
        card_type=_optional(row["card_type"]),
        amount=parse_amount(row["amount"]),
        fee=parse_amount(row["fee"]),
        tax=parse_amount(row["tax"]),
        settlement_id=_optional(row["settlement_id"]),
    )

def _settlement(row: Mapping[str, str]) -> Settlement:
    return Settlement(
        settlement_id=row["settlement_id"],
        utr=row["utr"],
        settled_at=datetime.fromisoformat(row["settled_at"]),
        amount=parse_amount(row["amount"]),
        fees=parse_amount(row["fees"]),
        tax=parse_amount(row["tax"]),
        status=row["status"],
    )

def _bank_line(row: Mapping[str, str]) -> BankLine:
    return BankLine(
        txn_date=date.fromisoformat(row["txn_date"]),
        narration=row["narration"],
        credit=parse_amount(row["credit"]),
        debit=parse_amount(row["debit"]),
    )

def _adjustment(row: Mapping[str, str]) -> Adjustment:
    return Adjustment(
        adjustment_id=row["adjustment_id"],
        kind=row["kind"],
        payment_id=row["payment_id"],
        amount=parse_amount(row["amount"]),
        raised_at=datetime.fromisoformat(row["raised_at"]),
        settlement_id=_optional(row["settlement_id"]),
    )