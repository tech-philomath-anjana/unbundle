import csv
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import get_args

from unbundle.record_types import (
    Adjustment,
    BankLine,
    CardNetwork,
    CardType,
    Method,
    Order,
    Payment,
    Settlement,
    SettlementStatus,
)
from unbundle.money import Bps, Paise, apply_rate, format_amount
from unbundle.ground_truth import Label

DEFAULT_SEED = 20260828
WINDOW_END = date(2026, 8, 28)
WINDOW_DAYS = 28

# A chargeback has to be older than the window or the payment it names would be in the data and the assert stops the window growing past it
CHARGEBACK_MIN_AGE_DAYS = 60
assert WINDOW_DAYS < CHARGEBACK_MIN_AGE_DAYS

# From https://razorpay.com/blog/razorpay-0-percent-platform-fee-offer-90-days-new-merchants-2026/ dated 24 August 2026, the table 
# "The Standard Pricing Picture After the Window" gives the standard platform fee per payment method plus 18% GST on the platform fee
GST_BPS: Bps = 1800
CARD_NETWORK_RATE: dict[str, Bps] = {
    "Maestro": 200,
    "Visa": 200,
    "MasterCard": 200,
    "RuPay": 200,
    "American Express": 300,
    "Diners Club": 300,
}
METHOD_RATE: dict[str, Bps] = {"upi": 200, "netbanking": 200, "wallet": 200}
# A blank column is not debit so it would take the credit rate, which is three times the debit one and a card type with no rate stops the run instead
EMI_TYPE_RATE: dict[str, Bps] = {"debit": 100, "credit": 300}

# No rate is published for unknown so it is left out
assert set(CARD_NETWORK_RATE) == set(get_args(CardNetwork)) - {"unknown"}
assert set(EMI_TYPE_RATE) == set(get_args(CardType))

METHOD_WEIGHTS = (("upi", 55), ("card", 25), ("netbanking", 12), ("wallet", 5), ("emi", 3))
NETWORK_WEIGHTS = (
    ("Visa", 38),
    ("MasterCard", 28),
    ("RuPay", 24),
    ("Maestro", 5),
    ("American Express", 3),
    ("Diners Club", 2),
)
TYPE_WEIGHTS = (("debit", 62), ("credit", 38))

# Without the split one settlement carries a big part of the month's money in one credit, so a damaged narration on it leaves that much of the revenue unmatched
SETTLEMENT_CYCLES_PER_DAY = 3

# The planting rates are set high so that the matcher has something to catch, not generally a real world situation, at a lower rate a class can end up 
# with none of them in the file and the matcher has nothing to check against

ORDER_ID_MISSING_RATE = 0.08
ORDER_UNLINKED_RATE = 0.02

# The rates below apply once per payment, and an order can make a failed attempt, a capture and a second capture

MISSING_SETTLEMENT_RATE = 0.02
FEE_MISMATCH_RATE = 0.01

# From https://razorpay.com/docs/payments/payments/, an unsuccessful attempt is marked failed and the customer retries, so the failed one shares 
# the order id and carries no money and a matcher that counts payments without reading the status counts money that never came in
FAILED_RETRY_RATE = 0.04

# Two captured payments against one order, so the merchant was paid twice for one sale and the second one is real money that has to go back
DUPLICATE_PAYMENT_RATE = 0.01

# The refund rate applies only to a payment that reached a settlement
REFUND_RATE = 0.03

# Only card payments here, emi is priced off the card type so an emi payment can still be priced without the network name
NETWORK_UNKNOWN_RATE = 0.06

# The rates below apply once per settlement, and a day settles SETTLEMENT_CYCLES_PER_DAY times

# A failed settlement still names its payments but no credit is written for it, so a matcher reading the id and never the status 
# reports money the merchant never got
SETTLEMENT_FAILED_RATE = 0.03

# From https://razorpay.com/docs/payments/settlements/, Razorpay settles only what the balance covers and "we will only choose the ones that add up to 
# your current live balance", so a payment sits in the export naming a settlement whose transfer left it out. The credit still ties out against the reduced amount,
# so no step of the cascade sees anything wrong and the payment is reported as arrived
HELD_BACK_RATE = 0.06

# From https://razorpay.com/docs/api/settlements/fetch-recon/, a refund is settled as a debit inside a settlement, here the refund is against 
# a payment that settled in an earlier cycle so the credit is short and none of the payments in it explain why
LATE_REFUND_RATE = 0.10

# From https://razorpay.com/blog/chargebacks/, Deduct at Onset, the disputed amount is taken off when the dispute is raised and not when it is decided, 
# so a sale from months back reduces a credit in this run and the payment it names is in no file the merchant has
CHARGEBACK_RATE = 0.15

# The rates below apply once per settlement that made a credit, not the failed ones

MANGLED_UTR_RATE = 0.20
UNKNOWN_CREDIT_RATE = 0.15

# The rate was set when each day made one settlement, so a run had few settlements and a lower rate could end up with none of them in the file, 
# SETTLEMENT_CYCLES_PER_DAY now splits each day into more settlements, so the rate is higher than it needs to be
BANK_FEE_RATE = 0.30

# A stray credit with an unrelated amount would never look like a settlement, so these copy a real settlement's amount and date
HARD_NEGATIVE_RATE = 0.50

# The failures all sit on one date and inside these hours rather than spread across the month, so they group into one incident
OUTAGE_DATE = date(2026, 8, 19)
OUTAGE_FROM_HOUR = 14
OUTAGE_TO_HOUR = 16
OUTAGE_METHOD = "upi"

# The payment is charged at this rate and not the rate its own method agreed, 300 is a rate Razorpay charges so the fee looks normal and only 
# the agreed rate says it is wrong
WRONG_RATE: Bps = 300


@dataclass(frozen=True, slots=True)
class Dataset:
    orders: tuple[Order, ...]
    payments: tuple[Payment, ...]
    settlements: tuple[Settlement, ...]
    bank_lines: tuple[BankLine, ...]
    adjustments: tuple[Adjustment, ...]
    labels: tuple[Label, ...]
    # Which settlement each bank line really came from, held by the line's position because two lines can carry the same narration and this is the 
    # answer key so the matcher never reads it
    credit_source: tuple[tuple[int, str], ...]


# From https://razorpay.com/docs/payments/settlements/, T+2 counts working days and bank holidays are not working days, RBI lists bank holidays 
# per region so only weekends are skipped here, reconcile.py has its own copy because importing this one would pull the generator and its answer key into the matcher
def working_days_after(start: date, days: int) -> date:
    moved = start
    added = 0
    while added < days:
        moved += timedelta(days=1)
        if moved.weekday() < 5:
            added += 1
    return moved


def rate_for(method: Method, card_network: CardNetwork | None, card_type: CardType | None) -> Bps:
    if method == "card":
        return CARD_NETWORK_RATE[card_network]
    if method == "emi":
        return EMI_TYPE_RATE[card_type]
    return METHOD_RATE[method]


def _weighted(rng: random.Random, weights: tuple[tuple[str, int], ...]) -> str:
    names = [name for name, _ in weights]
    counts = [count for _, count in weights]
    return rng.choices(names, weights=counts, k=1)[0]


def _order_amount(rng: random.Random) -> Paise:
    bucket = rng.random()
    if bucket < 0.60:
        return rng.randint(10_000, 100_000)
    if bucket < 0.90:
        return rng.randint(100_000, 500_000)
    return rng.randint(500_000, 2_500_000)


def _utr(settled_on: date, sequence: int) -> str:
    return f"AXISN{settled_on:%y%m%d}{sequence:05d}"


# The settlements are numbered one after another, so adding one to the last digit lands on the next settlement's real UTR and the matcher reads a 
# damaged reference as an undamaged one, the digit keeps moving until it names no settlement at all
def _swapped_digit(utr: str, real_utrs: set[str]) -> str:
    for step in range(1, 10):
        candidate = utr[:-1] + str((int(utr[-1]) + step) % 10)
        if candidate not in real_utrs:
            return candidate
    raise ValueError(f"every digit swap on {utr} names a real settlement")


# The last three digits go and the two left standing are the top of a five digit pad, so they stay 00 until the sequence outgrows three digits and
# the date is all that survives, which names every settlement that day rather than one of them and the matcher has to refuse it
def _narration(rng: random.Random, utr: str, real_utrs: set[str]) -> tuple[str, bool]:
    if rng.random() < MANGLED_UTR_RATE:
        damage = rng.random()
        if damage < 0.34:
            return f"NEFT-{utr[:-3]}-RAZORPAY", True
        if damage < 0.67:
            return f"NEFT-{_swapped_digit(utr, real_utrs)}-RAZORPAY", True
        return "NEFT CR RAZORPAY SOFTWARE", True
    return f"NEFT-{utr}-RAZORPAY", False


def generate(seed: int = DEFAULT_SEED, order_count: int = 5_000) -> Dataset:
    # Rng is a Random instance, not the module level functions, so a stray global random call somewhere else cannot change this run
    rng = random.Random(seed)
    window_start = WINDOW_END - timedelta(days=WINDOW_DAYS)

    orders: list[Order] = []
    payments: list[Payment] = []
    labels: list[Label] = []
    agreed_rate_by_payment: dict[str, Bps] = {}

    for index in range(order_count):
        # randint is inclusive at both ends so the offset runs from 0 to WINDOW_DAYS and the window covers a day more than the constant names, dropping
        # that day would take the most recent captures out of the file and those are the ones still in flight when the run ends
        placed_at = datetime(
            window_start.year,
            window_start.month,
            window_start.day,
            rng.randint(8, 22),
            rng.randint(0, 59),
        ) + timedelta(days=rng.randint(0, WINDOW_DAYS))
        amount = _order_amount(rng)
        order_id = f"ord_{index:06d}"
        order_ref = f"SHOP-{index:06d}"
        # One roll decides the order id and the receipt, the second check adds the two rates so each one happens as often as it says and 
        # one order never gets both damages
        roll = rng.random()
        kept_order_id: str | None = order_id
        kept_receipt: str | None = order_ref
        if roll < ORDER_UNLINKED_RATE:
            kept_order_id = None
            kept_receipt = None
            labels.append(
                Label("UNLINKED_ORDER", order_ref, "no gateway id kept and no receipt set")
            )
        elif roll < ORDER_UNLINKED_RATE + ORDER_ID_MISSING_RATE:
            kept_order_id = None
            labels.append(
                Label("ORDER_ID_MISSING", order_ref, "gateway id not kept, receipt set")
            )
        failed_retry = rng.random() < FAILED_RETRY_RATE
        duplicate = rng.random() < DUPLICATE_PAYMENT_RATE
        orders.append(
            Order(
                order_ref=order_ref,
                order_id=kept_order_id,
                receipt=kept_receipt,
                placed_at=placed_at,
                amount=amount,
                status="paid",
                attempts=1 + failed_retry + duplicate,
            )
        )

        method: Method = _weighted(rng, METHOD_WEIGHTS)
        card_network: CardNetwork | None = None
        card_type: CardType | None = None
        if method in ("card", "emi"):
            card_network = _weighted(rng, NETWORK_WEIGHTS)
            card_type = _weighted(rng, TYPE_WEIGHTS)

        agreed_rate = rate_for(method, card_network, card_type)
        charged_rate = agreed_rate
        payment_id = f"pay_{index:06d}"
        if rng.random() < FEE_MISMATCH_RATE and agreed_rate != WRONG_RATE:
            charged_rate = WRONG_RATE
            labels.append(
                Label(
                    "FEE_MISMATCH",
                    payment_id,
                    f"{method} agreed at {agreed_rate}bps, charged at {charged_rate}bps",
                )
            )
        mdr = apply_rate(amount, charged_rate)
        tax = apply_rate(mdr, GST_BPS)
        agreed_rate_by_payment[payment_id] = agreed_rate

        # The fee was charged at the network the card really was and only the export loses the name, so the fee is right but there is nothing 
        # in the data to check it against
        exported_network = card_network
        if method == "card" and rng.random() < NETWORK_UNKNOWN_RATE:
            exported_network = "unknown"
            labels.append(
                Label(
                    "NETWORK_UNKNOWN",
                    payment_id,
                    f"charged at {charged_rate}bps, network not named in the export",
                )
            )

        happened_at = placed_at + timedelta(minutes=rng.randint(1, 30))
        payments.append(
            Payment(
                payment_id=payment_id,
                # From https://razorpay.com/docs/api/settlements/fetch-recon/, the gateway has the order id and the receipt the merchant entered, so the 
                # payment keeps the order id here and only the receipt goes missing
                order_id=order_id,
                order_receipt=kept_receipt,
                happened_at=happened_at,
                status="captured",
                method=method,
                card_network=exported_network,
                card_type=card_type,
                amount=amount,
                fee=mdr + tax,
                tax=tax,
                settlement_id=None,
            )
        )

        if failed_retry:
            payments.append(
                Payment(
                    payment_id=f"{payment_id}f",
                    order_id=order_id,
                    order_receipt=kept_receipt,
                    happened_at=happened_at - timedelta(minutes=rng.randint(1, 10)),
                    status="failed",
                    method=method,
                    card_network=exported_network,
                    card_type=card_type,
                    amount=amount,
                    fee=0,
                    tax=0,
                    settlement_id=None,
                )
            )
            labels.append(
                Label("FAILED_RETRY", f"{payment_id}f", f"failed attempt on {order_id}")
            )

        # A second capture is a real payment that settles like any other
        if duplicate:
            duplicate_id = f"{payment_id}b"
            agreed_rate_by_payment[duplicate_id] = agreed_rate
            payments.append(
                Payment(
                    payment_id=duplicate_id,
                    order_id=order_id,
                    order_receipt=kept_receipt,
                    happened_at=happened_at + timedelta(minutes=rng.randint(1, 60)),
                    status="captured",
                    method=method,
                    card_network=exported_network,
                    card_type=card_type,
                    amount=amount,
                    fee=mdr + tax,
                    tax=tax,
                    settlement_id=None,
                )
            )
            labels.append(
                Label("DUPLICATE_PAYMENT", duplicate_id, f"second capture on {order_id}")
            )
            # The second capture is given the same fee as the first, so an overcharge on the first is charged again here and labelling only the first
            # would score a real finding as a false one
            if charged_rate != agreed_rate:
                labels.append(
                    Label(
                        "FEE_MISMATCH",
                        duplicate_id,
                        f"{method} agreed at {agreed_rate}bps, charged at {charged_rate}bps",
                    )
                )
            if exported_network == "unknown":
                labels.append(
                    Label(
                        "NETWORK_UNKNOWN",
                        duplicate_id,
                        f"charged at {charged_rate}bps, network not named in the export",
                    )
                )

    settled, state_labels = _assign_settlement_dates(rng, payments)
    labels.extend(state_labels)

    settlements, payments, adjustments, settlement_labels = _build_settlements(
        rng, payments, settled, agreed_rate_by_payment
    )
    labels.extend(settlement_labels)

    bank_lines, bank_labels, credit_source = _build_bank_lines(rng, settlements)
    labels.extend(bank_labels)

    return Dataset(
        orders=tuple(orders),
        payments=tuple(payments),
        settlements=tuple(settlements),
        bank_lines=tuple(bank_lines),
        adjustments=tuple(adjustments),
        labels=tuple(labels),
        credit_source=tuple(credit_source),
    )


def _assign_settlement_dates(
    rng: random.Random, payments: list[Payment]
) -> tuple[dict[str, date], list[Label]]:
    settled: dict[str, date] = {}
    labels: list[Label] = []

    for payment in payments:
        # A failed attempt moved no money so nothing is owed on it, and only money that is owed can go missing
        if payment.status != "captured":
            continue

        due = working_days_after(payment.happened_at.date(), 2)

        # An outage payment still owes its two working days before anything is owed on it, so the
        # in flight test comes first. Labelling it missing while it is not yet due asserts money
        # gone that the export cannot show gone, and the matcher reading only the export is right
        if _in_outage(payment) and due <= WINDOW_END:
            labels.append(Label("MISSING_SETTLEMENT", payment.payment_id, f"due {due}, never settled"))
            labels.append(
                Label(
                    "GATEWAY_OUTAGE",
                    payment.payment_id,
                    f"{OUTAGE_METHOD} captured {payment.happened_at:%d %b %H:%M} during the outage",
                )
            )
            continue

        if due > WINDOW_END:
            labels.append(Label("IN_FLIGHT", payment.payment_id, f"due {due}, run ends {WINDOW_END}"))
            continue
        if rng.random() < MISSING_SETTLEMENT_RATE:
            labels.append(Label("MISSING_SETTLEMENT", payment.payment_id, f"due {due}, never settled"))
            continue

        settled[payment.payment_id] = due
        if (due - payment.happened_at.date()).days > 2:
            labels.append(
                Label("T_PLUS_TWO", payment.payment_id, f"weekend crossed, settles {due}")
            )

    return settled, labels


def _in_outage(payment: Payment) -> bool:
    captured = payment.happened_at
    return (
        payment.method == OUTAGE_METHOD
        and captured.date() == OUTAGE_DATE
        and OUTAGE_FROM_HOUR <= captured.hour < OUTAGE_TO_HOUR
    )


def _build_settlements(
    rng: random.Random,
    payments: list[Payment],
    settled: dict[str, date],
    agreed_rate_by_payment: dict[str, Bps],
) -> tuple[list[Settlement], list[Payment], list[Adjustment], list[Label]]:
    by_date: dict[date, list[Payment]] = {}
    for payment in payments:
        due = settled.get(payment.payment_id)
        if due is not None:
            by_date.setdefault(due, []).append(payment)

    cycles: list[tuple[date, list[Payment]]] = []
    for settled_on in sorted(by_date):
        due_today = by_date[settled_on]
        # The minus signs make it a ceiling divide, so ten payments over three cycles gives four, four and two across three cycles, where a 
        # floor divide gives three each and a fourth cycle with one payment in it
        size = max(1, -(-len(due_today) // SETTLEMENT_CYCLES_PER_DAY))
        for start in range(0, len(due_today), size):
            cycles.append((settled_on, due_today[start : start + size]))

    settlements: list[Settlement] = []
    adjustments: list[Adjustment] = []
    labels: list[Label] = []
    assigned: dict[str, str] = {}
    settled_earlier: list[Payment] = []

    for sequence, (settled_on, group) in enumerate(cycles):
        settlement_id = f"setl_{sequence:05d}"

        gross = sum(payment.amount for payment in group)
        fees = sum(payment.fee for payment in group)
        tax = sum(payment.tax for payment in group)

        deducted = 0
        # Each refund is capped at half its own payment so the total taken off here cannot reach gross minus fees, the two deductions below are against
        # payments outside this group and have no such cap, which is why they check the credit first
        for payment in group:
            if rng.random() < REFUND_RATE:
                refund = rng.randint(1_000, max(1_001, payment.amount // 2))
                adjustments.append(
                    Adjustment(
                        adjustment_id=f"adj_{len(adjustments):06d}",
                        kind="refund",
                        payment_id=payment.payment_id,
                        # The date is worked out from the payment's time, a refund happens after its payment and before the settlement it is taken off
                        raised_at=payment.happened_at + timedelta(hours=rng.randint(1, 24)),
                        amount=refund,
                        settlement_id=settlement_id,
                    )
                )
                deducted += refund

        # A refund larger than the settlement would make the credit negative and money here is never negative, so a refund that does not fit 
        # is never made and fewer of them happen than the rate says
        if settled_earlier and rng.random() < LATE_REFUND_RATE:
            older = rng.choice(settled_earlier)
            refund = rng.randint(1_000, max(1_001, older.amount // 2))
            if refund < gross - fees - deducted:
                adjustments.append(
                    Adjustment(
                        adjustment_id=f"adj_{len(adjustments):06d}",
                        kind="refund",
                        payment_id=older.payment_id,
                        raised_at=older.happened_at + timedelta(days=rng.randint(1, 5)),
                        amount=refund,
                        settlement_id=settlement_id,
                    )
                )
                deducted += refund
                labels.append(
                    Label(
                        "PARTIAL_REFUND",
                        settlement_id,
                        f"{format_amount(refund)} against {older.payment_id}, settled earlier",
                    )
                )

        if rng.random() < CHARGEBACK_RATE:
            disputed = rng.randint(50_000, 800_000)
            raised_on = settled_on - timedelta(days=rng.randint(CHARGEBACK_MIN_AGE_DAYS, 200))
            if disputed < gross - fees - deducted:
                adjustments.append(
                    Adjustment(
                        adjustment_id=f"adj_{len(adjustments):06d}",
                        kind="chargeback",
                        payment_id=f"pay_archived_{rng.randint(0, 999_999):06d}",
                        raised_at=datetime(raised_on.year, raised_on.month, raised_on.day, 10, 0),
                        amount=disputed,
                        settlement_id=settlement_id,
                    )
                )
                deducted += disputed
                labels.append(
                    Label(
                        "CHARGEBACK_LATER",
                        settlement_id,
                        f"{format_amount(disputed)} raised {raised_on}, sale before the window",
                    )
                )

        status: SettlementStatus = "processed"
        if rng.random() < SETTLEMENT_FAILED_RATE:
            status = "failed"

        # Only on a settlement that sent money, since a failed one held everything back and its payments are already labelled. Never the last payment in 
        # a cycle either, a settlement holding back its only member would send nothing and is the failed case under a different name
        held_back: Payment | None = None
        if status == "processed" and len(group) > 1 and rng.random() < HELD_BACK_RATE:
            candidate = rng.choice(group)
            if candidate.net < gross - fees - deducted:
                held_back = candidate

        settlements.append(
            Settlement(
                settlement_id=settlement_id,
                utr=_utr(settled_on, sequence),
                settled_at=datetime(settled_on.year, settled_on.month, settled_on.day, 11, 0),
                amount=gross - fees - deducted - (held_back.net if held_back else 0),
                fees=fees,
                tax=tax,
                status=status,
            )
        )

        if status == "failed":
            for payment in group:
                labels.append(
                    Label(
                        "MISSING_SETTLEMENT",
                        payment.payment_id,
                        f"assigned to {settlement_id}, which failed",
                    )
                )
        else:
            if len(group) > 1:
                # Assigned and not paid, because a settlement can hold one of its payments back and then the credit covers one fewer than it names
                labels.append(
                    Label("BATCHED", settlement_id, f"{len(group)} payments assigned to one credit")
                )
            labels.extend(_rounding_labels(group, settlement_id, agreed_rate_by_payment))
            if held_back is not None:
                labels.append(
                    Label(
                        "HELD_BACK",
                        held_back.payment_id,
                        f"assigned to {settlement_id}, left out of the transfer",
                    )
                )

        # The export still names the settlement, which is the whole of the damage, a matcher
        # reading the id and finding the credit has no reason to look further
        for payment in group:
            assigned[payment.payment_id] = settlement_id
        # A failed settlement moved no money so its payments never settled, and a late refund taken against one of them would say settled earlier
        # about a payment the same run labels missing
        if status != "failed":
            # A held back payment did not settle either, so a later refund drawn against it would
            # say settled earlier about money that never moved, which is the same contradiction
            # the failed settlement guard above exists to stop
            settled_earlier.extend(payment for payment in group if payment is not held_back)

    # Payment is frozen so settlement_id cannot be set after it is built, and the settlement is only known here
    rebuilt = [
        Payment(
            payment_id=payment.payment_id,
            order_id=payment.order_id,
            order_receipt=payment.order_receipt,
            happened_at=payment.happened_at,
            status=payment.status,
            method=payment.method,
            card_network=payment.card_network,
            card_type=payment.card_type,
            amount=payment.amount,
            fee=payment.fee,
            tax=payment.tax,
            settlement_id=assigned.get(payment.payment_id),
        )
        for payment in payments
    ]

    return settlements, rebuilt, adjustments, labels


# Only the fee with GST on it is taken off the merchant, so a comparison between fees uses the rate and then the GST on that, the same as the pricing 
# and never the rate on its own
def _fee_with_gst(amount: Paise, rate: Bps) -> Paise:
    mdr = apply_rate(amount, rate)
    return mdr + apply_rate(mdr, GST_BPS)


def _rounding_labels(
    group: list[Payment], settlement_id: str, agreed_rate_by_payment: dict[str, Bps]
) -> list[Label]:
    # The rate is taken from what the payment was priced at and not from the export, the export can say unknown for the card network and unknown has no rate
    by_rate: dict[Bps, list[Payment]] = {}
    for payment in group:
        by_rate.setdefault(agreed_rate_by_payment[payment.payment_id], []).append(payment)

    labels: list[Label] = []
    for rate, members in by_rate.items():
        if len(members) < 2:
            continue
        # Razorpay charges each payment its own fee, this works out what the fee would have come to on the total instead, only so the two can be compared
        per_payment = sum(_fee_with_gst(payment.amount, rate) for payment in members)
        on_total = _fee_with_gst(sum(payment.amount for payment in members), rate)
        if per_payment != on_total:
            labels.append(
                Label(
                    "ROUNDING_DRIFT",
                    settlement_id,
                    f"{len(members)} payments at {rate}bps drift {per_payment - on_total} paise",
                )
            )
    return labels


def _build_bank_lines(
    rng: random.Random, settlements: list[Settlement]
) -> tuple[list[BankLine], list[Label], list[tuple[int, str]]]:
    lines: list[BankLine] = []
    labels: list[Label] = []
    source: list[tuple[int, str]] = []
    real_utrs = {settlement.utr for settlement in settlements}

    for settlement in settlements:
        if settlement.status != "processed":
            continue

        narration, mangled = _narration(rng, settlement.utr, real_utrs)

        # The credit lands short of what the settlement said, so the matcher has to notice a credit that does not match and once the UTR is damaged too 
        # there is nothing left to find it by
        bank_fee = 0
        if rng.random() < BANK_FEE_RATE:
            bank_fee = rng.randint(500, 5_000)

        lines.append(
            BankLine(
                txn_date=settlement.settled_at.date(),
                narration=narration,
                credit=settlement.amount - bank_fee,
                debit=0,
            )
        )
        source.append((len(lines) - 1, settlement.settlement_id))
        if mangled:
            labels.append(Label("MANGLED_UTR", settlement.settlement_id, f"narration {narration}"))
        if bank_fee:
            labels.append(
                Label(
                    "BANK_FEE_DEDUCTED",
                    settlement.settlement_id,
                    f"bank kept {format_amount(bank_fee)} of the transfer",
                )
            )

        if rng.random() < UNKNOWN_CREDIT_RATE:
            # A stray credit can copy a failed settlement's amount but a failed settlement sends no money, so matching on the amount would 
            # report money that never left
            others = [
                other
                for other in settlements
                if other.settlement_id != settlement.settlement_id
            ]
            if others and rng.random() < HARD_NEGATIVE_RATE:
                copied = rng.choice(others)
                amount = copied.amount
                landed_on = copied.settled_at.date()
            else:
                amount = rng.randint(50_000, 500_000)
                landed_on = settlement.settled_at.date()
            stray = f"NEFT-HDFCN{landed_on:%y%m%d}{rng.randint(10000, 99999)}-VENDOR"
            lines.append(
                BankLine(txn_date=landed_on, narration=stray, credit=amount, debit=0)
            )
            labels.append(Label("UNKNOWN_CREDIT", stray, f"credit {format_amount(amount)}"))

    return lines, labels, source


# Amounts are written as rupee strings so every run loads its own data back through parse_amount instead of reading integers it just wrote
def write_csvs(dataset: Dataset, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)

    _write(
        directory / "orders.csv",
        ("order_ref", "order_id", "receipt", "placed_at", "amount", "status", "attempts"),
        (
            (
                o.order_ref,
                o.order_id or "",
                o.receipt or "",
                o.placed_at.isoformat(),
                format_amount(o.amount),
                o.status,
                str(o.attempts),
            )
            for o in dataset.orders
        ),
    )
    _write(
        directory / "payments.csv",
        (
            "payment_id",
            "order_id",
            "order_receipt",
            "happened_at",
            "status",
            "method",
            "card_network",
            "card_type",
            "amount",
            "fee",
            "tax",
            "settlement_id",
        ),
        (
            (
                p.payment_id,
                p.order_id,
                p.order_receipt or "",
                p.happened_at.isoformat(),
                p.status,
                p.method,
                p.card_network or "",
                p.card_type or "",
                format_amount(p.amount),
                format_amount(p.fee),
                format_amount(p.tax),
                p.settlement_id or "",
            )
            for p in dataset.payments
        ),
    )
    _write(
        directory / "settlements.csv",
        ("settlement_id", "utr", "settled_at", "amount", "fees", "tax", "status"),
        (
            (
                s.settlement_id,
                s.utr,
                s.settled_at.isoformat(),
                format_amount(s.amount),
                format_amount(s.fees),
                format_amount(s.tax),
                s.status,
            )
            for s in dataset.settlements
        ),
    )
    _write(
        directory / "bank_statement.csv",
        ("txn_date", "narration", "credit", "debit"),
        (
            (b.txn_date.isoformat(), b.narration, format_amount(b.credit), format_amount(b.debit))
            for b in dataset.bank_lines
        ),
    )
    _write(
        directory / "adjustments.csv",
        ("adjustment_id", "kind", "payment_id", "amount", "raised_at", "settlement_id"),
        (
            (
                a.adjustment_id,
                a.kind,
                a.payment_id,
                format_amount(a.amount),
                a.raised_at.isoformat(),
                a.settlement_id or "",
            )
            for a in dataset.adjustments
        ),
    )


def _write(path: Path, header: tuple[str, ...], rows) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
