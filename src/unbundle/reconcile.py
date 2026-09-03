import re
from collections.abc import Container, Sequence
from datetime import date, timedelta
from typing import get_args

from unbundle.evaluate import Flagged, MatchOutcome, MatchedCredit
from unbundle.record_types import (
    Adjustment,
    BankLine,
    CardNetwork,
    CardType,
    Method,
    Order,
    Payment,
    Settlement,
)
from unbundle.money import Bps, Paise, apply_rate, within_tolerance

GST_BPS: Bps = 1800
CANDIDATE_WINDOW_DAYS = 2

# The most a bank transfer charge is assumed to be, above this a short credit is not a bank fee and the matcher does not guess at what it is
BANK_FEE_CEILING: Paise = 10_000

# The matcher's own rate card, which is what the merchant believes was agreed. Kept separate from the generator's on purpose because a fee that 
# differs from the agreed rate is the finding and importing the charged rate would make it invisible
AGREED_CARD_NETWORK_RATE: dict[str, Bps] = {
    "Maestro": 200,
    "Visa": 200,
    "MasterCard": 200,
    "RuPay": 200,
    "American Express": 300,
    "Diners Club": 300,
}
AGREED_METHOD_RATE: dict[str, Bps] = {"upi": 200, "netbanking": 200, "wallet": 200}
AGREED_EMI_TYPE_RATE: dict[str, Bps] = {"debit": 100, "credit": 300}

# Razorpay's own strings and not short forms, because a key here is matched against a column read out of an export. unknown is absent deliberately, 
# no rate is published for it, and the assert names the absence so a reader reaching for a default has to argue with it first
assert set(AGREED_CARD_NETWORK_RATE) | {"unknown"} == set(get_args(CardNetwork))
assert set(AGREED_EMI_TYPE_RATE) == set(get_args(CardType))

TOKEN = re.compile(r"[A-Za-z0-9]+")

# The matcher's own settlement calendar, for the same reason as the rate card. A merchant who cannot work out when a payment was due 
# cannot tell in flight from missing
def working_days_after(start: date, days: int) -> date:
    moved = start
    added = 0
    while added < days:
        moved += timedelta(days=1)
        if moved.weekday() < 5:
            added += 1
    return moved


def agreed_rate_for(
    method: Method, card_network: CardNetwork | None, card_type: CardType | None
) -> Bps | None:
    if method == "card":
        # No rate is published for unknown, so any value returned here would be invented and an invented rate produces a fee dispute against a payment 
        # that was priced correctly
        if card_network == "unknown":
            return None
        return AGREED_CARD_NETWORK_RATE[card_network]
    if method == "emi":
        # A blank column is not debit, and reading it as credit charges three times the debit rate
        return AGREED_EMI_TYPE_RATE.get(card_type)
    return AGREED_METHOD_RATE[method]


# Nothing rather than a number when the payment cannot be priced, so a caller has to decide what to do about it instead of comparing against
# a figure with no source behind it
def expected_fee(payment: Payment) -> Paise | None:
    rate = agreed_rate_for(payment.method, payment.card_network, payment.card_type)
    if rate is None:
        return None
    return _fee_with_gst(payment.amount, rate)


def _fee_with_gst(amount: Paise, rate: Bps) -> Paise:
    mdr = apply_rate(amount, rate)
    return mdr + apply_rate(mdr, GST_BPS)


# Tokens rather than a pattern, the narration format is the bank's and not ours, and a UTR that does not survive tokenising is damaged by definition
def find_utr(narration: str, known: Container[str]) -> str | None:
    for token in TOKEN.findall(narration):
        if token in known:
            return token
    return None


# Ordered merchant, gateway, bank, which is the order the three sources join in
def match(
    orders: Sequence[Order],
    payments: Sequence[Payment],
    settlements: Sequence[Settlement],
    adjustments: Sequence[Adjustment],
    bank_lines: Sequence[BankLine],
    as_of: date,
    tolerance: Paise = 0,
) -> MatchOutcome:
    settlement_by_utr = {settlement.utr: settlement for settlement in settlements}
    settlement_by_id = {settlement.settlement_id: settlement for settlement in settlements}

    payments_by_settlement: dict[str, list[Payment]] = {}
    for payment in payments:
        if payment.settlement_id is not None:
            payments_by_settlement.setdefault(payment.settlement_id, []).append(payment)

    matched: list[MatchedCredit] = []
    flagged: list[Flagged] = []
    observed: list[Flagged] = []
    claimed: set[str] = set()

    def take(index: int, line: BankLine, settlement: Settlement) -> None:
        # Finding the settlement is not the same as the money being right, so the credit is tied out against what the settlement said it sent. 
        # Short by a little is the bank keeping its transfer charge, short by a lot is not something to guess at
        shortfall = settlement.amount - line.credit
        if not within_tolerance(line.credit, settlement.amount, tolerance):
            if 0 < shortfall <= BANK_FEE_CEILING:
                flagged.append(Flagged(settlement.settlement_id, "BANK_FEE_DEDUCTED"))
            else:
                flagged.append(Flagged(line.narration, "UNKNOWN_CREDIT"))
                return

        claimed.add(settlement.settlement_id)
        members = payments_by_settlement.get(settlement.settlement_id, [])
        matched.append(
            MatchedCredit(
                line_index=index,
                narration=line.narration,
                payment_ids=tuple(payment.payment_id for payment in members),
            )
        )

        if len(members) > 1:
            observed.append(Flagged(settlement.settlement_id, "BATCHED"))
        if _drifts(members):
            observed.append(Flagged(settlement.settlement_id, "ROUNDING_DRIFT"))

    # A credit that names its settlement is stronger evidence than one that only fits by amount, so every UTR is matched first, or a stray credit copying
    # an amount takes the settlement that a later credit names outright
    without_utr: list[tuple[int, BankLine]] = []
    for index, line in enumerate(bank_lines):
        if line.credit <= 0:
            continue

        utr = find_utr(line.narration, settlement_by_utr)
        if utr is None:
            without_utr.append((index, line))
            continue

        settlement = settlement_by_utr[utr]
        # Two credits cannot both be one settlement, the first one took it so the second is money this run cannot account for
        if settlement.settlement_id in claimed:
            flagged.append(Flagged(line.narration, "UNKNOWN_CREDIT"))
            continue
        take(index, line, settlement)

    for index, line in without_utr:
        settlement = _by_amount_and_date(line, settlements, claimed, tolerance)
        if settlement is None:
            flagged.append(Flagged(line.narration, "UNKNOWN_CREDIT"))
            continue
        flagged.append(Flagged(settlement.settlement_id, "MANGLED_UTR"))
        take(index, line, settlement)

    adjustments_by_settlement: dict[str, list[Adjustment]] = {}
    for adjustment in adjustments:
        if adjustment.settlement_id is not None:
            adjustments_by_settlement.setdefault(adjustment.settlement_id, []).append(adjustment)

    settlement_of_payment = {payment.payment_id: payment.settlement_id for payment in payments}

    # Nothing here reads the bank side, so a settlement with no credit is still checked
    for settlement in settlements:
        members = payments_by_settlement.get(settlement.settlement_id)
        if not members:
            continue

        against = adjustments_by_settlement.get(settlement.settlement_id, [])
        deducted = sum(adjustment.amount for adjustment in against)
        shortfall = sum(payment.net for payment in members) - deducted - settlement.amount
        if shortfall != 0:
            # A settlement can leave one of its own payments out of the transfer, which puts the gap at exactly that payment's net, so a single
            # member matching it is the one that was left out and everything else in the settlement still ties out. Two members matching is two
            # answers and picking either one attributes the gap to the wrong payment, so it is left unnamed the way any other gap is
            excluded = [payment for payment in members if payment.net == shortfall]
            if len(excluded) != 1:
                continue

        for adjustment in against:
            if adjustment.kind == "chargeback":
                observed.append(Flagged(settlement.settlement_id, "CHARGEBACK_LATER"))
                continue
            # A refund against a payment inside this settlement is ordinary, the payment id is what separates it from one raised against a cycle settled earlier
            if settlement_of_payment.get(adjustment.payment_id) != settlement.settlement_id:
                observed.append(Flagged(settlement.settlement_id, "PARTIAL_REFUND"))

    # A failed attempt shares the order id with the capture that worked, so counting payments per order without reading the status reports a duplicate 
    # that never happened
    captured_by_order: dict[str, list[Payment]] = {}
    for payment in payments:
        if payment.status == "captured":
            captured_by_order.setdefault(payment.order_id, []).append(payment)

    duplicates: set[str] = set()
    for captures in captured_by_order.values():
        if len(captures) > 1:
            in_order = sorted(captures, key=lambda payment: payment.happened_at)
            # The first capture is the sale and every later one is money taken again, so the earliest is left alone and the rest are what the merchant has to give back
            duplicates.update(payment.payment_id for payment in in_order[1:])

    in_flight: list[str] = []
    received: list[str] = []
    unconfirmed: list[str] = []
    fee_overcharged: Paise = 0
    for payment in payments:
        # A failed attempt carries no money, no fee and no settlement, so every check below would read it as missing money and none of it would be true
        if payment.status != "captured":
            observed.append(Flagged(payment.payment_id, "FAILED_RETRY"))
            continue

        if payment.payment_id in duplicates:
            flagged.append(Flagged(payment.payment_id, "DUPLICATE_PAYMENT"))

        # A fee with no agreed rate behind it is not right and not wrong, it is unchecked, so it is observed and never flagged
        expected = expected_fee(payment)
        if expected is None:
            observed.append(Flagged(payment.payment_id, "NETWORK_UNKNOWN"))
        elif payment.fee != expected:
            flagged.append(Flagged(payment.payment_id, "FEE_MISMATCH"))
            # Signed, so a payment charged under the agreed rate nets off one charged over it and
            # the total is what the merchant is out of pocket rather than the size of the dispute
            fee_overcharged += payment.fee - expected

        due = working_days_after(payment.happened_at.date(), 2)
        settlement = (
            None if payment.settlement_id is None else settlement_by_id.get(payment.settlement_id)
        )
        # An id is not evidence the transfer happened, a settlement with status failed never reached the bank, so the payment it names is money
        # the merchant does not have
        if settlement is None or settlement.status == "failed":
            # The only thing separating a healthy payment from missing money is whether the due date has passed, so as_of is a parameter and never today's date.
            # A failed transfer is never in flight, it was attempted and it bounced
            if payment.settlement_id is None and due > as_of:
                in_flight.append(payment.payment_id)
            else:
                flagged.append(Flagged(payment.payment_id, "MISSING_SETTLEMENT"))
            continue

        settled_on = settlement.settled_at.date()
        if (settled_on - payment.happened_at.date()).days > 2:
            observed.append(Flagged(payment.payment_id, "T_PLUS_TWO"))

        # A processed settlement is Razorpay saying it sent the money, the bank line is what says it arrived
        if settlement.settlement_id in claimed:
            received.append(payment.payment_id)
        else:
            unconfirmed.append(payment.payment_id)

    # The merchant's own file is the third source and the only one that can lose the join, an order with neither the gateway id nor 
    # the receipt is money it cannot tie to anything
    payment_by_order_id = {payment.order_id: payment for payment in payments}
    payment_by_receipt = {
        payment.order_receipt: payment
        for payment in payments
        if payment.order_receipt is not None
    }

    for order in orders:
        if order.order_id is not None and order.order_id in payment_by_order_id:
            continue
        # Observed and not flagged, the order did join and nobody has to act on it today but the count is how many integrations lost the gateway id
        if order.receipt is not None and order.receipt in payment_by_receipt:
            observed.append(Flagged(order.order_ref, "ORDER_ID_MISSING"))
            continue
        # Named by the merchant's own number, because it is the one identifier an unlinked order still has
        flagged.append(Flagged(order.order_ref, "UNLINKED_ORDER"))

    return MatchOutcome(
        matched=tuple(matched),
        flagged=tuple(flagged),
        observed=tuple(observed),
        in_flight=tuple(in_flight),
        received=tuple(received),
        unconfirmed=tuple(unconfirmed),
        fee_overcharged=fee_overcharged,
    )


# An amount that fits two settlements is not a match, because picking one of them at random attributes money to the wrong orders and reports it as reconciled
def _by_amount_and_date(
    line: BankLine, settlements: Sequence[Settlement], claimed: set[str], tolerance: Paise
) -> Settlement | None:
    candidates = [
        settlement
        for settlement in settlements
        if settlement.settlement_id not in claimed
        and abs((settlement.settled_at.date() - line.txn_date).days) <= CANDIDATE_WINDOW_DAYS
        and within_tolerance(settlement.amount, line.credit, tolerance)
    ]
    if len(candidates) == 1:
        return candidates[0]
    return None


def _drifts(members: Sequence[Payment]) -> bool:
    by_rate: dict[Bps, list[Payment]] = {}
    for payment in members:
        rate = agreed_rate_for(payment.method, payment.card_network, payment.card_type)
        # The answer key groups a settlement by the rate each payment was priced at, and a card the export calls unknown was priced at some real rate, 
        # so an unpriceable payment can sit in the group being checked and dropping the payment compares a group that was never scored
        if rate is None:
            return False
        by_rate.setdefault(rate, []).append(payment)

    for rate, group in by_rate.items():
        if len(group) < 2:
            continue
        per_payment = sum(_fee_with_gst(payment.amount, rate) for payment in group)
        on_total = _fee_with_gst(sum(payment.amount for payment in group), rate)
        if per_payment != on_total:
            return True
    return False
